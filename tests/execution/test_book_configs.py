"""Step 3 (integration slice doc 14 §2a) — book-specific sizing configs.

The committed ExecutionConfig defaults are right for the ₹10L signal book but
**structurally block** the ₹25k shadow book: 10-15% of ₹25k = ₹2,500-3,750, both
below the ₹5,000 deadband -> the shadow book would never trade and the
go-live-size gate would measure nothing. The shadow config raises the caps and
drops the absolute deadband floor so whole-share rounding (sub_economic_skipped)
becomes the real small-capital floor.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from execution.config import (
    DEFAULT_EXECUTION_CONFIG,
    default_books,
    shadow_book_config,
    signal_book_config,
)
from execution.contracts import Action, SignalDecision
from execution.risk.sizing import size
from execution.security_master import Instrument

AS_OF = datetime(2026, 6, 9, 11, 0)
FRESH = {"daily OHLCV": "2026-06-09T00:00:00", "security master": "2026-06-09T09:00:00"}
INST = Instrument(symbol="X.NS", exchange="NSE", angel_token="1", isin="", lot_size=1, tick_size=0.05)


def _buy(symbol="X.NS", conf=0.85, action=Action.STRONG_BUY):
    return SignalDecision(symbol, action, conf, AS_OF, "Buy", "d", FRESH)


# ---------------------------------------------------------------------------
# The factories (table in §2a)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBookConfigFactories:
    def test_signal_book_matches_committed_defaults(self):
        c = signal_book_config()
        assert c.position_cap_init == 0.10
        assert c.position_cap_hard == 0.15
        assert c.deadband_min_notional == 5_000.0
        assert c.deadband_equity_frac == 0.02

    def test_shadow_book_overrides_exactly_the_four_locked_params(self):
        c = shadow_book_config()
        assert c.position_cap_init == 0.35
        assert c.position_cap_hard == 0.40
        assert c.deadband_min_notional == 0.0
        assert c.deadband_equity_frac == 0.02  # stays relative (2% of ₹25k = ₹500)

    def test_shadow_leaves_everything_else_identical_to_signal(self):
        sig, sh = signal_book_config(), shadow_book_config()
        # Only the four §2a params differ; the rest of the policy is shared.
        assert sh.confidence_floor == sig.confidence_floor
        assert sh.spread_hard == sig.spread_hard
        assert sh.stale_quote_seconds == sig.stale_quote_seconds
        assert sh.exec_window_start == sig.exec_window_start
        assert sh.costs == sig.costs

    def test_default_books_pairs_capital_with_config(self):
        books = default_books()
        names = {b.name for b in books}
        assert names == {"signal", "shadow"}
        by = {b.name: b for b in books}
        assert by["signal"].capital == DEFAULT_EXECUTION_CONFIG.signal_book_capital == 1_000_000.0
        assert by["shadow"].capital == DEFAULT_EXECUTION_CONFIG.shadow_book_capital == 25_000.0
        assert by["signal"].config.position_cap_init == 0.10
        assert by["shadow"].config.position_cap_init == 0.35


# ---------------------------------------------------------------------------
# The behavior the config exists to produce
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShadowBookActuallyTrades:
    def test_signal_config_traps_a_25k_book_in_the_deadband(self):
        """The bug being fixed: ₹10L defaults on ₹25k -> target below ₹5k deadband."""
        r = size(_buy(), equity=25_000.0, current_qty=0, ref_price=1300.0,
                 instrument=INST, config=signal_book_config())
        # 25000*0.10*1.0*0.85 = ₹2,125 target -> 1 share -> ₹1,300 < ₹5,000 deadband.
        assert not r.trades
        assert r.reason == "deadband"

    def test_shadow_config_lets_the_25k_book_buy_whole_shares(self):
        r = size(_buy(), equity=25_000.0, current_qty=0, ref_price=1300.0,
                 instrument=INST, config=shadow_book_config())
        # 25000*0.35*1.0*0.85 = ₹7,437.50 target -> round(5.72) = 6 shares; deadband floor is gone.
        assert r.trades
        assert r.delta_qty == 6

    def test_shadow_config_affords_a_share_of_an_expensive_name(self):
        """A ~₹6,000 name (≈24% of ₹25k) must be reachable, not deadband-trapped."""
        r = size(_buy(symbol="BIGNAME.NS"), equity=25_000.0, current_qty=0, ref_price=6_000.0,
                 instrument=INST, config=shadow_book_config())
        assert r.trades
        assert r.delta_qty == 1  # round(7437.5 / 6000) = 1 — the whole-share floor at work

    def test_shadow_config_honestly_skips_when_it_cannot_afford_a_share(self):
        """Low conviction Overweight on an expensive name rounds to 0 -> honest skip."""
        r = size(_buy(symbol="TCS.NS", conf=0.35, action=Action.BUY),
                 equity=25_000.0, current_qty=0, ref_price=3_850.0,
                 instrument=INST, config=shadow_book_config())
        # 25000*0.35*0.6*0.35 = ₹1,837.50 target -> floor(1837.5/3850) = 0 shares.
        assert not r.trades
        assert r.reason == "sub_economic_skipped"

    def test_signal_book_on_10L_sizes_normally(self):
        r = size(_buy(symbol="TCS.NS"), equity=1_000_000.0, current_qty=0, ref_price=3_850.0,
                 instrument=INST, config=signal_book_config())
        # 1000000*0.10*1.0*0.85 = ₹85,000 -> floor(85000/3850) = 22 shares.
        assert r.trades
        assert r.delta_qty == 22
