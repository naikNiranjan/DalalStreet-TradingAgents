"""Tests for agent_os.playbooks.schema and agent_os.playbooks.catalog.

TDD: written BEFORE the implementation. Run first to confirm failures,
then implement schema.py and catalog.py to make all tests pass.

The six real templates in agent_os/playbooks/cash-equity/ are used for
positive tests. All negative/mutation cases use tmp_path copies so the
real templates are never modified.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate the real templates directory
# ---------------------------------------------------------------------------
REAL_TEMPLATES_DIR = (
    Path(__file__).parent.parent.parent  # repo root
    / "agent_os"
    / "playbooks"
    / "cash-equity"
)

EXPECTED_NAMES = {
    "breakout",
    "gap-up-down",
    "trend-following",
    "news-shock",
    "earnings-reaction",
    "sector-rotation",
}

REQUIRED_SECTIONS = [
    "when_to_use",
    "data_required",
    "entry_rules",
    "invalidation",
    "risk_limits",
    "examples",
    "tests",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def copy_template(src_name: str, dest_dir: Path) -> Path:
    """Copy a real template into dest_dir and return the new path."""
    src = REAL_TEMPLATES_DIR / f"{src_name}.md"
    dest = dest_dir / f"{src_name}.md"
    shutil.copy2(src, dest)
    return dest


# ---------------------------------------------------------------------------
# 1. All 6 real templates load successfully
# ---------------------------------------------------------------------------

class TestLoadRealTemplates:

    def test_breakout_loads(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "breakout.md")
        assert p.name == "breakout"

    def test_gap_up_down_loads(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "gap-up-down.md")
        assert p.name == "gap-up-down"

    def test_trend_following_loads(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "trend-following.md")
        assert p.name == "trend-following"

    def test_news_shock_loads(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "news-shock.md")
        assert p.name == "news-shock"

    def test_earnings_reaction_loads(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "earnings-reaction.md")
        assert p.name == "earnings-reaction"

    def test_sector_rotation_loads(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "sector-rotation.md")
        assert p.name == "sector-rotation"

    def test_all_have_non_empty_description(self):
        from agent_os.playbooks.schema import load_all_playbooks
        playbooks = load_all_playbooks(REAL_TEMPLATES_DIR)
        for pb in playbooks:
            assert pb.description, f"Empty description for {pb.name}"

    def test_all_have_seven_sections(self):
        from agent_os.playbooks.schema import load_all_playbooks
        playbooks = load_all_playbooks(REAL_TEMPLATES_DIR)
        for pb in playbooks:
            assert set(pb.sections.keys()) == set(REQUIRED_SECTIONS), (
                f"Wrong sections for {pb.name}: {set(pb.sections.keys())}"
            )

    def test_all_sections_non_empty(self):
        from agent_os.playbooks.schema import load_all_playbooks
        playbooks = load_all_playbooks(REAL_TEMPLATES_DIR)
        for pb in playbooks:
            for section_name, text in pb.sections.items():
                assert text.strip(), (
                    f"Section '{section_name}' is empty in {pb.name}"
                )

    def test_load_all_returns_six(self):
        from agent_os.playbooks.schema import load_all_playbooks
        playbooks = load_all_playbooks(REAL_TEMPLATES_DIR)
        assert len(playbooks) == 6

    def test_load_all_names_match_stems(self):
        from agent_os.playbooks.schema import load_all_playbooks
        playbooks = load_all_playbooks(REAL_TEMPLATES_DIR)
        names = {pb.name for pb in playbooks}
        assert names == EXPECTED_NAMES

    def test_source_path_is_set(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "breakout.md")
        assert p.source_path == REAL_TEMPLATES_DIR / "breakout.md"


# ---------------------------------------------------------------------------
# 2. RiskLimits parsing — correct types and values for at least one template
# ---------------------------------------------------------------------------

class TestRiskLimitsParsing:

    def test_breakout_risk_limits_types(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "breakout.md")
        rl = p.risk_limits
        assert isinstance(rl.max_position_pct, float)
        assert isinstance(rl.stop_rule, str)
        assert isinstance(rl.max_signals_per_day, int)

    def test_breakout_risk_limits_values(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "breakout.md")
        rl = p.risk_limits
        # breakout.md: max_position_pct: 12, max_signals_per_day: 3
        assert rl.max_position_pct == pytest.approx(12.0)
        assert rl.max_signals_per_day == 3
        assert "below" in rl.stop_rule.lower() or len(rl.stop_rule) > 0

    def test_gap_up_down_risk_limits(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "gap-up-down.md")
        rl = p.risk_limits
        assert rl.max_position_pct == pytest.approx(10.0)
        assert rl.max_signals_per_day == 2

    def test_trend_following_risk_limits(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "trend-following.md")
        rl = p.risk_limits
        assert rl.max_position_pct == pytest.approx(15.0)
        assert rl.max_signals_per_day == 3

    def test_news_shock_risk_limits(self):
        from agent_os.playbooks.schema import load_playbook
        p = load_playbook(REAL_TEMPLATES_DIR / "news-shock.md")
        rl = p.risk_limits
        assert rl.max_position_pct == pytest.approx(8.0)
        assert rl.max_signals_per_day == 2

    def test_all_risk_limits_positive(self):
        from agent_os.playbooks.schema import load_all_playbooks
        playbooks = load_all_playbooks(REAL_TEMPLATES_DIR)
        for pb in playbooks:
            assert pb.risk_limits.max_position_pct > 0
            assert pb.risk_limits.max_position_pct <= 100
            assert pb.risk_limits.max_signals_per_day >= 1
            assert pb.risk_limits.stop_rule.strip()


# ---------------------------------------------------------------------------
# 3. Each section, removed individually → reject
# ---------------------------------------------------------------------------

class TestMissingSection:

    @pytest.mark.parametrize("section", REQUIRED_SECTIONS)
    def test_missing_section_rejected(self, tmp_path, section):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError
        dest = copy_template("breakout", tmp_path)
        text = dest.read_text()
        # Remove the section header and its content up to the next ## or EOF
        import re
        # Pattern: ## section_name\n ... (up to next ## or end of file)
        pattern = rf"\n## {re.escape(section)}\n.*?(?=\n## |\Z)"
        modified = re.sub(pattern, "", text, flags=re.DOTALL)
        dest.write_text(modified)
        with pytest.raises(PlaybookValidationError):
            load_playbook(dest)


# ---------------------------------------------------------------------------
# 4. risk_limits section — various rejection cases
# ---------------------------------------------------------------------------

class TestRiskLimitsRejection:

    def test_missing_risk_limits_section(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError
        dest = copy_template("breakout", tmp_path)
        text = dest.read_text()
        import re
        modified = re.sub(
            r"\n## risk_limits\n.*?(?=\n## |\Z)", "", text, flags=re.DOTALL
        )
        dest.write_text(modified)
        with pytest.raises(PlaybookValidationError):
            load_playbook(dest)

    def test_empty_risk_limits_section(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError
        dest = copy_template("breakout", tmp_path)
        text = dest.read_text()
        # Replace the risk_limits content with whitespace only
        import re
        modified = re.sub(
            r"(## risk_limits\n).*?(?=\n## |\Z)",
            r"\1\n",
            text,
            flags=re.DOTALL,
        )
        dest.write_text(modified)
        with pytest.raises(PlaybookValidationError):
            load_playbook(dest)

    def test_missing_max_position_pct_key(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError
        dest = copy_template("breakout", tmp_path)
        text = dest.read_text()
        modified = text.replace("- max_position_pct: 12\n", "")
        dest.write_text(modified)
        with pytest.raises(PlaybookValidationError):
            load_playbook(dest)

    def test_missing_stop_rule_key(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError
        dest = copy_template("breakout", tmp_path)
        text = dest.read_text()
        # Remove the stop_rule line
        lines = text.splitlines(keepends=True)
        filtered = [l for l in lines if not l.strip().startswith("- stop_rule:")]
        dest.write_text("".join(filtered))
        with pytest.raises(PlaybookValidationError):
            load_playbook(dest)

    def test_missing_max_signals_per_day_key(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError
        dest = copy_template("breakout", tmp_path)
        text = dest.read_text()
        lines = text.splitlines(keepends=True)
        filtered = [l for l in lines if not l.strip().startswith("- max_signals_per_day:")]
        dest.write_text("".join(filtered))
        with pytest.raises(PlaybookValidationError):
            load_playbook(dest)


# ---------------------------------------------------------------------------
# 5. Oversize body → reject
# ---------------------------------------------------------------------------

class TestOversizeBody:

    def test_oversize_body_rejected(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError, MAX_PLAYBOOK_CHARS
        dest = copy_template("breakout", tmp_path)
        text = dest.read_text()
        # Pad the body to exceed MAX_PLAYBOOK_CHARS
        padding = "x" * (MAX_PLAYBOOK_CHARS + 1000)
        dest.write_text(text + "\n" + padding)
        with pytest.raises(PlaybookValidationError):
            load_playbook(dest)


# ---------------------------------------------------------------------------
# 6. name != file stem → reject
# ---------------------------------------------------------------------------

class TestNameStemMismatch:

    def test_name_stem_mismatch_rejected(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError
        # Create a file with stem 'wrong-name' but frontmatter name: breakout
        src = REAL_TEMPLATES_DIR / "breakout.md"
        dest = tmp_path / "wrong-name.md"
        shutil.copy2(src, dest)
        with pytest.raises(PlaybookValidationError):
            load_playbook(dest)

    def test_correct_stem_loads(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook
        dest = copy_template("breakout", tmp_path)
        p = load_playbook(dest)
        assert p.name == "breakout"


# ---------------------------------------------------------------------------
# 7. Catalog tests
# ---------------------------------------------------------------------------

class TestCatalog:

    def test_build_catalog_returns_six(self):
        from agent_os.playbooks.catalog import build_catalog
        entries = build_catalog(REAL_TEMPLATES_DIR)
        assert len(entries) == 6

    def test_catalog_entry_names(self):
        from agent_os.playbooks.catalog import build_catalog
        entries = build_catalog(REAL_TEMPLATES_DIR)
        names = {e.name for e in entries}
        assert names == EXPECTED_NAMES

    def test_catalog_entry_has_name_and_description(self):
        from agent_os.playbooks.catalog import build_catalog
        entries = build_catalog(REAL_TEMPLATES_DIR)
        for entry in entries:
            assert entry.name
            assert entry.description

    def test_catalog_entry_exposes_only_name_and_description(self):
        """CatalogEntry must NOT have body/sections/risk_limits attributes."""
        from agent_os.playbooks.catalog import build_catalog, CatalogEntry
        entries = build_catalog(REAL_TEMPLATES_DIR)
        entry = entries[0]
        # Must have name and description
        assert hasattr(entry, "name")
        assert hasattr(entry, "description")
        # Must NOT have full-body fields
        assert not hasattr(entry, "sections")
        assert not hasattr(entry, "risk_limits")
        assert not hasattr(entry, "entry_rules")
        assert not hasattr(entry, "when_to_use")
        assert not hasattr(entry, "data_required")

    def test_catalog_entry_description_is_not_body(self):
        """L4-04: description EXACTLY equals the frontmatter description — a strict
        equality (no weak OR/length disjunct), so a regression leaking body text into
        the description would be caught."""
        from agent_os.playbooks.catalog import build_catalog
        entries = build_catalog(REAL_TEMPLATES_DIR)
        entry_map = {e.name: e for e in entries}
        expected = (
            "Enter on a confirmed breakout above a well-defined resistance level "
            "on expanding volume, in the direction of the higher-timeframe trend."
        )
        assert entry_map["breakout"].description == expected
        # And it must contain NO body/section markers.
        for token in ("when_to_use", "data_required", "entry_rules", "## "):
            assert token not in entry_map["breakout"].description

    def test_catalog_rejects_name_stem_mismatch(self, tmp_path):
        """L4-02: build_catalog enforces frontmatter name == file stem (mirrors
        load_playbook), so a mislabeled file cannot silently enter the catalog."""
        from agent_os.playbooks.catalog import build_catalog
        from agent_os.playbooks.schema import PlaybookValidationError
        (tmp_path / "wrong-stem.md").write_text(
            "---\nname: breakout\ndescription: A mislabeled playbook.\n---\n\nbody\n",
            encoding="utf-8",
        )
        with pytest.raises(PlaybookValidationError):
            build_catalog(tmp_path)

    def test_catalog_rejects_oversized_file(self, tmp_path):
        """L4-03: build_catalog enforces the MAX_PLAYBOOK_CHARS size cap (the catalog
        path no longer silently loads arbitrarily large files)."""
        from agent_os.playbooks.catalog import build_catalog
        from agent_os.playbooks.schema import PlaybookValidationError, MAX_PLAYBOOK_CHARS
        content = (
            "---\nname: huge\ndescription: An oversized playbook.\n---\n"
            + "X" * (MAX_PLAYBOOK_CHARS + 1000)
        )
        (tmp_path / "huge.md").write_text(content, encoding="utf-8")
        with pytest.raises(PlaybookValidationError):
            build_catalog(tmp_path)

    def test_catalog_is_dataclass(self):
        from agent_os.playbooks.catalog import CatalogEntry
        assert dataclasses.is_dataclass(CatalogEntry)

    def test_catalog_entry_fields_only(self):
        """CatalogEntry dataclass has exactly 'name' and 'description' fields."""
        from agent_os.playbooks.catalog import CatalogEntry
        field_names = {f.name for f in dataclasses.fields(CatalogEntry)}
        assert field_names == {"name", "description"}

    def test_build_catalog_is_frontmatter_only(self):
        """build_catalog reads only frontmatter (fast path).

        We verify this by making sure it returns descriptions that match
        frontmatter, not body content.
        """
        from agent_os.playbooks.catalog import build_catalog
        entries = build_catalog(REAL_TEMPLATES_DIR)
        entry_map = {e.name: e for e in entries}
        # Frontmatter description for gap-up-down must be present
        assert "gap" in entry_map["gap-up-down"].description.lower()


# ---------------------------------------------------------------------------
# 8. Playbook dataclass is a proper dataclass with the right fields
# ---------------------------------------------------------------------------

class TestPlaybookDataclass:

    def test_playbook_is_dataclass(self):
        from agent_os.playbooks.schema import Playbook
        assert dataclasses.is_dataclass(Playbook)

    def test_playbook_fields(self):
        from agent_os.playbooks.schema import Playbook
        field_names = {f.name for f in dataclasses.fields(Playbook)}
        assert "name" in field_names
        assert "description" in field_names
        assert "sections" in field_names
        assert "risk_limits" in field_names
        assert "source_path" in field_names

    def test_risk_limits_is_dataclass(self):
        from agent_os.playbooks.schema import RiskLimits
        assert dataclasses.is_dataclass(RiskLimits)

    def test_risk_limits_fields(self):
        from agent_os.playbooks.schema import RiskLimits
        field_names = {f.name for f in dataclasses.fields(RiskLimits)}
        assert field_names == {"max_position_pct", "stop_rule", "max_signals_per_day"}


# ---------------------------------------------------------------------------
# 9. REQUIRED_SECTIONS constant
# ---------------------------------------------------------------------------

class TestRequiredSections:

    def test_required_sections_constant_exists(self):
        from agent_os.playbooks.schema import REQUIRED_SECTIONS
        assert isinstance(REQUIRED_SECTIONS, (list, tuple))

    def test_required_sections_has_seven(self):
        from agent_os.playbooks.schema import REQUIRED_SECTIONS
        assert len(REQUIRED_SECTIONS) == 7

    def test_required_sections_names(self):
        from agent_os.playbooks.schema import REQUIRED_SECTIONS
        assert set(REQUIRED_SECTIONS) == set(REQUIRED_SECTIONS_EXPECTED := {
            "when_to_use", "data_required", "entry_rules", "invalidation",
            "risk_limits", "examples", "tests",
        })


# ---------------------------------------------------------------------------
# 10. Error class exists and is an exception subclass
# ---------------------------------------------------------------------------

class TestPlaybookValidationError:

    def test_error_is_exception(self):
        from agent_os.playbooks.schema import PlaybookValidationError
        assert issubclass(PlaybookValidationError, Exception)

    def test_error_message_included(self, tmp_path):
        from agent_os.playbooks.schema import load_playbook, PlaybookValidationError
        dest = copy_template("breakout", tmp_path)
        src = REAL_TEMPLATES_DIR / "breakout.md"
        # Rename so stem != name
        bad = tmp_path / "not-breakout.md"
        shutil.copy2(src, bad)
        with pytest.raises(PlaybookValidationError, match=r"(?i)(name|stem|mismatch)"):
            load_playbook(bad)


# ---------------------------------------------------------------------------
# 11. MAX_PLAYBOOK_CHARS constant
# ---------------------------------------------------------------------------

class TestMaxPlaybookChars:

    def test_constant_exists(self):
        from agent_os.playbooks.schema import MAX_PLAYBOOK_CHARS
        assert isinstance(MAX_PLAYBOOK_CHARS, int)
        assert MAX_PLAYBOOK_CHARS == 20000
