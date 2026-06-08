"""Task 4 — portfolio accounting, T+1 settlement (qty + cash), MTM equity."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from execution.contracts import Fill, Side
from execution.costs import compute_charges
from execution.portfolio import Portfolio

TUE = date(2026, 6, 9)    # trade day T
WED = date(2026, 6, 10)   # T+1 (one trading day later; holidays=[] -> weekends only)
THU = date(2026, 6, 11)   # T+2
TS = datetime(2026, 6, 9, 10, 30)
NO_HOLIDAYS: list[str] = []   # deterministic: only weekends are non-trading


def _buy(sym, qty, price):
    f = Fill("o", sym, Side.BUY, qty, price, TS, is_partial=False)
    return f, compute_charges(Side.BUY, qty, price)


def _sell(sym, qty, price):
    f = Fill("o", sym, Side.SELL, qty, price, TS, is_partial=False)
    return f, compute_charges(Side.SELL, qty, price)


def _pf(cash=1_000_000.0):
    return Portfolio(cash, settlement_days=1, holidays=NO_HOLIDAYS)


# --- buy: unsettled at T, settled at T+1 ---------------------------------------

def test_buy_is_unsettled_same_day():
    pf = _pf()
    fill, ch = _buy("RELIANCE.NS", 10, 1300.0)
    pf.apply_fill(fill, ch, trade_date=TUE)
    assert pf.qty("RELIANCE.NS") == 10
    assert pf.settled_qty("RELIANCE.NS") == 0          # T+1 settlement: not sellable today
    assert pf.settled_cash == round(1_000_000.0 - 13030.77, 2)
    assert pf.avg_price("RELIANCE.NS") == pytest.approx(1303.077)  # cost basis incl. buy charges


def test_settle_promotes_to_sellable_on_t_plus_1():
    pf = _pf()
    fill, ch = _buy("RELIANCE.NS", 10, 1300.0)
    pf.apply_fill(fill, ch, trade_date=TUE)
    pf.settle(WED)
    assert pf.settled_qty("RELIANCE.NS") == 10


def test_selling_unsettled_qty_raises_defensively():
    pf = _pf()
    bf, bc = _buy("RELIANCE.NS", 10, 1300.0)
    pf.apply_fill(bf, bc, trade_date=TUE)
    sf, sc = _sell("RELIANCE.NS", 10, 1320.0)
    with pytest.raises(ValueError):
        pf.apply_fill(sf, sc, trade_date=TUE)          # nothing settled yet -> no BTST


# --- realized P&L on a settled round-trip --------------------------------------

def test_realized_pnl_net_of_costs_on_round_trip():
    pf = _pf()
    bf, bc = _buy("RELIANCE.NS", 10, 1300.0)
    pf.apply_fill(bf, bc, trade_date=TUE)
    pf.settle(WED)
    sf, sc = _sell("RELIANCE.NS", 10, 1320.0)
    pf.apply_fill(sf, sc, trade_date=WED)
    # realized = sell proceeds (13147.13) - cost basis (13030.77)
    assert pf.realized_pnl == pytest.approx(116.36, abs=0.01)
    assert pf.qty("RELIANCE.NS") == 0
    assert pf.position("RELIANCE.NS") is None          # flat -> not a held position


# --- settled vs unsettled cash after a sell ------------------------------------

def test_sell_proceeds_are_unsettled_until_t_plus_1():
    pf = _pf()
    bf, bc = _buy("RELIANCE.NS", 10, 1300.0)
    pf.apply_fill(bf, bc, trade_date=TUE)
    pf.settle(WED)
    settled_before = pf.settled_cash
    sf, sc = _sell("RELIANCE.NS", 10, 1320.0)
    pf.apply_fill(sf, sc, trade_date=WED)
    # proceeds do NOT boost buying power yet
    assert pf.unsettled_cash() == pytest.approx(13147.13, abs=0.01)
    assert pf.buying_power() == settled_before          # unchanged until settlement
    pf.settle(THU)
    assert pf.buying_power() == pytest.approx(settled_before + 13147.13, abs=0.01)
    assert pf.unsettled_cash() == 0.0


# --- average price on adds ------------------------------------------------------

def test_avg_price_on_add():
    pf = _pf()
    f1, c1 = _buy("RELIANCE.NS", 10, 1300.0)
    f2, c2 = _buy("RELIANCE.NS", 10, 1400.0)
    pf.apply_fill(f1, c1, trade_date=TUE)
    pf.apply_fill(f2, c2, trade_date=TUE)
    assert pf.qty("RELIANCE.NS") == 20
    # cost-weighted average incl. buy charges: (13030.77 + 14033.14) / 20
    assert pf.avg_price("RELIANCE.NS") == pytest.approx(1353.1955, abs=0.001)


# --- MTM equity ----------------------------------------------------------------

def test_mark_to_market_equity_and_unrealized():
    pf = _pf(100_000.0)
    f, c = _buy("RELIANCE.NS", 10, 1300.0)
    pf.apply_fill(f, c, trade_date=TUE)
    pf.mark_to_market({"RELIANCE.NS": 1310.0})
    assert pf.unrealized_pnl == pytest.approx(1310.0 * 10 - 13030.77, abs=0.01)  # 69.23
    # equity = settled cash (86969.23) + holdings (13100) ; no unsettled cash
    assert pf.equity() == pytest.approx(86969.23 + 13100.0, abs=0.01)
    assert pf.realized_pnl == 0.0


# --- exposure queries used by the gates ----------------------------------------

def test_held_symbols_and_open_count():
    pf = _pf()
    for sym, px in [("RELIANCE.NS", 1300.0), ("INFY.NS", 1500.0)]:
        f, c = _buy(sym, 10, px)
        pf.apply_fill(f, c, trade_date=TUE)
    assert set(pf.held_symbols()) == {"RELIANCE.NS", "INFY.NS"}
    assert pf.open_position_count() == 2


def test_partial_fill_uses_filled_qty_only():
    pf = _pf()
    # a partial fill of 4 (not the requested 10) must only book 4 shares
    f = Fill("o", "RELIANCE.NS", Side.BUY, 4, 1300.0, TS, is_partial=True)
    pf.apply_fill(f, compute_charges(Side.BUY, 4, 1300.0), trade_date=TUE)
    assert pf.qty("RELIANCE.NS") == 4
