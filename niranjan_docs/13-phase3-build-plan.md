# Phase 3 — Execution Spine: task-by-task build plan

> Status: **BUILD PLAN.** Turns the locked contracts in
> [12-phase3-execution-spine.md](./12-phase3-execution-spine.md) into a sequenced,
> tests-first task list. The spine doc is the **contract of record** (the *what*);
> this doc is the **build order** (the *how / in what sequence*). Where they could
> ever disagree, the spine doc wins and this doc is corrected.
>
> **Two hard rules govern the whole phase:**
> 1. **Contract-first, tests-first.** Each task: write/confirm the typed contract →
>    write the failing tests → implement → green → move on.
> 2. **No trading behavior until the safety core exists.** The router (Task 7) — the
>    first module that can turn a signal into an "order" — is **blocked** on the risk
>    gates (Task 5) **and** the audit log (Task 6). Nothing simulates a fill in the
>    end-to-end path before gates + audit are in and tested.

## Package layout (target)

```
execution/
  __init__.py
  contracts.py          # Task 1  — SignalDecision, Order, Quote, Position, Fill, GateResult, enums
  security_master.py    # Task 1  — Instrument, lookup(), UnknownInstrument
  costs/
    __init__.py
    india_costs.py      # Task 2  — STT/stamp/GST/SEBI/exchange/brokerage/DP -> net
  brokers/
    __init__.py
    base.py             # Task 3  — Broker Protocol
    angel_quotes.py     # Task 3  — getMarketData(FULL) batched quote adapter
    paper.py            # Task 3  — PaperBroker (honest fill simulator)
  portfolio.py          # Task 4  — positions, cash, MTM, settled-qty / T+1 lots
  risk/
    __init__.py
    sizing.py           # Task 5  — absolute target, tier*conf*cap, deadband
    guards.py           # Task 5  — fail-closed gate chain (universal vs entry-quality)
  audit.py              # Task 6  — append-only, hash-chained trail
  router.py             # Task 7  — signal -> intent -> size -> gates -> quote -> fill -> portfolio -> audit
  report.py             # Task 8  — daily P&L + go-live counters (dual books)
tests/execution/        # mirrors the above, one test module per source module
```

Everything in `execution/` is **pure-Python and offline-testable** except `angel_quotes.py`
(thin live adapter — unit-tested against synthetic SmartAPI responses, never a live call in CI).

## Reused, already-built pieces (do not rebuild)

- **Calendar** — `tradingagents/dataflows/india_calendar.py` (`is_market_open`, `is_trading_day`,
  `next/previous_trading_day`, reads `config["nse_holidays"]`). Task 0 fills the holiday list;
  the `market_open` gate (Task 5) and the execution-window check (Task 7) call into it.
- **Angel session** — `tradingagents/dataflows/angel_one.py` exposes `_get_client()` (cached
  logged-in SmartConnect), `_resolve_token()`, `_exchange_for()`, `_pick_token()`. Task 1's
  security master and Task 3's quote adapter reuse these — **no second login path**.
- **PM decision** — `tradingagents/agents/schemas.py::PortfolioDecision`. Task 1 adds one
  `conviction` field (same `Literal["low","medium","high"]` pattern `SentimentReport.confidence`
  already uses), the single source of `SignalDecision.confidence`.
- **Config** — `tradingagents/default_config.py`. Task 0 adds `nse_holidays`; later tasks add an
  `execution` config block (caps, slippage, books, universe, sector tags).

---

## Task 0 — NSE 2026 holiday calendar (BLOCKER)

**Goal:** no daily run may start against an unpopulated/short holiday list. Today the built-in set
is 3 fixed-date holidays only (correct-by-design but live-unsafe).

**Files:** `tradingagents/default_config.py` (add `nse_holidays`), a small
`execution/calendar_guard.py` (or fold the assertion into `india_calendar.py`), `tests/execution/test_calendar_guard.py`.

