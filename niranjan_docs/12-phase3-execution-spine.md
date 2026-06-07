# Phase 3 — Execution / Paper-Trading Spine (PLAN — lock before code)

> Status: **PLAN — defaults LOCKED (v2, 2026-06-08), awaiting reviewer pass.** No code until the
> contracts below + [Recommended locked defaults v2](#recommended-locked-defaults-v2--locked-2026-06-08)
> are signed off. v2 folds the original reviewer defaults + the user's refinements + a 4-lens
> review panel's high-severity findings; it **supersedes the open questions** at the bottom.
> Builds on: [02-architecture](./02-architecture.md) · [08-roadmap §Phase 3](./08-implementation-roadmap.md)
> · [11-success-metrics](./11-success-metrics.md) · [07-SEBI](./07-sebi-compliance.md).
> Phase 2 (India data layer) is committed + pushed on `feature/phase2-india-data`.

## Why this is a plan, not a patch

Phase 3 changes the system from an **analysis agent** (emits a rating) into a **paper-trading
execution spine** (acts on a rating, even if simulated). That is the first phase where a bug can
"place" an order, mis-size a position, or hide a loss. So we **lock the typed contracts first** —
`SignalDecision`, security master, risk-gate result, paper fills, audit record, daily report —
then implement against them. Default posture everywhere: **fail-closed** (when in doubt, don't
trade). The LLM never touches money or bypasses a gate; everything between the brain and the
(simulated) broker is pure, deterministic, unit-tested code.

Scope guardrails (from the locked roadmap): **cash equity only**, **once-daily cadence**, **CNC
delivery**, **paper mode is the hard default**. F&O and live mode are deferred tracks.

---

## Package layout (locked in [02](./02-architecture.md), top-level beside `tradingagents/`)

```
execution/
  contracts.py        # SignalDecision, Order, Quote, Position, Fill, GateResult, enums
  security_master.py  # symbol -> {token, isin, lot, tick, exchange, board_lot, tradable}
  brokers/
    base.py           # Broker Protocol
    paper.py          # PaperBroker — realistic fill simulator
  risk/
    guards.py         # fail-closed MVP gates (this phase)
    sizing.py         # deterministic position sizing
  costs/
    india_costs.py    # STT, stamp, GST, brokerage, SEBI, exchange, DP -> net
  portfolio.py        # positions, cash, mark-to-market, realized/unrealized P&L
  router.py           # SignalDecision -> intent -> risk -> order -> fill -> audit
  audit.py            # append-only, hash-chained decision->order->fill trail
  report.py           # daily P&L + metrics report (feeds 11-success-metrics)
```

`tests/execution/` mirrors this. Everything is pure-Python and offline-testable except the live
adapters (not built this phase).

---

## Contract 1 — `SignalDecision` (analysis engine → execution)

The single typed handoff. The graph (Phase 1/2) produces this; execution consumes **only** this —
it never reads the LLM's free-text reports for decisions.

```python
class Action(str, Enum):        # maps from the existing 5-tier rating
    STRONG_BUY = "strong_buy"   # Buy
    BUY        = "buy"          # Overweight
    HOLD       = "hold"         # Hold
    REDUCE     = "reduce"       # Underweight
    EXIT       = "exit"         # Sell

@dataclass(frozen=True)
class SignalDecision:
    symbol: str                 # canonical, e.g. "RELIANCE.NS"
    action: Action
    confidence: float           # 0..1, DERIVED IN CODE from a categorical conviction (see v2.C)
    as_of: datetime             # IST; the decision timestamp (look-ahead boundary)
    rating_raw: str             # original 5-tier label, for audit
    rationale_digest: str       # short hash/snippet of the report (audit only, not logic)
    data_freshness: dict        # {source: last_updated_ts} — feeds the stale-data gate (REQUIRED)
    horizon_days: int = 1       # once-daily cadence default
    schema_version: int = 1     # defaulted fields MUST come last (valid dataclass shape)
```

**Decisions locked in [v2](#recommended-locked-defaults-v2--locked-2026-06-08):** (a) rating→Action
map above; (b) `confidence` is **derived in code** from a constrained categorical conviction added to
`PortfolioDecision` (high/med/low → 0.85/0.60/0.35; missing→fail-closed HOLD) — **not** a free-emitted
LLM float; (c) target-weight is **not** in the signal — sizing is deterministic in `risk/sizing.py`
(the LLM proposes direction + conviction, code decides quantity).

---

## Contract 2 — Security master (`security_master.py`)

Maps a tradable symbol to the immutable facts every downstream step needs. Sourced from Angel's
scrip data (reuse the SmartAPI `searchScrip` already wired in `dataflows/angel_one.py`) + a cached
file refreshed daily; ISIN cross-checked.

```python
@dataclass(frozen=True)
class Instrument:
    symbol: str          # "RELIANCE.NS"
    exchange: str        # "NSE" | "BSE"
    angel_token: str     # SmartAPI symboltoken
    isin: str
    lot_size: int        # 1 for cash equity; matters for F&O later
    tick_size: float     # e.g. 0.05 — orders must round to this
    board_lot: int = 1
    tradable: bool = True  # ASM/GSM/suspension -> False blocks at the gate
```

`lookup(symbol) -> Instrument` raises a typed `UnknownInstrument` (fail-closed: no instrument →
the router blocks, never guesses a token/lot). Tick/lot are used to **round + validate** every
order before it reaches a broker.

---

## Contract 3 — Broker data types + `Broker` Protocol

Matches the interface already sketched in [02](./02-architecture.md). Concrete types:

```python
# (from dataclasses import dataclass, field;  from uuid import uuid4)
class Side(str, Enum):        BUY = "buy"; SELL = "sell"
class OrderType(str, Enum):   MARKET = "market"           # LIMIT deferred to Phase 5 (v2)
class TimeInForce(str, Enum): IOC = "ioc"                 # DAY / resting deferred to Phase 5 (v2)
class OrderState(str, Enum):  NEW="new"; ACCEPTED="accepted"; PARTIAL="partial"
                              FILLED="filled"; REJECTED="rejected"; CANCELLED="cancelled"

@dataclass(frozen=True)
class Quote:                 # snapshot used by the fill simulator + spread gate
    symbol: str; ltp: float; bid: float; ask: float; ts: datetime
    bid_qty: int; ask_qty: int

@dataclass(frozen=True)
class Order:                                              # Phase 3 = MARKET + IOC only
    symbol: str; side: Side; qty: int                    # required fields first
    order_type: OrderType = OrderType.MARKET
    tif: TimeInForce = TimeInForce.IOC
    product: str = "CNC"
    client_oid: str = field(default_factory=lambda: uuid4().hex)   # idempotency key

@dataclass(frozen=True)
class Fill:
    order_id: str; symbol: str; side: Side; qty: int
    price: float; ts: datetime; is_partial: bool

@dataclass(frozen=True)
class Position:
    symbol: str; qty: int; avg_price: float; ltp: float
    realized_pnl: float; unrealized_pnl: float

class Broker(Protocol):
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...  # ONE batched FULL call
    def get_quote(self, symbol: str) -> Quote: ...           # single-symbol convenience
    def place_order(self, order: Order) -> str: ...          # returns order_id
    def get_order_status(self, order_id: str) -> OrderState: ...
    def cancel_order(self, order_id: str) -> None: ...
    def get_positions(self) -> list[Position]: ...
    def is_market_open(self) -> bool: ...                    # uses india_calendar (2E)
```

`PaperBroker` implements this now; `AngelOneBroker`/`DhanBroker` (Phase 6) implement the same
Protocol. The router holds a `Broker`, not a concrete class — **mode switch = which is injected**.

> **Quote source (locked, v2):** both quote methods are backed by Angel **`getMarketData("FULL")`** —
> the only call that returns bid/ask depth (`ltpData` is LTP-only and **forbidden** for fills). The
> execution pass uses **`get_quotes(universe)` = ONE FULL call** for all names (`get_quote` is the
> single-symbol convenience, never a per-symbol loop). This quote adapter is built **in Phase 3**
> even though order placement stays paper; a failed/None response is a `quote_fetch_failed` block,
> never an LTP fallback. **Order primitive (locked, v2): `MARKET` + `IOC` only** —
> `LIMIT`/`OrderType.LIMIT` and resting-order lifecycle are deferred to Phase 5.

---

## Contract 4 — Fail-closed risk gates (`risk/guards.py`) — the safety core

Every intended order runs the **ordered** gate chain. Default is **block**: if a gate's
precondition can't be *verified*, it vetoes. Gates are pure functions of `(Order, context)`.

```python
@dataclass(frozen=True)
class GateResult:
    allowed: bool
    gate: str           # which gate decided
    reason: str         # human-readable, goes to audit
    severity: str       # "block" | "warn"
```

**MVP gate set shipped this phase** (the hard subset of [02](./02-architecture.md); the rest is
Phase 5):

| # | Gate | Blocks when |
|---|------|-------------|
| 1 | `kill_switch` | global kill flag set (manual/auto) |
| 2 | `auth_valid` | no/expired broker session (paper: always ok; structure ready for live) |
| 3 | `market_open` | outside NSE hours / holiday (via 2E `india_calendar`) |
| 4 | `data_fresh` | a **critical** analysis source (`daily OHLCV` / `security master`) stale-or-missing → **block**; **degradable** (news/social/fundamentals) → **warn**. *Quote* freshness is the separate clock-2 `stale_quote` **entry-quality** gate (entry block / exit `intended_exit_unfilled`) — see v2 Freshness |
| 5 | `instrument_tradable` | security master says `tradable=False` (ASM/GSM/suspended) |
| 6 | `spread_sane` / `no_book` | spread > 0.05% **(entries; warn for exits — see below)**; or empty/zero-qty touch |
| 7 | `daily_loss_limit` | realized+unrealized day loss ≥ cap → block all new buys |
| 8 | `position_size_cap` | order would exceed per-position % of equity |
| 9 | `max_open_positions` | already at the position-count cap |
| 10 | `audit_writable` | audit log not append-able (no order without a trail) |
| 11 | `buying_power` / `settled_cash` | BUY net cost (incl. est. charges) > available **settled** cash (CNC = no leverage) |
| 12 | `no_BTST` / `settled_qty` | SELL/REDUCE of **unsettled** stock (bought < T+1 ago); `allow_btst` default **False** |
| 13 | `sector_cap` | would hold > 2 concurrent names in one sector (see v2.B) |

Evaluation: **all gates run**, results collected; **any `block` ⇒ order vetoed**, the full list is
audited (we log *why*, every gate, not just the first failure). A blocked order is a recorded
event, never a silent drop. Caps come from config (defaults locked in
[memory/11-success-metrics](./11-success-metrics.md): ≤15%/position, ≤3% daily loss, ≤8 positions).

> **Gate classes & exit asymmetry (LOCKED, v2 — the safety crux):** gates fall into two classes, and
> exits bypass **only** the entry-quality class.
> - **(i) Universal safety/compliance gates — block ENTRIES *and* EXITS:** `kill_switch`,
>   `market_open`, `instrument_tradable`, `auth_valid`, `audit_writable`, and (for any SELL) `no_BTST`
>   / `settled_qty`. You may not transact at all when these fail — not even to de-risk (e.g. you can't
>   "exit" stock that isn't settled, trade a suspended name, or act with the market closed / kill
>   switch thrown / broker session expired / no writable audit).
>   - *Phase 3 paper note on `auth_valid`:* order-placement auth is **always OK** (paper fills need no
>     live broker session). The only live auth dependency in Phase 3 is the Angel **quote** session;
>     its expiry surfaces as **`quote_fetch_failed`** — a no-usable-quote condition that follows the
>     **entry-quality** rule (entry → block, exit → `intended_exit_unfilled`), NOT a universal block.
> - **(ii) Entry-quality gates — ENTRIES only:** the confidence floor, `spread_sane`, `no_book`,
>   `no bid/ask`, `stale_quote`. For **REDUCE / EXIT** these **never veto** the intent. A wide
>   `spread_sane` on an exit downgrades to `warn` (accept the worse fill). When there is genuinely
>   no fillable market on an exit (`no_book` / `no bid/ask` / `stale_quote`), the exit is recorded as
>   **`intended_exit_unfilled`** (NOT a normal `REJECTED`, never silently assumed closed) so the
>   trapped position stays visible.
> - **Size/exposure gates** (`daily_loss_limit`, `position_size_cap`, `max_open_positions`,
>   `sector_cap`, `buying_power`) are entry-side by construction — exits reduce risk and free cash,
>   so they don't apply to exits.
>
> The "safe" default (HOLD) is the *unsafe* action when you've been told to get out — hence exits are
> never suppressed by the entry-quality class, but are still bound by universal safety/compliance.

---

## Contract 5 — Paper fill simulator (`brokers/paper.py`) — honest, not fill-at-LTP

A fill-at-LTP simulator makes paper P&L a lie. The model, against the live `Quote`:

- **Spread crossing:** BUY fills near `ask`, SELL near `bid` (not `ltp`).
- **Slippage:** add `slippage_bps` on top, scaled by order size vs `bid_qty/ask_qty`.
- **Partial fills (IOC):** if `qty > available_qty`, fill the available part `PARTIAL`; the remainder
  **expires** (IOC — never rests, per v2). On an EXIT, an unfilled remainder is reported as
  `intended_exit_unfilled`.
- **Rejection vs unfilled (side-aware):** on an **ENTRY**, `no_book` / zero liquidity / stale quote
  → `REJECTED`. On an **EXIT / REDUCE** the same no-fillable-market conditions are **not** a normal
  rejection — they are recorded as **`intended_exit_unfilled`** (the de-risk couldn't execute and
  must stay visible). No LIMIT-price rejection exists in Phase 3 (primitive is MARKET).
- **Tick/lot:** fill price rounded to `tick_size`; qty validated against `lot_size`.

All parameters (`slippage_bps`, `spread_model`, `reject_rules`, `partial_policy`) are config, so the
simulator is tunable and **every branch is unit-tested** with synthetic quotes. Output (side-aware):
a `Fill`, a `REJECTED` record (**entries only**), or an `intended_exit_unfilled` record (**exits**)
that portfolio + cost model + report consume.

---

## Contract 6 — India cost model (`costs/india_costs.py`)

Already specified in roadmap/architecture; lock the components for CNC equity delivery: **STT**,
**stamp duty**, **GST**, **SEBI turnover fee**, **exchange txn charge**, **brokerage** (₹0 for
delivery on most discount brokers, configurable), **DP charges** on sell. Input = `Fill`, output =
itemized charges + net cash impact. Pure math, table-driven, fully tested against known examples.

---

## Contract 7 — Audit log (`audit.py`) — immutable, hash-chained

Append-only JSONL; each record carries `prev_hash` + `hash(record)` so tampering/gaps are
detectable. One record per stage of one decision's lifecycle:

```python
@dataclass(frozen=True)
class AuditRecord:
    ts: datetime; run_id: str; symbol: str
    stage: str            # "signal" | "gate" | "order" | "fill" | "reject" | "block"
    payload: dict         # the relevant contract object, serialized
    prev_hash: str; hash: str
```

Invariant enforced by gate #10: **no order is placed without its preceding audit records
written.** The daily report and the success-metrics audit (100% coverage requirement) read from
this log.

---

## Contract 8 — Daily report (`report.py`) — feeds the paper→live gate

Ties directly to [11-success-metrics](./11-success-metrics.md). Per session:

- Decisions made, orders attempted, blocked (with gate reasons), filled (incl. partials/rejects).
- Realized + unrealized P&L **net of the India cost model**; equity curve point.
- Running counters for the go-live gate: sessions, trades, max drawdown, daily-loss-limit
  breaches (must be 0 trades after a hit), stale-data trades (must be 0), audit coverage (100%).
- Kill-switch drill log.

Output: a Markdown report + a machine-readable JSON line per session for trend tracking.

---

## Recommended locked defaults v2 — LOCKED 2026-06-08

> Supersedes the [open questions](#open-questions--resolved-in-v2-above) below. Folds the reviewer's
> proposed defaults + the user's refinements + the 4-lens review panel's high-severity findings.
> **This is the contract the Phase 3 implementation builds against.**

### A. Capital — dual-track paper books
- **Signal-quality book: ₹10,00,000** — tests strategy quality with room to diversify.
- **Go-live-size shadow book: ₹25,000** — same signals, run in parallel; tests survival under real
  small-capital constraints (whole-share sizing, fixed costs, idle cash).
- **Paper→live gate requires BOTH books to pass**, and the shadow book must show **positive net
  expectancy after India costs**. The daily report (Contract 8) computes **cost-drag %** and
  per-trade fixed-cost burden **for each book** (at ₹25k, a single share of TCS ~₹3,850 / ICICIBANK
  ~₹1,250 already dominates a position — fidelity to live depends on showing this survives).

### B. Universe — 12 large-caps, ≤2 concurrently-held per sector
`RELIANCE · HDFCBANK · ICICIBANK · SBIN · INFY · TCS · LT · BHARTIARTL · ITC · HINDUNILVR · MARUTI · SUNPHARMA` (all `.NS`).
- **Sector cap = ≤2 concurrently-held names per sector** (gate #13 / sizing). **Pool ≠ portfolio:**
  12 candidates, ≤2 per sector held at once.
- ⚠️ The pool has **3 Financials** (HDFCBANK, ICICIBANK, SBIN), so the ≤2/sector cap **binds** there
  — at most 2 of the 3 banks may be held simultaneously. Coarse sector tags (Financials / IT / FMCG /
  Energy / Capital-Goods / Telecom / Auto / Pharma) are hardcoded for these 12 for Phase 3.
- `max_open_positions` stays **5 (init) / 8 (hard max)**; the 12-name pool gives rotation + diversity
  while keeping deployment conservative. The report measures **return-on-deployed-capital** (not just
  total-equity) so idle cash doesn't flatter drawdown or dilute expectancy.

### C. Confidence — derived in code, entry-side only
- **Add `confidence: float` to `PortfolioDecision`.** It is **not** free-emitted by the LLM: the PM
  decision emits a **constrained categorical conviction** (`low|med|high`, the same pattern analyst
  reports already use), and **code maps** it — **high→0.85, med→0.60, low→0.35**.
- **Missing / unparseable / NaN / out-of-[0,1] → fail-closed:** force **HOLD** on entries; audit.
- **Entry floor `confidence ≥ 0.60` applies to STRONG_BUY / BUY only.** REDUCE / EXIT are **never**
  suppressed or down-scaled by confidence (HOLD is a no-op).
- This is **uncalibrated proxy conviction, not probability** — the floor/scaling are heuristics to be
  recalibrated against realized hit-rate in Phase 5.

### Sizing — absolute target, `tier × confidence × cap`
- `target_notional = equity × position_cap × tier_mult[action] × confidence`
  - `tier_mult`: **STRONG_BUY 1.0 · BUY 0.6 · HOLD 0**.
  - `position_cap` = **10% (init) / 15% (hard max)**.
  - **`equity` = total mark-to-market value (cash + positions)**, snapshotted **once per session**.
- **Targets are ABSOLUTE** — router computes `delta = target − current` (BUY +delta / SELL −delta).
  **EXIT → target 0** (full close). **REDUCE → target = 50% of current position.**
- **No-trade deadband:** act only if `|delta| ≥ 2% of equity` **and** `notional ≥ ₹5,000` (after
  tick/lot rounding). **EXIT bypasses the deadband** (always fully closes); rounded-to-zero qty =
  no-trade, audited `sub_economic_skipped`. (Prevents confidence-jitter churn that India costs eat.)

### Freshness — TWO independent clocks with different gate classes

The quote clock and the analysis-input clock are deliberately separated so a stale quote can **never
trap an exit** (it is entry-quality, not a universal blocker).

- **Clock 1 — analysis-input freshness → gate #4 `data_fresh`** on `SignalDecision.data_freshness`:
  - **Critical → block (entries AND exits):** `daily OHLCV`, `security master`. (NOTE: `quote` is
    **not** here — it lives in clock 2.) A **missing key = infinitely stale = block** (the dict is
    bug/attacker-controlled).
  - **Degradable → warn, never veto:** `news/social`, `fundamentals`. A feeds-down day must not block
    trades on these liquid names.
  - Thresholds: news/social ≤48h, fundamentals ≤ fetch-time, OHLCV ≤ session, security master ≤24h.
- **Clock 2 — execution-time quote freshness → `stale_quote`, an ENTRY-QUALITY gate** on `Quote.ts`,
  **≤60s measured at each fill** (wall-clock). Per the Contract 4 gate-class split: on an **ENTRY** a
  stale quote (or `no bid/ask` / `no_book` / `quote_fetch_failed`) **blocks**; on a **REDUCE/EXIT** it
  **does not veto** — the exit is recorded as **`intended_exit_unfilled`**. Keeping `quote` out of the
  clock-1 critical set is what guarantees this.
- **Fundamentals freshness = fetch-time** (≤2 trading days since a successful scrape), **not data
  age** (quarterly staleness is normal). **Undated RSS items don't contribute freshness** (ignored,
  never "fresh now").
- **`as_of` look-ahead:** refuse to act on a `SignalDecision` whose `as_of` is from a **prior
  session** unless re-validated against a fresh quote.

### Execution timing & quotes
- **Analysis runs anytime → `SignalDecision` persisted → a thin execution pass runs only
  09:20–15:25 IST** on a trading day, fetching fresh FULL quotes and filling (avoids the open/close
  auctions). Off-hours → distinct reason **`no_live_depth_outside_hours`** (not a generic
  market_open block).
- **Quote = `getMarketData("FULL")`**, batched; **liquidity reality:** require
  `depth.buy[0].qty>0 AND depth.sell[0].qty>0` or `no_book` (block entry / warn exit). See Contract 3.

### Spread gate
- **Hard block at spread > 0.05%** (entries) · **warn band 0.05–0.20%** · per-instrument-tier config
  (a later mid-cap universe needs ~0.20%+). `no_book` short-circuits before any spread math.

### Calendar — Phase 3 BLOCKER
- **Populate `config["nse_holidays"]` with the full official NSE 2026 list** (incl. **movable**
  holidays — Holi, Good Friday, Eid, Diwali/Laxmi Pujan — **and the Muhurat special session**)
  **before the first daily paper run.** Add a **startup assertion** that the current year's list is
  populated or refuse to run. Test: a known movable holiday (e.g. Diwali) is treated as closed.

---

## Build order (each step: contracts → tests → impl → green)

0. **Calendar blocker:** populate `config["nse_holidays"]` with the full official **NSE 2026** list
   (movable holidays + Muhurat) + startup assertion (+ test: Diwali treated as closed). *Must land
   before any daily run.*
1. `contracts.py` (incl. `confidence` + categorical→float map, `Action`, `tier_mult`) +
   `security_master.py` (+ tests: lookup, unknown→fail-closed, tick/lot rounding, conviction map).
2. `costs/india_costs.py` (+ tests vs worked examples).
3. **Angel `getMarketData("FULL")` quote adapter** in `execution/brokers/` (batch; bid/ask/depth;
   `quote_fetch_failed` on None; `no_book` on empty/zero-qty) + `brokers/paper.py` fill simulator
   (+ tests: spread, slippage, partial, reject, tick/lot, **no_book**, **entry-vs-exit asymmetry**).
4. `portfolio.py` incl. **settled-qty / T+1 lots** and MTM equity (+ tests: accounting, avg price,
   realized/unrealized, **settled vs unsettled**).
5. `risk/sizing.py` (absolute target, tier×conf×cap, REDUCE=50%/EXIT=0, deadband, min-notional) +
   `risk/guards.py` (+ tests: **each gate vetoes correctly**, fail-closed defaults, **exits never
   vetoed by confidence/spread/liquidity**, **buying_power**, **no_BTST**, **sector_cap**).
6. `audit.py` (+ tests: hash chain, gap/tamper detection, "no order without audit").
7. `router.py` wiring signal→intent→size→gates→**FULL quote**→paper order→fill→portfolio→audit, with
   the **anytime-analysis → persisted SignalDecision → 09:20–15:25 execution pass** split
   (+ integration test, incl. `no_live_depth_outside_hours`, `intended_exit_unfilled`).
8. `report.py` (**dual books ₹10L + ₹25k**, cost-drag %, return-on-deployed-capital) + a daily run
   over the locked **12-name universe** producing both reports.

**Exit criteria (matches roadmap):** a running virtual portfolio (both books) with India-correct
costs and a daily report, where **every order provably passed the MVP gates**, every blocked order is
logged with its reason, and **exits are never blocked by entry-side gates**. No live order placement,
no F&O. (The FULL-mode *quote* read is live; *order placement* stays paper.)

---

## Open questions — RESOLVED in v2 above

> Retained for traceability. All seven are now answered by
> [Recommended locked defaults v2](#recommended-locked-defaults-v2--locked-2026-06-08).

1. **Rating→Action map** — ✅ locked (v2.C / Contract 1); confidence floor 0.60 on entries only.
2. **Sizing rule** — ✅ fixed-fractional `equity × cap × tier_mult × confidence`, absolute target (v2 Sizing).
3. **Stale-data threshold** — ✅ critical-vs-degradable set + two clocks (v2 Freshness).
4. **Spread-sane threshold** — ✅ 0.05% hard / 0.05–0.20% warn, per-tier (v2 Spread gate).
5. **Paper quote source** — ✅ Angel `getMarketData("FULL")`, built in Phase 3 (v2 Execution / Contract 3).
6. **Order lifecycle** — ✅ `MARKET` + `IOC` only; LIMIT/resting deferred to Phase 5 (v2 / Contract 3).
7. **Universe + cash** — ✅ dual books ₹10L + ₹25k; 12-name ≤2/sector universe (v2.A / v2.B).

**Confidence source — LOCKED for Phase 3:** the PM decision **emits the categorical conviction enum**
(`low|med|high`) directly on `PortfolioDecision`; code maps it to the float (v2.C). **Analyst-level
categorical aggregation is DEFERRED to Phase 5** as a calibration refinement — it is **not** a Phase 3
input, so there is exactly one confidence source.

**Still genuinely open (small params, settle during the reviewer pass):** exact `slippage_bps` /
rejection-rule parameters for the paper simulator; and whether the ₹25k shadow book shares the ₹10L
book's signals 1:1 or re-sizes independently (default: same signals, independent sizing).

---

## Non-goals this phase (deferred, by design)

Live order placement (Phase 6) · full guard set / circuits / ASM-GSM / order-rate cap (Phase 5) ·
backtesting (Phase 4) · F&O (deferred track) · Agent Operating Layer (deferred,
[10](./10-agent-operating-layer.md)).
