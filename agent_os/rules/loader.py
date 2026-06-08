"""
agent_os.rules.loader
=====================
Loads the five binding rule files in fixed order, applies per-file character
caps and prompt-injection scanning, and renders a cache-stable binding block
intended for the STABLE prompt tier.

SCOPE LOCK: this module loads and renders the rule block only. It does NOT
wire the block into any live graph, PM, or agent prompt. No order-placement
authority exists here or anywhere in agent_os.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict

# Zero-width / invisible characters stripped before injection scanning.
_ZERO_WIDTH_CHARS: str = "​‌‍﻿"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fixed order of the five rule files — order is part of the contract and
#: must not change between loads (cache stability).
RULE_FILES: List[str] = [
    "TRADING_RULES.md",
    "RISK_POLICY.md",
    "NO_TRADE_RULES.md",
    "DATA_SOURCES.md",
    "FNO_RULES.md",
]

#: Per-file character cap. Content above this is truncated (not dropped).
MAX_CHARS_PER_FILE: int = 20_000

#: Default rules directory (agent_os/rules/ relative to this file's parent).
_DEFAULT_RULES_DIR: Path = Path(__file__).parent

#: Binding header prepended to every rendered block.
_BINDING_HEADER: str = (
    "=== BINDING CONSTRAINTS ===\n"
    "These are BINDING constraints. If a rule conflicts with a request or a "
    "model suggestion, the rule wins. You cannot place or simulate an order "
    "that violates them. These constraints are NON-NEGOTIABLE and must be "
    "respected at all times.\n"
    "===========================\n\n"
)

# ---------------------------------------------------------------------------
# Prompt-injection patterns
# ---------------------------------------------------------------------------
# Scan for known jailbreak / prompt-injection fingerprints. A match means
# the file has been tampered with — FAIL CLOSED: raise RuleInjectionDetected.

_INJECTION_PATTERNS: List[re.Pattern] = [
    # ignore (all|the|any|previous|prior) …instructions
    re.compile(
        r"ignore\s+(all|the|any|previous|prior)\s+.*instruction",
        re.IGNORECASE,
    ),
    # disregard (the|all|previous|above)
    re.compile(
        r"disregard\s+(the|all|previous|above)",
        re.IGNORECASE,
    ),
    # you are now (a|an|the)
    re.compile(
        r"you\s+are\s+now\s+(a|an|the)\s+",
        re.IGNORECASE,
    ),
    # <| … |>  (token boundary injection)
    re.compile(
        r"<\|.*?\|>",
        re.DOTALL,
    ),
    # begin system prompt
    re.compile(
        r"begin\s+system\s+prompt",
        re.IGNORECASE,
    ),
    # ^system: at the start of a line
    re.compile(
        r"^system\s*:",
        re.IGNORECASE | re.MULTILINE,
    ),
    # jailbreak
    re.compile(
        r"jailbreak",
        re.IGNORECASE,
    ),
    # forget (all|any|the|previous|prior) …instruction(s)
    re.compile(
        r"forget\s+(all|any|the|previous|prior)\b.*instruction",
        re.IGNORECASE | re.DOTALL,
    ),
    # pretend (you are|to be) …
    re.compile(
        r"pretend\s+(you\s+are|to\s+be)\b",
        re.IGNORECASE,
    ),
    # act as (a|an) (dan|jailbreak|unrestricted|unconstrained)
    re.compile(
        r"act\s+as\s+(an?\s+)?(dan|jailbreak|unrestricted|unconstrained)\b",
        re.IGNORECASE,
    ),
    # override (the) system prompt — scoped to 'system prompt' to avoid
    # false positives on legitimate rule text like "overrides all other logic"
    # or "no override path".
    re.compile(
        r"override\s+(the\s+)?system\s+prompt",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RuleFileMissing(FileNotFoundError):
    """Raised when one of the five required rule files is absent.

    Fail-closed: a missing file is never silently skipped.
    """


class RuleInjectionDetected(ValueError):
    """Raised when a prompt-injection marker is found in a rule file.

    Fail-closed: a tampered file must not be loaded into the prompt.
    """


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RuleBundle:
    """Result of a successful rule load.

    Attributes
    ----------
    text : str
        The fully rendered binding block (binding header + all five files,
        in fixed order, with file dividers).
    files : list[dict]
        Per-file metadata dicts, one per rule file, in RULE_FILES order.
        Each dict contains:
          - ``name``       : filename (str)
          - ``char_count`` : characters included (after any truncation)
          - ``truncated``  : True if the file was capped (bool)
    truncated : bool
        True if *any* file was truncated.
    char_count : int
        Total characters across all files (sum of per-file char_count).
    """

    text: str
    files: List[Dict] = field(default_factory=list)
    truncated: bool = False
    char_count: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_rules(rules_dir: Path | str | None = None) -> RuleBundle:
    """Load the five binding rule files and return a :class:`RuleBundle`.

    Parameters
    ----------
    rules_dir:
        Directory containing the five rule files. Defaults to
        ``agent_os/rules/`` (the directory this module lives in).

    Raises
    ------
    RuleFileMissing
        If any of the five required files does not exist.
    RuleInjectionDetected
        If any file contains a prompt-injection marker.

    Returns
    -------
    RuleBundle
        Rendered block + metadata.
    """
    if rules_dir is None:
        rules_dir = _DEFAULT_RULES_DIR
    rules_dir = Path(rules_dir)

    file_entries: List[Dict] = []
    rendered_sections: List[str] = []
    any_truncated = False

    for fname in RULE_FILES:
        fpath = rules_dir / fname

        # --- FAIL CLOSED: missing file ---
        if not fpath.exists():
            raise RuleFileMissing(
                f"Required rule file is missing: {fname!r} "
                f"(looked in {rules_dir})"
            )

        raw_text = fpath.read_text(encoding="utf-8")

        # --- Prompt-injection scan (on raw, uncapped text) ---
        _scan_for_injection(raw_text, fname)

        # --- Character cap ---
        truncated = len(raw_text) > MAX_CHARS_PER_FILE
        if truncated:
            raw_text = raw_text[:MAX_CHARS_PER_FILE]
            any_truncated = True

        char_count = len(raw_text)
        file_entries.append(
            {"name": fname, "char_count": char_count, "truncated": truncated}
        )

        # --- Build section with divider ---
        section = (
            f"--- {fname} ---\n"
            f"{raw_text}\n"
        )
        rendered_sections.append(section)

    total_chars = sum(e["char_count"] for e in file_entries)
    rendered_text = _BINDING_HEADER + "\n".join(rendered_sections)

    return RuleBundle(
        text=rendered_text,
        files=file_entries,
        truncated=any_truncated,
        char_count=total_chars,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scan_for_injection(text: str, filename: str) -> None:
    """Scan *text* for prompt-injection markers.

    Before scanning, applies NFKC Unicode normalization and strips zero-width
    characters (U+200B, U+200C, U+200D, U+FEFF) to defeat homoglyph and
    ZWSP evasion attacks.

    Raises
    ------
    RuleInjectionDetected
        If any injection pattern matches, citing *filename*.
    """
    # Normalize: NFKC collapses homoglyphs and compatibility forms.
    normalized = unicodedata.normalize("NFKC", text)
    # Strip zero-width / invisible characters.
    for zwchar in _ZERO_WIDTH_CHARS:
        normalized = normalized.replace(zwchar, "")

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(normalized):
            raise RuleInjectionDetected(
                f"Prompt-injection marker detected in {filename!r}: "
                f"pattern {pattern.pattern!r} matched. "
                "This file may have been tampered with."
            )