**Do:**
- Add the **full official NSE 2026 trading-holiday list** (incl. movable: Holi, Good Friday,
  Mahashivratri, Eid, Ganesh Chaturthi, Dussehra, Diwali/Laxmi Pujan, Guru Nanak Jayanti, etc.)
  to `config["nse_holidays"]` as `YYYY-MM-DD` strings. **The Muhurat special session is a
  half-day *trading* session, not a holiday — it is NOT in this set** (documented inline).
- `assert_calendar_ready(config, year)` — raises `CalendarNotConfigured` unless the configured
  list contains ≥ N entries **for that year** (sanity floor, e.g. ≥ 10) and the three known
  fixed-date anchors. Called at run startup (Task 7) before any signal is acted on.

**Tests (first):** Diwali 2026 → `is_trading_day` False; a known movable holiday → closed; an
ordinary weekday → open; empty/short list → `assert_calendar_ready` raises; full list → passes.

**Acceptance:** Diwali treated as closed; startup refuses an unconfigured year.

---

## Task 1 — contracts + security master

**Goal:** lock every typed object the rest of the phase passes around, and the immutable
instrument facts. Pure data + one lookup; **zero behavior**.

**Files:** `execution/contracts.py`, `execution/security_master.py`,
`tradingagents/agents/schemas.py` (add `conviction`),
`tests/execution/test_contracts.py`, `tests/execution/test_security_master.py`.

**Contracts (verbatim shapes from spine §Contract 1/2/3):**
- `Action` (STRONG_BUY/BUY/HOLD/REDUCE/EXIT), `Side`, `OrderType.MARKET`, `TimeInForce.IOC`,
  `OrderState`.
- `SignalDecision` (frozen): required `symbol, action, confidence, as_of, rating_raw,
  rationale_digest, data_freshness` **then** defaulted `horizon_days=1, schema_version=1`.
- `Quote, Order` (`client_oid=field(default_factory=lambda: uuid4().hex)`), `Fill, Position,
  GateResult`.
- Maps as module constants: `RATING_TO_ACTION`, `CONVICTION_TO_CONFIDENCE`
  (`high=0.85/medium=0.60/low=0.35`), `TIER_MULT` (`STRONG_BUY=1.0/BUY=0.6/HOLD=0`).
- `SignalDecision.from_portfolio_decision(pd, symbol, as_of, data_freshness)` — derives
  `confidence` from `pd.conviction` via the map; **missing/unparseable/NaN/out-of-[0,1] →
  fail-closed HOLD** (the only place this translation lives).
- **`conviction: Literal["low","medium","high"]`** added to `PortfolioDecision` with a field
  description that makes the PM emit it; `render_pm_decision` unchanged for display.
- `Instrument` (frozen: symbol, exchange, angel_token, isin, lot_size, tick_size, board_lot=1,
  tradable=True) + `lookup(symbol) -> Instrument` raising `UnknownInstrument` (fail-closed).
  `round_to_tick(price, tick)` / `validate_lot(qty, lot)` helpers used by paper fills + orders.

**Tests (first):** dataclass field order valid (required-before-defaulted); rating→action map
total over the 5 tiers; conviction→confidence map incl. every fail-closed branch → HOLD; tick
rounding (0.05) round-half behavior; unknown symbol → `UnknownInstrument`; `from_portfolio_decision`
on a real `PortfolioDecision`. `conviction` round-trips through structured output (extend an
existing structured-agent test if cheap).

**Acceptance:** contracts import clean; every map branch covered; security master fail-closed.

---

## Task 2 — India cost model

