"""refresh_from_angel resilience — throttle + backoff against Angel's rate limit.

Angel SmartAPI rate-limits ``searchScrip``; resolving 12 tokens back-to-back trips
'Access denied because of exceeding access rate'. refresh_from_angel must throttle
between calls and retry rate-limit errors with backoff — but NOT retry genuine
not-found errors. Fully offline: the angel_one helpers are monkeypatched.
"""

from __future__ import annotations

import pytest

import tradingagents.dataflows.angel_one as ao
from execution.security_master import SecurityMaster


@pytest.fixture
def _patch_angel(monkeypatch):
    monkeypatch.setattr(ao, "_get_client", lambda: object())
    monkeypatch.setattr(ao, "_exchange_for", lambda s: ("NSE", s.split(".")[0]))


@pytest.mark.unit
class TestRefreshThrottleAndRetry:
    def test_retries_rate_limit_then_succeeds(self, _patch_angel, monkeypatch):
        calls = {"n": 0}

        def flaky(client, base, exchange):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("Couldn't parse ... Access denied because of exceeding access rate")
            return "2885"

        monkeypatch.setattr(ao, "_resolve_token", flaky)
        sleeps = []
        sm = SecurityMaster.from_records([{"symbol": "RELIANCE.NS", "exchange": "NSE"}])
        sm.refresh_from_angel(["RELIANCE.NS"], delay_s=0.01, sleep=sleeps.append)

        assert sm.lookup("RELIANCE.NS").angel_token == "2885"
        assert calls["n"] == 3                 # retried twice, then succeeded
        assert len(sleeps) >= 2                 # backed off between retries
        assert sleeps[1] > sleeps[0]            # exponential-ish backoff

    def test_does_not_retry_non_rate_limit_errors(self, _patch_angel, monkeypatch):
        calls = {"n": 0}

        def not_found(client, base, exchange):
            calls["n"] += 1
            raise RuntimeError("no NSE cash-equity scrip for 'WIPRO'")

        monkeypatch.setattr(ao, "_resolve_token", not_found)
        sm = SecurityMaster.from_records([{"symbol": "WIPRO.NS", "exchange": "NSE"}])
        with pytest.raises(RuntimeError, match="no NSE cash-equity scrip"):
            sm.refresh_from_angel(["WIPRO.NS"], delay_s=0.01, sleep=lambda s: None)
        assert calls["n"] == 1                  # failed fast, no wasted retries

    def test_throttles_between_symbols(self, _patch_angel, monkeypatch):
        monkeypatch.setattr(ao, "_resolve_token", lambda c, b, e: "1")
        sleeps = []
        sm = SecurityMaster.from_records([
            {"symbol": "RELIANCE.NS", "exchange": "NSE"},
            {"symbol": "INFY.NS", "exchange": "NSE"},
            {"symbol": "TCS.NS", "exchange": "NSE"},
        ])
        sm.refresh_from_angel(["RELIANCE.NS", "INFY.NS", "TCS.NS"], delay_s=0.01, sleep=sleeps.append)
        # one inter-call throttle between each of the 3 symbols (>= 2 gaps)
        assert len(sleeps) >= 2
        assert all(s.angel_token == "1" for s in (sm.lookup("RELIANCE.NS"), sm.lookup("TCS.NS")))

    def test_gives_up_after_max_retries(self, _patch_angel, monkeypatch):
        def always_limited(client, base, exchange):
            raise RuntimeError("Access denied because of exceeding access rate")

        monkeypatch.setattr(ao, "_resolve_token", always_limited)
        sm = SecurityMaster.from_records([{"symbol": "RELIANCE.NS", "exchange": "NSE"}])
        with pytest.raises(RuntimeError, match="access rate"):
            sm.refresh_from_angel(["RELIANCE.NS"], delay_s=0.01, max_retries=3, sleep=lambda s: None)
