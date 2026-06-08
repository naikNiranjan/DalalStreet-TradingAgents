"""Locked Phase 3 execution defaults (spine v2, 2026-06-08) in one typed place.

Everything the risk layer, sizing, paper simulator, and cost model parameterize on
lives here as a frozen dataclass so a run is reproducible and the reviewer can see the
whole policy at a glance. Values mirror
niranjan_docs/12-phase3-execution-spine.md §"Recommended locked defaults v2".

These are deliberately *not* folded into the global ``tradingagents.default_config``
(which configures the LLM graph) — the execution policy is its own concern. Only
``nse_holidays`` is shared (the calendar reads it from the global config).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

# --- universe (spine v2.B): 12 large-caps, <=2 concurrently-held per sector ----
UNIVERSE: tuple[str, ...] = (
    "RELIANCE.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "INFY.NS",
    "TCS.NS",
    "LT.NS",
    "BHARTIARTL.NS",
    "ITC.NS",
    "HINDUNILVR.NS",
    "MARUTI.NS",
    "SUNPHARMA.NS",
)

# Coarse, hardcoded sector tags for the 12-name pool (spine v2.B). The pool has 3
# Financials (HDFCBANK/ICICIBANK/SBIN), so the <=2/sector cap binds there.
SECTOR_TAGS: dict[str, str] = {
    "RELIANCE.NS": "Energy",
    "HDFCBANK.NS": "Financials",
    "ICICIBANK.NS": "Financials",
    "SBIN.NS": "Financials",
    "INFY.NS": "IT",
    "TCS.NS": "IT",
    "LT.NS": "CapitalGoods",
    "BHARTIARTL.NS": "Telecom",
    "ITC.NS": "FMCG",
    "HINDUNILVR.NS": "FMCG",
    "MARUTI.NS": "Auto",
    "SUNPHARMA.NS": "Pharma",
}


@dataclass(frozen=True)
class CostConfig:
    """India CNC equity-delivery charge rates (fractions of turnover unless noted).

    Defaults track **Angel One's published equity-delivery schedule (2026)** so the
    ₹25k shadow book reflects real cost drag (per the reviewer's correction):
      * brokerage = lower of ₹20 or 0.10% of turnover, **minimum ₹5** per order;
      * NSE transaction charge 0.0030699% (BSE 0.00375%);
      * DP sell charge ₹20 per scrip/transaction (**plus GST**).
    GST (18%) applies to (brokerage + exchange txn + SEBI fee + **DP charge**). All
    overridable; a truly-free discount broker is brokerage_rate=max=min=0.
    """

    brokerage_rate: float = 0.001        # 0.10% delivery
    brokerage_max: float = 20.0          # capped at ₹20/order
    brokerage_min: float = 5.0           # floored at ₹5/order (when any brokerage applies)
    stt_buy: float = 0.001               # 0.10% delivery buy
    stt_sell: float = 0.001              # 0.10% delivery sell
    stamp_buy: float = 0.00015           # 0.015% buy only
    exchange_txn_nse: float = 0.000030699  # 0.0030699% NSE
    exchange_txn_bse: float = 0.0000375    # 0.00375% BSE
    sebi_turnover: float = 0.000001      # ₹10 per crore
    gst_rate: float = 0.18               # on (brokerage + exchange txn + SEBI + DP)
    dp_charge_per_sell_scrip: float = 20.0  # ₹20/scrip on SELL (+GST)


@dataclass(frozen=True)
class PaperConfig:
    """Paper fill-simulator parameters (spine Contract 5). Honest, not fill-at-LTP."""

    base_slippage_bps: float = 2.0       # bps added beyond the touch, before size scaling
    size_impact_bps: float = 5.0         # extra bps when order qty == full touch qty (linear)
    tick_default: float = 0.05           # fallback tick when instrument unknown (cash equity)


@dataclass(frozen=True)
class ExecutionConfig:
    """The full locked Phase 3 policy."""

    # A — capital (dual paper books)
    signal_book_capital: float = 1_000_000.0   # ₹10,00,000 signal-quality book
    shadow_book_capital: float = 25_000.0      # ₹25,000 go-live-size shadow book

    # B — universe / exposure
    sector_cap: int = 2                        # <=2 concurrently-held names per sector
    max_open_positions_init: int = 5
    max_open_positions_hard: int = 8

    # sizing / position cap
    position_cap_init: float = 0.10            # 10% of equity per position (init)
    position_cap_hard: float = 0.15            # 15% hard max
    reduce_fraction: float = 0.50              # REDUCE -> 50% of current position

    # C — confidence (entry-side only)
    confidence_floor: float = 0.60             # entry floor on STRONG_BUY / BUY only

    # deadband (anti-churn). EXIT bypasses the deadband.
    deadband_equity_frac: float = 0.02         # act only if |delta| >= 2% of equity
    deadband_min_notional: float = 5_000.0     # and notional >= ₹5,000

    # risk caps
    daily_loss_limit: float = 0.03             # 3% of equity -> block new buys

    # spread gate (fraction of mid)
    spread_hard: float = 0.0005                # 0.05% hard block (entries)
    spread_warn: float = 0.0020                # 0.05-0.20% warn band

    # freshness clocks
    stale_quote_seconds: int = 60              # clock 2 — execution quote age at fill
    ohlcv_max_age_hours: int = 36              # clock 1 critical — daily OHLCV <= last session
    security_master_max_age_hours: int = 24    # clock 1 critical
    news_social_max_age_hours: int = 48        # clock 1 degradable (warn)
    fundamentals_max_age_hours: int = 72       # clock 1 degradable — fetch-time, ~2 trading days

    # settlement (T+1; no BTST)
    settlement_days: int = 1
    allow_btst: bool = False

    # execution window (IST) — analysis anytime, execution pass only inside this band
    exec_window_start: time = time(9, 20)
    exec_window_end: time = time(15, 25)

    # nested sub-policies
    costs: CostConfig = field(default_factory=CostConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)


# A module-level default the rest of the package imports when no override is passed.
DEFAULT_EXECUTION_CONFIG = ExecutionConfig()


# ---------------------------------------------------------------------------
# Book-specific sizing configs (integration slice doc 14 §2a, reviewer-locked)
# ---------------------------------------------------------------------------
#
# The committed defaults are correct for the ₹10L signal book but structurally
# block the ₹25k shadow book: 10-15% of ₹25k = ₹2,500-3,750, both below the
# ₹5,000 absolute deadband -> the shadow book never trades and the go-live-size
# gate measures nothing. The shadow config raises the caps and drops the absolute
# deadband floor so whole-share rounding (sub_economic_skipped) is the real
# small-capital floor. Only these four params differ from the signal book.


def signal_book_config() -> ExecutionConfig:
    """₹10,00,000 signal-quality book — the committed defaults are its policy."""
    return ExecutionConfig()


def shadow_book_config() -> ExecutionConfig:
    """₹25,000 go-live-size shadow book — concentration is unavoidable and the point."""
    return ExecutionConfig(
        position_cap_init=0.35,        # afford ≥1 share of an expensive name (1 TCS ≈ 15% of ₹25k)
        position_cap_hard=0.40,        # concentration is unavoidable at ₹25k
        deadband_min_notional=0.0,     # the ₹5k floor is 20% of ₹25k; let whole-share rounding be the floor
        # deadband_equity_frac stays 0.02 (2% of ₹25k = ₹500 anti-churn floor, still relative)
    )


@dataclass(frozen=True)
class BookSpec:
    """One paper book: a display name, its starting capital, and its sizing policy."""

    name: str
    capital: float
    config: ExecutionConfig


def default_books() -> tuple[BookSpec, ...]:
    """The locked dual-book setup: ₹10L signal book + ₹25k go-live shadow book."""
    sig = signal_book_config()
    sh = shadow_book_config()
    return (
        BookSpec("signal", sig.signal_book_capital, sig),
        BookSpec("shadow", sh.shadow_book_capital, sh),
    )
