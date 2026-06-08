"""Task 1 — security master: lookup (fail-closed), tick/lot helpers, seed + I/O."""

from __future__ import annotations

import pytest

from execution.config import UNIVERSE
from execution.security_master import (
    Instrument,
    SecurityMaster,
    UnknownInstrument,
    default_universe_master,
    floor_to_lot,
    round_to_tick,
    validate_lot,
)

REC = {
    "symbol": "RELIANCE.NS", "exchange": "NSE", "angel_token": "2885",
    "isin": "INE002A01018", "lot_size": 1, "tick_size": 0.05, "tradable": True,
}


# --- tick / lot helpers --------------------------------------------------------

@pytest.mark.parametrize(
    "price,tick,expected",
    [(100.07, 0.05, 100.05),
     (100.03, 0.05, 100.05),
     (100.02, 0.05, 100.00),
     (100.00, 0.05, 100.00),
     (1299.99, 0.05, 1300.00),
     (123.456, 0.0, 123.456)],  # no grid -> unchanged
)
def test_round_to_tick(price, tick, expected):
    assert round_to_tick(price, tick) == pytest.approx(expected)


@pytest.mark.parametrize("qty,lot,expected", [(7, 1, 7), (7, 5, 5), (3, 5, 0), (10, 5, 10), (-4, 1, 0)])
def test_floor_to_lot(qty, lot, expected):
    assert floor_to_lot(qty, lot) == expected


@pytest.mark.parametrize(
    "qty,lot,ok",
    [(5, 5, True), (10, 5, True), (7, 5, False), (3, 1, True), (0, 1, False), (-1, 1, False)],
)
def test_validate_lot(qty, lot, ok):
    assert validate_lot(qty, lot) is ok


# --- lookup is fail-closed -----------------------------------------------------

def test_lookup_returns_instrument():
    sm = SecurityMaster.from_records([REC])
    inst = sm.lookup("RELIANCE.NS")
    assert isinstance(inst, Instrument)
    assert inst.exchange == "NSE" and inst.angel_token == "2885"
    assert inst.tick_size == 0.05 and inst.lot_size == 1 and inst.has_token


def test_unknown_symbol_raises_not_guesses():
    sm = SecurityMaster.from_records([REC])
    with pytest.raises(UnknownInstrument):
        sm.lookup("WIPRO.NS")
    assert "WIPRO.NS" not in sm
    assert "RELIANCE.NS" in sm


def test_instrument_is_frozen():
    inst = SecurityMaster.from_records([REC]).lookup("RELIANCE.NS")
    with pytest.raises(Exception):
        inst.tick_size = 0.10  # frozen dataclass


# --- universe seed -------------------------------------------------------------

def test_default_universe_master_covers_the_locked_12():
    sm = default_universe_master()
    assert len(sm) == 12
    assert set(sm.symbols()) == set(UNIVERSE)
    for sym in UNIVERSE:
        inst = sm.lookup(sym)
        assert inst.exchange == "NSE"
        assert inst.tick_size == 0.05 and inst.lot_size == 1 and inst.board_lot == 1
        assert inst.tradable
        assert not inst.has_token  # tokens must be refreshed from Angel (fail-closed until then)


# --- file round-trip -----------------------------------------------------------

def test_save_and_load_round_trip(tmp_path):
    sm = SecurityMaster.from_records([REC, {**REC, "symbol": "TCS.NS", "angel_token": "11536"}])
    path = tmp_path / "secmaster.json"
    sm.save(str(path))
    loaded = SecurityMaster.from_file(str(path))
    assert set(loaded.symbols()) == {"RELIANCE.NS", "TCS.NS"}
    assert loaded.lookup("TCS.NS").angel_token == "11536"
