"""Step 5 — offline smoke for scripts/paper_session.py (no live calls).

Covers the script's pure wiring (arg parsing + book selection). The live path
(refresh_from_angel / graph LLM / Angel FULL quote) is intentionally NOT exercised
in CI — it's manual/credentialed. We only prove the runnable surface is sound.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "paper_session.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("paper_session_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
class TestPaperSessionScript:
    def test_paper_is_the_hard_default(self):
        mod = _load()
        args = mod._parse_args([])
        assert args.books == "both"
        assert args.kill_switch is False
        assert args.analysis_only is False and args.exec_only is False
        assert args.out_dir == "runs"

    def test_analysis_only_and_exec_only_are_mutually_exclusive(self):
        mod = _load()
        with pytest.raises(SystemExit):
            mod._parse_args(["--analysis-only", "--exec-only"])

    def test_select_books_filters(self):
        mod = _load()
        assert {b.name for b in mod._select_books("both")} == {"signal", "shadow"}
        assert [b.name for b in mod._select_books("signal")] == ["signal"]
        assert [b.name for b in mod._select_books("shadow")] == ["shadow"]
        # the shadow book carries its own (₹25k-tuned) config
        shadow = mod._select_books("shadow")[0]
        assert shadow.capital == 25_000.0
        assert shadow.config.position_cap_init == 0.35

    def test_security_master_setup_always_refreshes(self, monkeypatch):
        """Reviewer finding #1: the SM is refreshed in EVERY mode (incl. analysis-only),
        so sm_refreshed_at is real and 'security master' freshness gets stamped — never
        None (which would block every trade in a later --exec-only run)."""
        mod = _load()
        calls = {"n": 0}

        def fake_refresh(self, symbols, *, save_to=None):
            calls["n"] += 1  # populate nothing; just prove the LIVE refresh fired

        monkeypatch.setattr(
            "execution.security_master.SecurityMaster.refresh_from_angel", fake_refresh
        )
        sm, refreshed_at = mod._build_security_master()
        assert calls["n"] == 1          # refresh happens regardless of run mode
        assert refreshed_at is not None  # a real timestamp to stamp into signal freshness

    def test_llm_provider_defaults_to_foundry_not_openai(self):
        """The paper session must run on Azure Foundry (role-routed), never silently
        on the bare-default OpenAI endpoint. CLI > env > azure-foundry."""
        mod = _load()
        assert mod._resolve_llm_provider(None, None) == "azure-foundry"
        assert mod._resolve_llm_provider(None, "openai") == "openai"        # env respected
        assert mod._resolve_llm_provider("anthropic", "openai") == "anthropic"  # CLI wins
