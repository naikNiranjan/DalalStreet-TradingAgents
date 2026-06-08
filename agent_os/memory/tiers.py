"""Episodic memory tier store for the agent_os layer.

SIDECAR INVARIANT: These files are sidecar JSONL files stored alongside (but
completely separate from) the base-repo TradingMemoryLog markdown grammar.
They NEVER touch trading_memory.md or its format.

Only episodic tier is POPULATED in this slice.
"""

from __future__ import annotations

import json
import os
from typing import Any

from tradingagents.agents.utils.rating import RATINGS_5_TIER, parse_rating  # noqa: F401 — re-export

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The minimal truncation envelope {"__truncated__": true, "__raw__": ""} serializes
# to 38 chars. We require at least 64 so the while-loop in append() can converge
# (the envelope shrinks by halving __raw__ on each iteration, and 64 gives enough
# headroom for the fixed envelope overhead of ~38 chars).
_MIN_ENTRY_CHARS: int = 64

# Keys that must SURVIVE truncation so every stored entry still cites its source.
# (Reviewer finding 2: a truncated entry that dropped source/session_date/book would
# violate "every memory entry cites its source".)
_DEFAULT_PROVENANCE_KEYS: tuple = ("source", "session_date", "book")


# ---------------------------------------------------------------------------
# TIERS registry: only episodic is populated this slice
# ---------------------------------------------------------------------------

TIERS: dict[str, Any] = {
    "short_term": None,
    "episodic": {
        "max_entries": 500,
        "max_chars_per_entry": 4096,
        "description": "Per-session book outcomes derived from audit log + session report.",
    },
    "long_term": None,
    "regime": None,
    "strategy": None,
}


# ---------------------------------------------------------------------------
# TierStore
# ---------------------------------------------------------------------------

class TierStore:
    """File-backed JSONL store for one memory tier.

    Parameters
    ----------
    base_dir:
        Directory under which ``<tier_name>.jsonl`` is stored.
    tier_name:
        Name of the tier (e.g. "episodic"). The file will be
        ``<base_dir>/<tier_name>.jsonl``.
    max_entries:
        Maximum number of entries to retain. When exceeded the OLDEST
        entries are trimmed so the store stays bounded.
    max_chars_per_entry:
        Hard cap on the serialized length (UTF-8 JSON) of any single entry.
        Entries larger than this are truncated to fit within the cap before
        storage — but the provenance keys (see ``provenance_keys``) are ALWAYS
        preserved so a truncated entry still cites its source.
    provenance_keys:
        Top-level keys that must survive truncation (default
        ``("source", "session_date", "book")``). If an entry cannot be stored
        within ``max_chars_per_entry`` while keeping these keys, ``append`` raises
        rather than silently dropping the source citation.
    """

    def __init__(
        self,
        base_dir: str,
        tier_name: str,
        *,
        max_entries: int,
        max_chars_per_entry: int,
        provenance_keys: tuple = _DEFAULT_PROVENANCE_KEYS,
    ) -> None:
        if max_chars_per_entry < _MIN_ENTRY_CHARS:
            raise ValueError(
                f"max_chars_per_entry={max_chars_per_entry} is below the safe minimum "
                f"_MIN_ENTRY_CHARS={_MIN_ENTRY_CHARS}. The truncation envelope requires "
                f"at least {_MIN_ENTRY_CHARS} chars to converge without infinite-looping."
            )
        self._base_dir = base_dir
        self._tier_name = tier_name
        self._max_entries = max_entries
        self._max_chars_per_entry = max_chars_per_entry
        self._provenance_keys = tuple(provenance_keys)
        os.makedirs(base_dir, exist_ok=True)

    @property
    def path(self) -> str:
        return os.path.join(self._base_dir, f"{self._tier_name}.jsonl")

    def append(self, entry: dict) -> None:
        """Append one entry to the tier, enforcing char cap and entry bound.

        The entry is serialized to JSON. If it exceeds ``max_chars_per_entry`` it is
        stored as a truncation envelope that PRESERVES the provenance keys
        (``self._provenance_keys`` — source/session_date/book by default) and carries
        the overflow in ``__raw__``. This guarantees every stored entry still cites its
        source. If the entry cannot fit even with an empty ``__raw__`` (i.e. the
        provenance alone exceeds the cap), ``append`` RAISES rather than dropping the
        citation. After appending, if the store exceeds ``max_entries`` the oldest
        entries are discarded.
        """
        serialized = json.dumps(entry, ensure_ascii=True, sort_keys=True)
        if len(serialized) > self._max_chars_per_entry:
            # Preserve provenance so the truncated entry still cites its source.
            prov = {k: entry[k] for k in self._provenance_keys if k in entry}

            def _envelope(raw: str) -> str:
                env: dict[str, Any] = {"__truncated__": True, **prov, "__raw__": raw}
                return json.dumps(env, ensure_ascii=True, sort_keys=True)

            # Start with as much overflow as the cap could hold, then shrink __raw__
            # (never the provenance) until it fits.
            truncated_raw = serialized[: self._max_chars_per_entry]
            serialized = _envelope(truncated_raw)
            while len(serialized) > self._max_chars_per_entry and truncated_raw:
                truncated_raw = truncated_raw[: len(truncated_raw) // 2]
                serialized = _envelope(truncated_raw)
            if len(serialized) > self._max_chars_per_entry:
                # Even with empty __raw__ the provenance envelope doesn't fit — fail
                # closed rather than store an entry that cannot cite its source.
                raise ValueError(
                    f"Cannot store entry within max_chars_per_entry="
                    f"{self._max_chars_per_entry} while preserving provenance keys "
                    f"{sorted(prov)}. Increase the cap for tier '{self._tier_name}'."
                )

        os.makedirs(self._base_dir, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(serialized + "\n")

        self._trim_to_max_entries()

    def read_all(self) -> list[dict]:
        """Return all stored entries as a list of dicts (oldest first)."""
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Skip corrupt lines (fail-safe read, fail-closed write)
                        continue
        return entries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _trim_to_max_entries(self) -> None:
        """Keep only the most recent max_entries entries."""
        entries = self.read_all()
        if len(entries) <= self._max_entries:
            return
        # Keep newest max_entries (tail), drop oldest (head)
        to_keep = entries[-self._max_entries :]
        with open(self.path, "w", encoding="utf-8") as fh:
            for entry in to_keep:
                fh.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")
