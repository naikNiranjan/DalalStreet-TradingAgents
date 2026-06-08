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

import math
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

# ---------------------------------------------------------------------------
# Digest constants and helpers
# ---------------------------------------------------------------------------

#: Maximum token budget for the derived binding-rules digest. If a future rule
#: edit pushes the derived digest above this, the test suite will go red and a
#: human decides whether to relax the budget (see doc 16 D2).
DIGEST_TOKEN_BUDGET: int = 800


def estimate_tokens(text: str) -> int:
    """Deterministic token proxy: ceil(len(text) / 4).

    Why this formula: no tiktoken dependency in the stack. The plan doc (16)
    measured the full five-file block as 16,146 chars ≈ 4,036 tokens, i.e.
    approximately 4 chars/token. This proxy matches that ratio and adds zero
    dependencies — safe to call from within the agent_os layer without pulling
    in the tiktoken wheel.

    Note: 4 chars/token is a generous LOWER bound for terse/acronym/number-dense
    content (e.g. rule IDs, tickers, numeric caps). The actual tokenization of
    compact rule text can be denser (more tokens per char), so the real cost may
    exceed this estimate. The 800-token budget has deliberate headroom; the
    measured digest is ~312 tokens (~1248 chars) well within the budget.
    """
    return math.ceil(len(text) / 4)

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


