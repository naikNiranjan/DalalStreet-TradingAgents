"""Playbook catalog — progressive disclosure tier 1.

Reads ONLY the frontmatter from each playbook file to build a lightweight
index of (name, description) pairs. The full body is never loaded by this
module; call schema.load_playbook() to access sections and risk_limits.

Design:
- CatalogEntry exposes ONLY name + description (no body, no sections).
- build_catalog reads frontmatter with stdlib string handling, mirroring
  the parse pattern in schema.py — no yaml import.
- Fail-closed: files without valid frontmatter raise PlaybookValidationError.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from agent_os.playbooks.schema import PlaybookValidationError, MAX_PLAYBOOK_CHARS


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class CatalogEntry:
    """Lightweight catalog entry — frontmatter only (progressive disclosure)."""
    name: str
    description: str


# ---------------------------------------------------------------------------
# Frontmatter-only parser (stdlib, no yaml)
# ---------------------------------------------------------------------------

def _read_frontmatter_only(path: Path) -> Dict[str, str]:
    """Read just the frontmatter key/value pairs from a .md file.

    Reads the entire file but only PARSES up to the closing '---' fence, so
    name and description are the only fields exposed. The body is not parsed
    or returned.

    Returns a dict of {key: value} strings.
    Raises PlaybookValidationError if the frontmatter structure is absent or
    if the file size exceeds MAX_PLAYBOOK_CHARS.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlaybookValidationError(f"Cannot read {path}: {exc}") from exc

    if len(raw) > MAX_PLAYBOOK_CHARS:
        raise PlaybookValidationError(
            f"{path}: file size {len(raw)} chars exceeds MAX_PLAYBOOK_CHARS "
            f"({MAX_PLAYBOOK_CHARS})"
        )

    if not raw.startswith("---"):
        raise PlaybookValidationError(
            f"{path}: missing opening frontmatter fence '---'"
        )

    rest = raw[3:]
    end_match = re.search(r"\n---[ \t]*\n", rest)
    if not end_match:
        raise PlaybookValidationError(
            f"{path}: missing closing frontmatter fence '---'"
        )

    fm_text = rest[: end_match.start()]
    metadata: Dict[str, str] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        metadata[key.strip()] = value.strip()

    return metadata


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_catalog(directory: Path) -> List[CatalogEntry]:
    """Build a catalog of playbooks from *directory* reading frontmatter only.

    Each .md file in *directory* contributes exactly one CatalogEntry.
    Files with missing or invalid frontmatter, a name that does not match
    the file stem, or size exceeding MAX_PLAYBOOK_CHARS raise
    PlaybookValidationError (fail-closed).

    Returns entries sorted by name.
    """
    directory = Path(directory)
    entries: List[CatalogEntry] = []

    for md_file in sorted(directory.glob("*.md")):
        metadata = _read_frontmatter_only(md_file)

        name = metadata.get("name", "").strip()
        if not name:
            raise PlaybookValidationError(
                f"{md_file}: frontmatter 'name' is missing or empty"
            )

        # Enforce name == file stem (mirrors load_playbook contract)
        if name != md_file.stem:
            raise PlaybookValidationError(
                f"{md_file}: frontmatter name {name!r} does not match "
                f"file stem {md_file.stem!r}"
            )

        description = metadata.get("description", "").strip()
        if not description:
            raise PlaybookValidationError(
                f"{md_file}: frontmatter 'description' is missing or empty"
            )

        entries.append(CatalogEntry(name=name, description=description))

    return entries
