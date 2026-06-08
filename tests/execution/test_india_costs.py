"""Task 2 — India cost model, pinned to worked examples (₹0.01)."""

from __future__ import annotations

import pytest

from execution.config import CostConfig
from execution.contracts import Side
from execution.costs import compute_charges


# --- worked BUY example: 10 RELIANCE @ ₹1300, NSE (Angel delivery schedule) -----
# turnover 13000; brokerage min(0.1%×13000=13, cap20)=13.00 (floor 5 n/a);
# STT 0.1% = 13.00; stamp 0.015% = 1.95; exch 0.0030699% = 0.40; SEBI ₹10/cr = 0.01;
# GST 18%×(13.00+0.40+0.01+0)=2.41; DP 0 (buy).
# total = 13.00 + 13.00 + 1.95 + 0.40 + 0.01 + 2.41 = 30.77 ; net = -(13000 + 30.77)
def test_worked_buy_example():
    c = compute_charges(Side.BUY, 10, 1300.0, exchange="NSE")
    assert c.turnover == 1300.0 * 10
    assert c.brokerage == 13.00
    assert c.stt == 13.00
    assert c.stamp_duty == 1.95
    assert c.exchange_txn == 0.40
    assert c.sebi_fee == 0.01
    assert c.gst == 2.41
    assert c.dp_charge == 0.0          # no DP on buy
    assert c.total == 30.77
    assert c.net_cash_impact == -13030.77


# --- worked SELL example: 10 RELIANCE @ ₹1320, NSE -----------------------------
# turnover 13200; brokerage 13.20; STT 0.1% = 13.20; stamp 0 (sell); exch 0.41;
# SEBI 0.01; DP ₹20.00; GST 18%×(13.20+0.41+0.01+20.00)=6.05.
# total = 13.20 + 13.20 + 0.41 + 0.01 + 6.05 + 20.00 = 52.87
def test_worked_sell_example():
    c = compute_charges(Side.SELL, 10, 1320.0, exchange="NSE")
    assert c.brokerage == 13.20
    assert c.stt == 13.20
    assert c.stamp_duty == 0.0          # stamp is buy-only
    assert c.exchange_txn == 0.41
    assert c.sebi_fee == 0.01
    assert c.dp_charge == 20.00         # flat DP on sell
    assert c.gst == 6.05
    assert c.total == 52.87
    assert c.net_cash_impact == 13147.13


# --- component rules -----------------------------------------------------------

def test_stt_charged_both_sides():
    buy = compute_charges(Side.BUY, 100, 500.0)
    sell = compute_charges(Side.SELL, 100, 500.0)
    assert buy.stt > 0 and sell.stt > 0
    assert buy.stt == pytest.approx(sell.stt)  # same 0.1% rate, same turnover


def test_stamp_is_buy_only_and_dp_is_sell_only():
    buy = compute_charges(Side.BUY, 100, 500.0)
    sell = compute_charges(Side.SELL, 100, 500.0)
    assert buy.stamp_duty > 0 and sell.stamp_duty == 0.0
    assert sell.dp_charge > 0 and buy.dp_charge == 0.0


def test_gst_base_excludes_stt_and_stamp_on_buy():
    c = compute_charges(Side.BUY, 100, 500.0)   # dp = 0 on a buy
    expected_gst = round(0.18 * (c.brokerage + c.exchange_txn + c.sebi_fee), 2)
    assert c.gst == expected_gst
    # STT and stamp must NOT be in the GST base.
    assert c.gst < round(0.18 * (c.stt + c.stamp_duty), 2)


def test_gst_base_includes_dp_charge_on_sell():
    c = compute_charges(Side.SELL, 100, 500.0)
    expected_gst = round(0.18 * (c.brokerage + c.exchange_txn + c.sebi_fee + c.dp_charge), 2)
    assert c.gst == expected_gst
    assert c.dp_charge > 0  # DP is taxed


def test_bse_uses_bse_txn_rate():
    nse = compute_charges(Side.BUY, 1000, 1000.0, exchange="NSE")
    bse = compute_charges(Side.BUY, 1000, 1000.0, exchange="BSE")
    assert bse.exchange_txn > nse.exchange_txn  # BSE rate is higher in the default schedule


def test_default_brokerage_is_capped_at_20():
    big = compute_charges(Side.BUY, 1000, 1000.0)   # 0.1% of 10,00,000 = 1000 -> cap ₹20
    assert big.brokerage == 20.0


def test_brokerage_minimum_floor_applies_on_small_orders():
    small = compute_charges(Side.BUY, 1, 300.0)     # 0.1% of 300 = ₹0.30 -> floor ₹5
    assert small.brokerage == 5.0


def test_zero_brokerage_config_stays_zero():
    free = CostConfig(brokerage_rate=0.0, brokerage_max=0.0, brokerage_min=0.0)
    c = compute_charges(Side.BUY, 1, 300.0, cfg=free)
    assert c.brokerage == 0.0           # a truly-free broker isn't forced to the ₹5 floor


def test_brokerage_cap_applied_when_configured():
    cfg = CostConfig(brokerage_rate=0.0003, brokerage_max=20.0)  # 0.03% capped at ₹20
    big = compute_charges(Side.BUY, 1000, 1000.0, cfg=cfg)       # 0.03% of 10,00,000 = 300 -> cap 20
    assert big.brokerage == 20.0


def test_cost_drag_fraction():
    c = compute_charges(Side.SELL, 10, 1320.0)
    assert c.cost_drag_frac == pytest.approx(c.total / c.turnover)


def test_rejects_nonpositive_inputs():
    with pytest.raises(ValueError):
        compute_charges(Side.BUY, 0, 100.0)
    with pytest.raises(ValueError):
        compute_charges(Side.BUY, 10, 0.0)