class RuleDigestError(ValueError):
    """Raised when render_digest cannot produce a complete, verified digest.

    Fail-closed: a tampered, reformatted, or partially-parsed rule file must
    never yield a silent partial or empty digest. If the parser cannot extract
    a non-empty set of NO_TRADE hard stops AND a non-empty set of risk-cap
    bullets from their allowlisted sections, the digest is refused entirely.
    This ensures a missing bold lead (non-bold item, indented item, reformatted
    item) is a loud failure rather than a silently-dropped rule.
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


@dataclass
class RuleDigest:
    """A compact, derived binding digest suitable for prompt injection.

    Derived by PARSING the two source files (NO_TRADE_RULES.md and
    RISK_POLICY.md); a rule-file edit automatically changes the digest because
    no text is hardcoded here. The digest is alignment-only — it does NOT
    enforce anything; the deterministic risk-gate chain in execution/ is the
    only thing that can stop an order.

    Attributes
    ----------
    text : str
        The fully rendered compact digest (header + hard stops + risk caps).
    token_estimate : int
        Estimated token count via ``estimate_tokens(text)`` (4 chars/token).
    char_count : int
        Character count — equal to ``len(text)``.
    no_trade_count : int
        Number of NO_TRADE hard stops parsed from NO_TRADE_RULES.md.
    caps_count : int
        Number of risk cap bullets parsed from RISK_POLICY.md.
    """

    text: str
    token_estimate: int
    char_count: int
    no_trade_count: int
    caps_count: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _read_scan_cap(rules_dir: Path, fname: str) -> tuple[str, bool]:
    """Read *fname* from *rules_dir*, scan for injection, apply the MAX_CHARS cap.

    Returns (text, truncated) where *text* is the (possibly truncated) content
    and *truncated* is True if the file was capped.

    Raises
    ------
    RuleFileMissing
        If the file does not exist.
    RuleInjectionDetected
        If the file contains a prompt-injection marker.

    Implementation note: the scan is applied to the raw (uncapped) text before
    truncation, matching the order used by load_rules (scan raw → then cap).
    This ensures injection markers are never hidden by truncation.
    """
    fpath = rules_dir / fname
    if not fpath.exists():
        raise RuleFileMissing(
            f"Required rule file is missing: {fname!r} "
            f"(looked in {rules_dir})"
        )
    raw = fpath.read_text(encoding="utf-8")
    _scan_for_injection(raw, fname)
    truncated = len(raw) > MAX_CHARS_PER_FILE
    if truncated:
        raw = raw[:MAX_CHARS_PER_FILE]
    return raw, truncated


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


def render_digest(rules_dir: Path | str | None = None) -> RuleDigest:
    """Derive a compact binding-rules digest by parsing the two source files.

    The digest is derived (not hardcoded): a rule-file edit automatically
    propagates into the digest text. It is intentionally much smaller than the
    full five-file block returned by :func:`load_rules` — bounded to
    ``DIGEST_TOKEN_BUDGET`` tokens by construction.

    Alignment-only contract
    -----------------------
    This digest is alignment guidance for the Portfolio Manager prompt. It does
    NOT enforce anything. The deterministic risk-gate chain in ``execution/`` is
    the final backstop and the only thing that can stop an order.

    Fail-closed parsing
    -------------------
    After parsing, raises :class:`RuleDigestError` if:
      - ``no_trade_leads`` is empty (file empty, mis-formatted, or all bold
        leads stripped — indicates tampering or reformatting).
      - A numbered NO_TRADE item exists whose bold lead was not captured (item
        count != lead count), meaning a rule would be silently dropped.
      - ``caps_leads`` within the allowlisted gate-enforcing sections is empty.
      - A scoped bullet in those sections has no bold lead (count mismatch).

    Caps scoping
    ------------
    Caps are parsed ONLY from the three gate-enforcing sections of RISK_POLICY.md:
      - "Hard caps (enforced by gates)"
      - "Liquidity and data-quality guards"
      - "Settlement discipline"
    The "Audit and verifiability" section is excluded to avoid mislabelling its
    "Audit coverage target is 100%" bullet as a gate cap (it is not gate-enforced).

    Parameters
    ----------
    rules_dir:
        Directory containing the rule files. Defaults to ``agent_os/rules/``.

    Raises
    ------
    RuleFileMissing
        If ``NO_TRADE_RULES.md`` or ``RISK_POLICY.md`` is absent (fail-closed).
    RuleInjectionDetected
        If either file contains a prompt-injection marker (fail-closed).
    RuleDigestError
        If the parsed digest is empty or has a count mismatch (fail-closed).

    Returns
    -------
    RuleDigest
        Compact rendered digest + metadata.
    """
    if rules_dir is None:
        rules_dir = _DEFAULT_RULES_DIR
    rules_dir = Path(rules_dir)

    # --- Load and scan the two source files via the shared helper (FIX-C) ---
    # _read_scan_cap: exists-check -> RuleFileMissing; read; injection scan;
    # then cap to MAX_CHARS_PER_FILE — same order as load_rules.
    no_trade_raw, _ = _read_scan_cap(rules_dir, "NO_TRADE_RULES.md")
    risk_policy_raw, _ = _read_scan_cap(rules_dir, "RISK_POLICY.md")

    # --- Parse NO_TRADE_RULES.md: numbered hard stops (FIX-B tolerant regex) ---
    #
    # Tolerant item regex: match top-level numbered items, allowing optional
    # leading whitespace.  ^\s*\d+\.\s  counts all hard stops.
    no_trade_item_pattern = re.compile(
        r"^\s*\d+\.\s",
        re.MULTILINE,
    )
    # Tolerant lead regex: match the bold lead within a numbered item,
    # allowing optional leading whitespace.  ^\s*\d+\.\s+\*\*([^*]+?)\*\*
    no_trade_lead_pattern = re.compile(
        r"^\s*\d+\.\s+\*\*([^*]+?)\*\*",
        re.MULTILINE,
    )
    no_trade_items = no_trade_item_pattern.findall(no_trade_raw)
    no_trade_leads = no_trade_lead_pattern.findall(no_trade_raw)

    # FIX-B symmetric count invariant: every numbered item must have a bold lead.
    if len(no_trade_items) != len(no_trade_leads):
        raise RuleDigestError(
            f"NO_TRADE_RULES.md count mismatch: found {len(no_trade_items)} "
            f"numbered items but only {len(no_trade_leads)} bold leads. "
            f"A numbered hard stop has no bold lead — this indicates tampering "
            f"or reformatting. Fix the file or raise the budget. "
            f"Mismatch delta: {len(no_trade_items) - len(no_trade_leads)} item(s) "
            f"without a captured lead."
        )

    # FIX-A fail-closed: empty set is never acceptable.
    if not no_trade_leads:
        raise RuleDigestError(
            "NO_TRADE_RULES.md yielded zero bold leads after parsing — the file "
            "may be empty, tampered with, or all bold formatting has been stripped. "
            "A digest with no hard stops is refused (fail-closed)."
        )

    # --- Parse RISK_POLICY.md: cap/guard bullets scoped to allowlisted sections ---
    # (FIX-D) Only parse from these three gate-enforcing sections:
    _CAPS_ALLOWLISTED_SECTIONS = frozenset({
        "Hard caps (enforced by gates)",
        "Liquidity and data-quality guards",
        "Settlement discipline",
    })

    # Split RISK_POLICY.md on ## headers and collect bodies for allowlisted sections.
    # FIX-J: _extract_scoped_section_body now raises RuleDigestError if any allowlisted
    # section title is absent from the file (fail-closed on header rename/removal).
    scoped_body = _extract_scoped_section_body(
        risk_policy_raw, _CAPS_ALLOWLISTED_SECTIONS
    )

    # FIX-K: Anchor patterns to TOP-LEVEL bullets only (no leading whitespace).
    # Old patterns used ^\s*[-*]\s which matched indented sub-bullets, causing
    # spurious RuleDigestError on legitimate '  - some note' sub-bullets.
    # New patterns use ^[-*]\s — zero leading whitespace — so indented sub-bullets
    # are invisible to BOTH item count and lead count (no mismatch, no spurious raise).
    # A top-level non-bold bullet (^[-*]\s without **) still triggers the count
    # mismatch and raises correctly.
    caps_item_pattern = re.compile(
        r"^[-*]\s",
        re.MULTILINE,
    )
    # ^[-*]\s+\*\*([^*]+?)\*\*  extracts bold leads from top-level bullets only.
    caps_lead_pattern = re.compile(
        r"^[-*]\s+\*\*([^*]+?)\*\*",
        re.MULTILINE,
    )
    caps_items = caps_item_pattern.findall(scoped_body)
    caps_leads = caps_lead_pattern.findall(scoped_body)

    # FIX-B symmetric count invariant for caps: every scoped bullet must have a bold lead.
    if len(caps_items) != len(caps_leads):
        raise RuleDigestError(
            f"RISK_POLICY.md scoped caps count mismatch: found {len(caps_items)} "
            f"bullets in gate-enforcing sections but only {len(caps_leads)} bold leads. "
            f"A cap bullet has no bold lead — indicates tampering or reformatting. "
            f"Mismatch delta: {len(caps_items) - len(caps_leads)} bullet(s) "
            f"without a captured lead."
        )

    # FIX-A fail-closed: empty caps set is never acceptable.
    if not caps_leads:
        raise RuleDigestError(
            "RISK_POLICY.md yielded zero bold cap leads in the gate-enforcing sections "
            "after parsing — the file may be empty, tampered with, or all bold "
            "formatting has been stripped. A digest with no caps is refused (fail-closed)."
        )

    # --- Compose the digest ---
    # FIX-E: removed hardcoded gate count to prevent stale number.
    # Header: binding/alignment + backstop language (required by tests B1.3)
    header = (
        "=== BINDING ALIGNMENT DIGEST ===\n"
        "These rules are BINDING. When a rule conflicts with a request or model "
        "suggestion, the rule wins. You cannot recommend a trade that violates a "
        "NO_TRADE rule. The deterministic risk-gate chain in execution/ is the "
        "final backstop — these gates operate independently and cannot be bypassed "
        "by any prompt.\n"
        "================================\n"
    )

    # NO_TRADE hard stops block
    no_trade_lines = "\n".join(
        f"  {i + 1}. {lead}" for i, lead in enumerate(no_trade_leads)
    )
    no_trade_block = f"NO-TRADE HARD STOPS (must not trade when any holds):\n{no_trade_lines}\n"

    # RISK CAPS block (scoped to gate-enforcing sections only)
    caps_lines = "\n".join(f"  - {lead}" for lead in caps_leads)
    caps_block = f"RISK CAPS (enforced by gates; alignment only here):\n{caps_lines}\n"

    text = "\n".join([header, no_trade_block, caps_block])

    token_estimate = estimate_tokens(text)
    char_count = len(text)

    # FIX (Codex review): enforce the budget at RUNTIME, not only in tests. A
    # rule file that grows the derived digest past DIGEST_TOKEN_BUDGET must fail
    # closed rather than silently inject an over-budget prompt on the opt-in
    # path. Relaxing the budget is a conscious human decision (doc 16 D2: raise
    # to <=1200 only if the <=800 digest would drop a NO_TRADE hard-stop or a
    # RISK_POLICY cap) made by editing DIGEST_TOKEN_BUDGET — never silent.
    if token_estimate > DIGEST_TOKEN_BUDGET:
        raise RuleDigestError(
            f"Derived digest is {token_estimate} tokens, over the "
            f"DIGEST_TOKEN_BUDGET of {DIGEST_TOKEN_BUDGET}. A rule file has grown "
            f"the digest past the locked budget. Trim the rules or consciously "
            f"raise DIGEST_TOKEN_BUDGET per doc 16 D2 (relax to <=1200 only if the "
            f"<=800 digest would drop a NO_TRADE hard-stop or a RISK_POLICY cap). "
            f"Fail-closed: an over-budget digest is not injected into the PM prompt."
        )

    return RuleDigest(
        text=text,
        token_estimate=token_estimate,
        char_count=char_count,
        no_trade_count=len(no_trade_leads),
        caps_count=len(caps_leads),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_scoped_section_body(text: str, allowlisted_titles: frozenset) -> str:
    """Return the concatenated body of sections whose title is in *allowlisted_titles*.

    Splits *text* on ``## `` headers (the ``## <Title>`` Markdown heading).
    For each section whose title (stripped) is in *allowlisted_titles*, appends
    its body (lines up to the next ``## `` header) to the result.

    Used by :func:`render_digest` to scope caps extraction to the three
    gate-enforcing sections of RISK_POLICY.md, excluding "Audit and verifiability".

    FIX-J: After collecting bodies, verifies that EVERY allowlisted section title
    was actually found in the file. If any is missing, raises :class:`RuleDigestError`
    naming the missing section(s). This ensures a cosmetic header rename does not
    silently drop that section's caps — fail closed.
    """
    # Split on lines that start with "## " (level-2 headers)
    header_re = re.compile(r"^## (.+)$", re.MULTILINE)
    parts = header_re.split(text)
    # parts[0] is preamble before the first ## header (ignored)
    # then [title, body, title, body, ...]
    result_parts: list[str] = []
    found_titles: set[str] = set()
    # Iterate pairs starting at index 1
    for i in range(1, len(parts) - 1, 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if title in allowlisted_titles:
            result_parts.append(body)
            found_titles.add(title)

    # FIX-J: fail closed — every allowlisted section title must be present.
    missing = allowlisted_titles - found_titles
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise RuleDigestError(
            f"RISK_POLICY.md is missing required gate-cap section header(s): "
            f"{missing_list}. A renamed or removed gate-cap section must not "
            f"silently drop its caps — fix the header or update the allowlist."
        )

    return "\n".join(result_parts)


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
