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

    def test_llm_provider_defaults_to_foundry_not_openai(self):
        """The paper session must run on Azure Foundry (role-routed), never silently
        on the bare-default OpenAI endpoint. CLI > env > azure-foundry."""
        mod = _load()
        assert mod._resolve_llm_provider(None, None) == "azure-foundry"
        assert mod._resolve_llm_provider(None, "openai") == "openai"        # env respected
        assert mod._resolve_llm_provider("anthropic", "openai") == "anthropic"  # CLI wins

    # --- security-master daily cache (avoid re-hammering Angel's rate limit) -----

    def test_build_sm_uses_fresh_cache_without_refreshing(self, tmp_path, monkeypatch):
        from datetime import datetime
        from execution.config import UNIVERSE
        from execution.security_master import SecurityMaster
        mod = _load()

        cache = str(tmp_path / "sm.json")
        seeded = SecurityMaster.from_records(
            [{"symbol": s, "exchange": "NSE", "angel_token": "1"} for s in UNIVERSE]
        )
        refreshed_at = datetime(2026, 6, 8, 9, 30)
        mod._save_security_master_cache(seeded, cache, refreshed_at)

        calls = {"n": 0}
        monkeypatch.setattr(
            "execution.security_master.SecurityMaster.refresh_from_angel",
            lambda self, *a, **k: calls.__setitem__("n", calls["n"] + 1),
        )
        sm, ts = mod._build_security_master(
            cache_path=cache, max_age_hours=20, now=datetime(2026, 6, 8, 14, 0),
        )
        assert calls["n"] == 0                  # NO live refresh — cache hit
        assert ts == refreshed_at                # honest refresh time for freshness
        assert sm.lookup(UNIVERSE[0]).angel_token == "1"

    def test_build_sm_refreshes_when_cache_missing(self, tmp_path, monkeypatch):
        from datetime import datetime
        from execution.config import UNIVERSE
        from execution.security_master import SecurityMaster
        mod = _load()

        monkeypatch.setattr(
            mod, "default_universe_master",
            lambda: SecurityMaster.from_records(
                [{"symbol": s, "exchange": "NSE", "angel_token": "9"} for s in UNIVERSE]
            ),
        )
        calls = {"n": 0}
        monkeypatch.setattr(
            "execution.security_master.SecurityMaster.refresh_from_angel",
            lambda self, *a, **k: calls.__setitem__("n", calls["n"] + 1),
        )
        sm, ts = mod._build_security_master(
            cache_path=str(tmp_path / "missing.json"), max_age_hours=20,
            now=datetime(2026, 6, 8, 14, 0),
        )
        assert calls["n"] == 1                   # cache miss -> one live refresh
        assert ts is not None                    # real timestamp -> stamps 'security master' freshness

    def test_build_sm_rejects_stale_cache(self, tmp_path, monkeypatch):
        from datetime import datetime
        from execution.config import UNIVERSE
        from execution.security_master import SecurityMaster
        mod = _load()

        cache = str(tmp_path / "sm.json")
        seeded = SecurityMaster.from_records(
            [{"symbol": s, "exchange": "NSE", "angel_token": "1"} for s in UNIVERSE]
        )
        mod._save_security_master_cache(seeded, cache, datetime(2026, 6, 7, 9, 30))  # yesterday
        monkeypatch.setattr(
            mod, "default_universe_master",
            lambda: SecurityMaster.from_records(
                [{"symbol": s, "exchange": "NSE", "angel_token": "9"} for s in UNIVERSE]
            ),
        )
        calls = {"n": 0}
        monkeypatch.setattr(
            "execution.security_master.SecurityMaster.refresh_from_angel",
            lambda self, *a, **k: calls.__setitem__("n", calls["n"] + 1),
        )
        sm, ts = mod._build_security_master(
            cache_path=cache, max_age_hours=20, now=datetime(2026, 6, 8, 14, 0),
        )
        assert calls["n"] == 1                   # >20h old -> refuse cache, refresh

    def test_build_sm_rejects_blank_token_cache(self, tmp_path, monkeypatch):
        from datetime import datetime
        from execution.config import UNIVERSE
        from execution.security_master import SecurityMaster, default_universe_master
        mod = _load()

        cache = str(tmp_path / "sm.json")
        # blank-token master (as default_universe_master seeds) is useless -> must refuse
        mod._save_security_master_cache(default_universe_master(), cache, datetime(2026, 6, 8, 9, 30))
        monkeypatch.setattr(
            mod, "default_universe_master",
            lambda: SecurityMaster.from_records(
                [{"symbol": s, "exchange": "NSE", "angel_token": "9"} for s in UNIVERSE]
            ),
        )
        calls = {"n": 0}
        monkeypatch.setattr(
            "execution.security_master.SecurityMaster.refresh_from_angel",
            lambda self, *a, **k: calls.__setitem__("n", calls["n"] + 1),
        )
        sm, ts = mod._build_security_master(
            cache_path=cache, max_age_hours=20, now=datetime(2026, 6, 8, 14, 0),
        )
        assert calls["n"] == 1                   # blank tokens -> refuse, refresh
