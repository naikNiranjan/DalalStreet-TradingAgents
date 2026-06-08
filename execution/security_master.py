"""Security master (spine Contract 2) — symbol -> immutable tradable facts.

Maps a canonical symbol to the facts every downstream step needs (Angel token,
ISIN, tick, lot, exchange, tradability). Sourced from Angel's ``searchScrip`` scrip
data + a cached file refreshed daily. Fail-closed: an unknown symbol raises
``UnknownInstrument`` — the router blocks rather than guessing a token/lot.

The live ``refresh_from_angel`` reuses the SmartAPI session already wired in
:mod:`tradingagents.dataflows.angel_one` (no second login path). It is never called
in unit tests; tests build a :class:`SecurityMaster` from explicit records.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from .config import UNIVERSE

__all__ = [
    "Instrument",
    "UnknownInstrument",
    "SecurityMaster",
    "round_to_tick",
    "floor_to_lot",
    "validate_lot",
    "default_universe_master",
]


class UnknownInstrument(KeyError):
    """Raised when a symbol is not in the security master (fail-closed: no guess)."""


@dataclass(frozen=True)
class Instrument:
    """Immutable per-symbol facts. Shape locked in spine Contract 2."""

    symbol: str          # "RELIANCE.NS"
    exchange: str        # "NSE" | "BSE"
    angel_token: str     # SmartAPI symboltoken ("" until refreshed from Angel)
    isin: str
    lot_size: int        # 1 for cash equity; matters for F&O later
    tick_size: float     # e.g. 0.05 — orders must round to this
    board_lot: int = 1
    tradable: bool = True  # ASM/GSM/suspension -> False blocks at the gate

    @property
    def has_token(self) -> bool:
        return bool(self.angel_token)


# ---------------------------------------------------------------------------
# Tick / lot helpers (used by sizing, orders, and the paper fill simulator)
# ---------------------------------------------------------------------------


def round_to_tick(price: float, tick: float) -> float:
    """Round ``price`` to the nearest multiple of ``tick`` (NSE tick = 0.05).

    A non-positive tick is treated as "no tick grid" and the price is returned
    unchanged. The extra ``round(_, 4)`` clears binary-float dust (0.05 is not exact).
    """
    if tick is None or tick <= 0:
        return price
    return round(round(price / tick) * tick, 4)


def floor_to_lot(qty: int, lot: int) -> int:
    """Largest multiple of ``lot`` not exceeding ``qty`` (>=0)."""
    if lot is None or lot <= 1:
        return max(0, int(qty))
    return (max(0, int(qty)) // lot) * lot


def validate_lot(qty: int, lot: int) -> bool:
    """True when ``qty`` is a positive whole multiple of ``lot``."""
    if qty <= 0:
        return False
    if lot is None or lot <= 1:
        return True
    return qty % lot == 0


# ---------------------------------------------------------------------------
# SecurityMaster
# ---------------------------------------------------------------------------


class SecurityMaster:
    """In-memory registry of :class:`Instrument`, loadable from records / file / Angel."""

    def __init__(self, instruments: Optional[Iterable[Instrument]] = None):
        self._by_symbol: dict[str, Instrument] = {}
        for inst in instruments or ():
            self._by_symbol[inst.symbol] = inst

    # --- construction ------------------------------------------------------

    @classmethod
    def from_records(cls, records: Iterable[dict]) -> "SecurityMaster":
        """Build from a list of dicts (e.g. parsed from the daily cache file)."""
        insts = []
        for r in records:
            insts.append(
                Instrument(
                    symbol=r["symbol"],
                    exchange=r["exchange"],
                    angel_token=str(r.get("angel_token", "")),
                    isin=str(r.get("isin", "")),
                    lot_size=int(r.get("lot_size", 1)),
                    tick_size=float(r.get("tick_size", 0.05)),
                    board_lot=int(r.get("board_lot", 1)),
                    tradable=bool(r.get("tradable", True)),
                )
            )
        return cls(insts)

    @classmethod
    def from_file(cls, path: str) -> "SecurityMaster":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_records(json.load(fh))

    def to_records(self) -> list[dict]:
        return [asdict(i) for i in self._by_symbol.values()]

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_records(), fh, indent=2, sort_keys=True)

    # --- lookup ------------------------------------------------------------

    def lookup(self, symbol: str) -> Instrument:
        """Return the :class:`Instrument` for ``symbol`` or raise ``UnknownInstrument``."""
        try:
            return self._by_symbol[symbol]
        except KeyError as exc:
            raise UnknownInstrument(
                f"{symbol!r} is not in the security master — refusing to trade an "
                "instrument with no verified token/lot/tick (fail-closed)."
            ) from exc

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._by_symbol

    def symbols(self) -> list[str]:
        return list(self._by_symbol.keys())

    def __len__(self) -> int:
        return len(self._by_symbol)

    # --- live refresh (not exercised in unit tests) ------------------------

    def refresh_from_angel(self, symbols: Iterable[str], *, save_to: Optional[str] = None) -> None:
        """Populate angel_token (and exchange) for ``symbols`` via SmartAPI searchScrip.

        Reuses the cached login + token resolution in ``dataflows.angel_one``. Cash
        equity tick/lot are standard (0.05 / 1). Live call — never used in CI.
        """
        from tradingagents.dataflows.angel_one import (
            _exchange_for,
            _get_client,
            _resolve_token,
        )

        client = _get_client()
        for symbol in symbols:
            exchange, base = _exchange_for(symbol)
            token = _resolve_token(client, base, exchange)
            self._by_symbol[symbol] = Instrument(
                symbol=symbol,
                exchange=exchange,
                angel_token=str(token),
                isin=self._by_symbol.get(symbol, _BLANK).isin if symbol in self._by_symbol else "",
                lot_size=1,
                tick_size=0.05,
                board_lot=1,
                tradable=True,
            )
        if save_to:
            self.save(save_to)


_BLANK = Instrument(symbol="", exchange="", angel_token="", isin="", lot_size=1, tick_size=0.05)


def default_universe_master() -> SecurityMaster:
    """Seed a SecurityMaster for the locked 12-name universe (cash-equity defaults).

    Tokens/ISINs are intentionally left blank — they must be filled by
    :meth:`SecurityMaster.refresh_from_angel` (or a daily cache file) before a live
    quote can be fetched. A blank token fails closed at quote time (quote_fetch_failed
    on entry / intended_exit_unfilled on exit), never a guess. Tick (0.05) and lot (1)
    are the NSE cash-equity standard.
    """
    return SecurityMaster(
        Instrument(
            symbol=sym,
            exchange="NSE",
            angel_token="",
            isin="",
            lot_size=1,
            tick_size=0.05,
            board_lot=1,
            tradable=True,
        )
        for sym in UNIVERSE
    )
