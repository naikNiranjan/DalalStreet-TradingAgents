"""Playbook schema loader and validator for agent_os.

Parses cash-equity playbook Markdown files with YAML-style frontmatter
and structured section headers. Uses stdlib only — no PyYAML import.

Design constraints:
- Fail-closed: PlaybookValidationError on ANY violation.
- No order-placement authority. This module only reads and validates.
- No imports from references/hermes-agent/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS: List[str] = [
    "when_to_use",
    "data_required",
    "entry_rules",
    "invalidation",
    "risk_limits",
    "examples",
    "tests",
]

MAX_PLAYBOOK_CHARS: int = 20_000
_MAX_DESCRIPTION_CHARS: int = 300


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PlaybookValidationError(Exception):
    """Raised when a playbook file fails any validation check."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RiskLimits:
    """Machine-readable risk parameters extracted from the risk_limits section."""
    max_position_pct: float   # >0 and <=100
    stop_rule: str             # non-empty
    max_signals_per_day: int  # >=1


@dataclass
class Playbook:
    """Fully loaded and validated playbook."""
    name: str
    description: str
    sections: Dict[str, str]      # section_name -> body text (stripped)
    risk_limits: RiskLimits
    source_path: Path


# ---------------------------------------------------------------------------
# Frontmatter parsing (stdlib only — no yaml import)
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[Dict[str, str], str]:
    """Parse simple key: value frontmatter between the first two --- fences.

    Returns (metadata_dict, body_after_fence).
    Keys and values are plain strings; only 'name' and 'description' are used.
    Raises PlaybookValidationError when the frontmatter structure is missing.
    """
    if not content.startswith("---"):
        raise PlaybookValidationError("Missing opening frontmatter fence '---'")

    # Find the closing fence: a line that is exactly '---'
    rest = content[3:]
    # Look for a newline followed by --- followed by newline or end
    end_match = re.search(r"\n---[ \t]*\n", rest)
    if not end_match:
        raise PlaybookValidationError("Missing closing frontmatter fence '---'")

    fm_text = rest[: end_match.start()]
    body = rest[end_match.end():]

    metadata: Dict[str, str] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        metadata[key.strip()] = value.strip()

    return metadata, body


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

def _parse_sections(body: str) -> Dict[str, str]:
    """Split a markdown body into sections keyed by ## header name.

    Each value is the text between successive ## headers (stripped).
    Returns a dict; does NOT validate which sections are present.
    """
    sections: Dict[str, str] = {}
    # Split on lines that are exactly '## <name>'
    parts = re.split(r"\n## ([^\n]+)\n", "\n" + body)
    # parts[0] is text before the first ##, then alternating name/content
    i = 1
    while i + 1 <= len(parts) - 1:
        section_name = parts[i].strip()
        section_body = parts[i + 1]
        sections[section_name] = section_body.strip()
        i += 2
    return sections


# ---------------------------------------------------------------------------
# risk_limits section parser
# ---------------------------------------------------------------------------

def _parse_risk_limits(section_text: str, source_path: Path) -> RiskLimits:
    """Parse the three machine-readable lines from a risk_limits section.

    Expected format (in any order within the section):
        - max_position_pct: <number>
        - stop_rule: <string>
        - max_signals_per_day: <int>
    """
    if not section_text.strip():
        raise PlaybookValidationError(
            f"{source_path}: risk_limits section is empty"
        )

    data: Dict[str, str] = {}
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        # Remove leading dash
        stripped = stripped[1:].strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        data[key.strip()] = value.strip()

    # Validate all three keys present
    for required_key in ("max_position_pct", "stop_rule", "max_signals_per_day"):
        if required_key not in data:
            raise PlaybookValidationError(
                f"{source_path}: risk_limits missing key '{required_key}'"
            )

    # Parse max_position_pct
    try:
        max_pos = float(data["max_position_pct"])
    except ValueError:
        raise PlaybookValidationError(
            f"{source_path}: max_position_pct is not a number: "
            f"{data['max_position_pct']!r}"
        )
    if not (0 < max_pos <= 100):
        raise PlaybookValidationError(
            f"{source_path}: max_position_pct={max_pos} is out of range (0, 100]"
        )

    # Parse stop_rule
    stop_rule = data["stop_rule"].strip()
    if not stop_rule:
        raise PlaybookValidationError(
            f"{source_path}: stop_rule is empty"
        )

    # Parse max_signals_per_day
    try:
        max_sig = int(data["max_signals_per_day"])
    except ValueError:
        raise PlaybookValidationError(
            f"{source_path}: max_signals_per_day is not an int: "
            f"{data['max_signals_per_day']!r}"
        )
    if max_sig < 1:
        raise PlaybookValidationError(
            f"{source_path}: max_signals_per_day={max_sig} must be >= 1"
        )

    return RiskLimits(
        max_position_pct=max_pos,
        stop_rule=stop_rule,
        max_signals_per_day=max_sig,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_playbook(path: Path) -> Playbook:
    """Load and strictly validate a playbook Markdown file.

    Raises PlaybookValidationError on any violation:
    - Missing / malformed frontmatter
    - name != file stem
    - Empty or over-long description
    - Body exceeds MAX_PLAYBOOK_CHARS
    - Any of the 7 REQUIRED_SECTIONS missing or empty
    - risk_limits section missing, empty, or incomplete
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlaybookValidationError(f"Cannot read {path}: {exc}") from exc

    # Body size cap (entire file content, not just body)
    if len(raw) > MAX_PLAYBOOK_CHARS:
        raise PlaybookValidationError(
            f"{path}: file size {len(raw)} chars exceeds MAX_PLAYBOOK_CHARS "
            f"({MAX_PLAYBOOK_CHARS})"
        )

    metadata, body = _parse_frontmatter(raw)

    # Validate name
    name = metadata.get("name", "").strip()
    if not name:
        raise PlaybookValidationError(f"{path}: frontmatter 'name' is missing or empty")

    stem = path.stem
    if name != stem:
        raise PlaybookValidationError(
            f"{path}: frontmatter name {name!r} does not match file stem {stem!r}"
        )

    # Validate description
    description = metadata.get("description", "").strip()
    if not description:
        raise PlaybookValidationError(
            f"{path}: frontmatter 'description' is missing or empty"
        )
    if len(description) > _MAX_DESCRIPTION_CHARS:
        raise PlaybookValidationError(
            f"{path}: description length {len(description)} exceeds "
            f"{_MAX_DESCRIPTION_CHARS} chars"
        )

    # Parse sections
    sections = _parse_sections(body)

    # Validate all 7 required sections are present and non-empty
    for section_name in REQUIRED_SECTIONS:
        if section_name not in sections:
            raise PlaybookValidationError(
                f"{path}: required section '## {section_name}' is missing"
            )
        if not sections[section_name].strip():
            raise PlaybookValidationError(
                f"{path}: section '## {section_name}' is present but empty"
            )

    # Parse risk_limits
    risk_limits = _parse_risk_limits(sections["risk_limits"], path)

    return Playbook(
        name=name,
        description=description,
        sections=sections,
        risk_limits=risk_limits,
        source_path=path,
    )


def load_all_playbooks(directory: Path) -> List[Playbook]:
    """Load all .md files in *directory* as Playbook objects.

    Files that fail validation raise PlaybookValidationError (fail-closed).
    Returns a list sorted by playbook name.
    """
    directory = Path(directory)
    playbooks: List[Playbook] = []
    for md_file in sorted(directory.glob("*.md")):
        playbooks.append(load_playbook(md_file))
    return playbooks