**Goal:** itemized CNC-delivery charges so paper P&L is honest (the ₹25k book's whole point).

**Files:** `execution/costs/india_costs.py`, `tests/execution/test_india_costs.py`.

**Do:** `compute_charges(side, qty, price, *, config) -> Charges` (itemized: STT, stamp duty, GST,
SEBI turnover fee, exchange txn charge, brokerage, DP charge) + `net_cash_impact(fill, charges)`.
Table-driven rates in config (`CostConfig`); defaults track **Angel One's 2026 delivery schedule**
(reviewer-corrected): brokerage = lower of ₹20 or 0.10% (**min ₹5/order**), STT 0.1% both sides,
stamp 0.015% buy-only, NSE txn 0.0030699%, SEBI ₹10/cr, **GST 18% on (brokerage+txn+SEBI+DP)**, DP
₹20/scrip on sell (+GST). Pure math; no I/O. (A truly-free broker = brokerage rate/max/min = 0.)

**Tests (first):** a worked BUY example and a worked SELL example checked to the paisa against
hand-computed numbers; buy-only stamp; sell-only DP + STT both sides; GST base = brokerage+txn
only; zero-brokerage path.

**Acceptance:** matches worked examples to ₹0.01; every component independently asserted.

---

## Task 3 — Angel FULL quote adapter + PaperBroker

**Goal:** a real batched bid/ask/depth quote read (live data, paper orders) + an honest fill
simulator. **This is the first module that can refuse a trade for market-microstructure reasons.**

**Files:** `execution/brokers/base.py`, `execution/brokers/angel_quotes.py`,
`execution/brokers/paper.py`, `tests/execution/test_angel_quotes.py`,
`tests/execution/test_paper_broker.py`.

**Do:**
- `Broker` Protocol (spine §Contract 3): `get_quotes(symbols)->dict[str,Quote]` (ONE batched FULL
  call), `get_quote(symbol)`, `place_order`, `get_order_status`, `cancel_order`, `get_positions`,
  `is_market_open`.
- `angel_quotes.py`: builds the `getMarketData("FULL", {exchange:[tokens]})` request from
  `Instrument`s (reuse `_get_client`, group tokens by exchange → **one** call), parses
  `data.fetched[]` into `Quote(ltp,bid,ask,bid_qty,ask_qty,ts)` from `depth.buy[0]/sell[0]`.
  **`None`/error → `quote_fetch_failed`; empty or zero-qty touch → `no_book`.** Never an LTP
  fallback. Live call is isolated behind `_get_client()` so tests inject a fake client.
- `PaperBroker(Broker)`: `place_order` runs the **honest** model from spine §Contract 5 — BUY
  fills near `ask`, SELL near `bid`; `slippage_bps` scaled by size vs touch qty; IOC partial
  (remainder expires); **side-aware**: entry no-fill → `REJECTED`, exit no-fill →
  `intended_exit_unfilled`; price rounded to `tick_size`, qty validated vs `lot_size`. All
  params from `execution.paper.*` config.

**Tests (first):** quote parse (depth → bid/ask/qty), `quote_fetch_failed`, `no_book`, batching
groups by exchange into one call (assert call count == 1); paper: spread-cross BUY@ask/SELL@bid,
slippage scales with size, partial IOC remainder expires, **entry no_book→REJECTED vs
exit no_book→intended_exit_unfilled**, tick rounding, lot validation. Synthetic quotes only.

**Acceptance:** one FULL call per universe; no LTP fallback anywhere; entry/exit asymmetry proven
at the simulator layer.

---

## Task 4 — portfolio + settled quantity

**Goal:** correct cash + position accounting with **T+1 settlement** so the `no_BTST` gate has
real data and MTM equity is honest.

**Files:** `execution/portfolio.py`, `tests/execution/test_portfolio.py`.

**Do:** `Portfolio` holding cash (with **settled vs unsettled** buckets), positions
(`qty, avg_price, settled_qty, lots[(qty, trade_date)]`), `apply_fill(fill, charges, trade_date)`
(updates avg price, cash, realized P&L on sells, records an unsettled lot on buys),
`settle(as_of_date)` (promotes lots whose `trade_date <= as_of - T+1` to settled), `mark_to_market
(quotes)` (unrealized P&L), `equity()` (cash + MTM positions), `settled_qty(symbol)`. No leverage
(CNC); selling reduces `settled_qty` first.

**Tests (first):** buy → unsettled lot, settled_qty 0 same day; `settle` after T+1 promotes it;
avg-price on add; realized P&L on partial sell (FIFO/avg per locked choice); MTM equity from a
quote map; **sell of unsettled qty surfaces as 0 sellable** (gate consumes this); cash settled vs
unsettled split.

**Acceptance:** settled-qty correct across a T, T+1, T+2 timeline; equity reconciles cash+positions.

---

## Task 5 — sizing + risk gates (SAFETY CORE)

**Goal:** the deterministic layer between brain and broker. **Router is blocked on this.**

**Files:** `execution/risk/sizing.py`, `execution/risk/guards.py`,
`tests/execution/test_sizing.py`, `tests/execution/test_guards.py`.

**Do:**
- `sizing.py`: `target_notional = equity * position_cap * TIER_MULT[action] * confidence`;
  **absolute target** → `delta = target - current_value`; **EXIT→0, REDUCE→50% of current**;
  deadband `|delta| >= 2% equity AND notional >= ₹5,000` (**EXIT bypasses deadband**);
  tick/lot rounding → qty; rounded-to-zero → `sub_economic_skipped`.
- `guards.py`: the **13-gate ordered chain** (spine §Contract 4 table) as pure
  `(Order, context) -> GateResult` functions; `evaluate(order, ctx) -> list[GateResult]`
  (**all gates run**, any `block` ⇒ vetoed, full list returned for audit). The **gate-class
  split** is the crux:
  - **Universal (block entries AND exits):** `kill_switch, market_open, instrument_tradable,
    auth_valid, audit_writable`, and for any SELL `no_BTST/settled_qty`.
  - **Entry-quality (entries only; never veto exits):** confidence floor, `spread_sane`,
    `no_book`, no bid/ask, `stale_quote`. On exit, no fillable market → `intended_exit_unfilled`.
  - **Size/exposure (entries only):** `daily_loss_limit, position_size_cap, max_open_positions,
    sector_cap, buying_power`.

**Tests (first):** **each gate vetoes when its precondition fails**; fail-closed when a precondition
can't be verified (missing freshness key, missing settled qty); **exits never vetoed by
confidence / spread / liquidity** (the asymmetry, asserted gate-by-gate); `buying_power` uses
settled cash + est. charges; `no_BTST` blocks selling unsettled; `sector_cap` binds at 2 of the 3
banks; deadband + sub-economic; EXIT bypasses deadband. Property-style: a HOLD never produces an
order; an EXIT is never blocked by an entry-quality gate.

**Acceptance:** full gate matrix green; the "exit can't be trapped by an entry-quality gate"
invariant proven for every entry-quality gate.

---

## Task 6 — audit log (SAFETY CORE)

**Goal:** immutable, hash-chained trail. **Router is blocked on this too** — gate #10
`audit_writable` means no order is placed without its preceding records written.

**Files:** `execution/audit.py`, `tests/execution/test_audit.py`.

**Do:** append-only JSONL; `AuditRecord(ts, run_id, symbol, stage, payload, prev_hash, hash)` where
`hash = sha256(canonical_json(record_without_hash) + prev_hash)`. `AuditLog(path)` with
`append(stage, symbol, payload) -> AuditRecord`, `verify() -> bool` (chain intact, no gaps/tamper),
`is_writable() -> bool` (feeds gate #10). Canonical JSON (sorted keys, fixed separators) so hashes
are reproducible. Contract objects serialized via a stable `to_payload()`.

**Tests (first):** chain links (`record.prev_hash == prior.hash`); tamper a middle payload →
`verify()` False; drop a line → `verify()` False; `is_writable` False on a read-only path; one
record per lifecycle stage; round-trip serialize/replay.

**Acceptance:** any single-record edit or gap is detected; `is_writable` reflects real writeability.

---

## Task 7 — router integration

**Goal:** wire the spine end-to-end. **Unblocked only after Tasks 5 + 6 are green.**

**Files:** `execution/router.py`, `tests/execution/test_router.py`.

**Do:** two explicit phases (spine §Execution timing):
1. **Analysis (anytime):** consume a `PortfolioDecision` → `SignalDecision.from_portfolio_decision`
   → **persist** it (so execution is decoupled from analysis timing).
2. **Execution pass (09:20–15:25 IST, trading day only):** `assert_calendar_ready`; load persisted
   signals; **one `get_quotes(universe)` FULL call**; per symbol: size → `evaluate` gates →
   (if allowed) `place_order` on the injected `Broker` → `apply_fill` to portfolio →
   `compute_charges` → **audit every stage** (signal, gate results, order, fill/reject/block).
   Off-hours → `no_live_depth_outside_hours`; exit with no fillable market → `intended_exit_unfilled`.
   Router holds a `Broker` (paper now, live later) — **mode = which broker is injected.**

**Tests (first):** end-to-end with a fake Broker + in-memory audit: a BUY signal flows to a Fill and
a portfolio update with a complete audit chain; a blocked order writes a `block` record and **no**
order; an EXIT with `no_book` → `intended_exit_unfilled` (position stays); off-hours →
`no_live_depth_outside_hours`; **gate #10 proven: kill audit writeability → no order placed**;
exactly one quote call per pass.

**Acceptance:** every order provably passed the gate chain and has a full audit trail; exits never
suppressed by entry-side gates in the integrated path.

---

## Task 8 — daily report

**Goal:** the artifact the paper→live gate reads (ties to [11-success-metrics](./11-success-metrics.md)).

**Files:** `execution/report.py`, `tests/execution/test_report.py`.

**Do:** per session, **for each of the dual books (₹10,00,000 + ₹25,000)**: decisions/orders/
blocked(with reasons)/filled(incl. partials, rejects, `intended_exit_unfilled`); realized +
unrealized P&L **net of the cost model**; equity-curve point; **cost-drag %** and per-trade
fixed-cost burden; **return-on-deployed-capital** (not just total equity); go-live counters
(sessions, trades, max drawdown, daily-loss-limit breaches = 0-trades-after, stale-data trades = 0,
audit coverage = 100%); kill-switch drill log. Output: Markdown report + one machine-readable JSON
line per session per book. Reads the audit log + portfolio; no recomputation of trades.

**Tests (first):** counters aggregate correctly from a synthetic audit log; cost-drag math; both
books rendered; audit-coverage = 100% only when every order has its chain; a daily-loss breach with
a later trade flips the gate red.

**Acceptance:** a daily run over the 12-name universe produces both books' Markdown + JSON; every
go-live counter is populated from the audit log, not recomputed.

---

## Sequencing & dependency graph

```
0 calendar ─┐
1 contracts ┼─► 2 costs ─┐
            │            ├─► 5 sizing+gates ─┐
            ├─► 3 quotes+paper ─► 4 portfolio┼─► 7 router ─► 8 report
            │                                │     ▲
            └────────────────► 6 audit ──────┘─────┘
                                          (7 BLOCKED until 5 AND 6 green)
```

- **0 and 1 first** (no behavior depends on un-typed objects or an unconfigured calendar).
- **2, 3, 4** are independent given 1 — buildable in any order; each fully tested before 5.
- **5 (gates) + 6 (audit) are the safety core** — 7 must not be started until both are green.
- **7 then 8** — report reads what the router audited.

## Definition of done (phase)

A running virtual portfolio (**both books**) over the locked **12-name universe**, with
India-correct costs and a daily report, where **every order provably passed the MVP gates**, every
blocked order is logged with its reason, **exits are never blocked by entry-side gates**, and the
audit chain verifies 100%. No live order placement; the FULL-mode *quote* read is live, *order
placement* stays paper. Full `pytest` green (existing 388 + new `tests/execution/`).
