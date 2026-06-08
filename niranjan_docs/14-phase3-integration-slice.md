# Phase 3 — Integration slice: graph → SignalDecision → Router (PLAN)

> Status: **PLAN — reviewer rounds 1 & 2 folded in (API-preserving fallbacks + book-specific sizing
> configs + coverage reporting); awaiting sign-off. No code until signed off** (cadence: plan →
> review → build → review → commit). Builds on the committed spine
> ([13-phase3-build-plan](./13-phase3-build-plan.md), commit `2aac3ec`) and the locked contracts
> ([12-phase3-execution-spine](./12-phase3-execution-spine.md)).

## Goal

Prove the spine is **connected to the analysis graph**, not just unit-tested in isolation: run **one
real dual-book paper session** over the locked 12-name universe — graph emits a decision per name →
typed `SignalDecision` → `Router` → Angel FULL quotes → `PaperBroker` fills → dual-book reports.

**Hard guardrail: still no live orders.** This slice adds a *live quote read* (Angel `getMarketData
FULL`) and *live LLM graph* calls, but order placement stays `PaperBroker`. "Do not jump to live."

## The gap to close (why this needs a plan, not just glue)

`TradingGraph.propagate(company, trade_date)` returns `(final_state, processed_signal)` where
`processed_signal = parse_rating(final_state["final_trade_decision"])` — i.e. the PM output reaches
us only as **rendered markdown** (`trading_graph.py:445`). Two problems for the bridge:

1. **`conviction` is lost.** It's a field on `PortfolioDecision` (added Phase 3) but
   `render_pm_decision` does **not** render it, so it isn't in the markdown. Yet it is the *single
   source* of execution confidence (`from_portfolio_decision`). So we must **surface the typed
   `PortfolioDecision`**, not re-parse markdown.
2. **`SignalDecision` needs `as_of` + `data_freshness`** — neither exists in the graph today. We
   must stamp the decision time and build a freshness map from the data layer.

## Changes / contracts

### 1. Surface the typed PM decision (additive, APIs preserved)
- Add `portfolio_decision: Optional[PortfolioDecision]` to `AgentState` (agent_states.py).
- `portfolio_manager_node` stores the typed object in state **additively**; `final_trade_decision`
  (markdown) is **unchanged** so memory log / CLI / saved reports keep working.
- **Preserve the public API (reviewer-locked):** `propagate()` keeps returning **exactly**
  `(final_state, processed_signal)` — callers and tests depend on that shape, so it does **not**
  change. The session runner reads the typed object from `final_state["portfolio_decision"]`.
- **Scope the helper change (reviewer-locked):** `invoke_structured_or_freetext` gains a
  `return_parsed: bool = False` keyword — **back-compat default returns `str`**, so its other callers
  (sentiment / research manager / trader) are untouched. Only the PM node passes `return_parsed=True`
  to receive `(markdown, parsed_or_None)`. (Equivalent alternative: a thin PM-only wrapper; either way
  the shared `str`-returning contract is preserved.)
- **Bridge fallback (LOCKED).** A small `build_signal(state, symbol, as_of, freshness)` is the **only**
  caller of `SignalDecision.from_portfolio_decision` and **never passes it `None`**:
  - `portfolio_decision` present → `from_portfolio_decision(pd, ...)` (typed path; derives confidence).
  - `portfolio_decision is None` (free-text fallback / conviction unparseable) → parse the rating from
    the markdown for `rating_raw` (audit only) and emit a **HOLD** `SignalDecision`
    (`action=HOLD, confidence=0.0`), audited `reason="pm_decision_unstructured"`. For this first
    same-session run (no carried positions) HOLD is a no-op — the safest fallback.
  - **Deferred (NOT this slice):** mapping a markdown `Underweight`/`Sell` to REDUCE/EXIT **only when an
    existing position** is held — needs multi-session position carry-over; out of scope here.

### 2. Freshness capture (`data_freshness`)
**Freshness must come from a real, verifiable read — the session runner must NOT invent it**
(reviewer-locked). The graph returns *reports*, not source timestamps, so:
- `daily OHLCV` (critical clock-1) = the **last-bar timestamp from an explicit OHLCV freshness probe**
  the session runner performs per symbol — a dedicated `get_stock_data` read via the existing vendor
  dispatch (Angel→yfinance) — recorded as *the* freshness source. *(Cleaner long-term: instrument the
  in-graph dataflow calls with a `FreshnessTracker` so the analysis fetch itself stamps freshness;
  deferred — the explicit probe is simpler and auditable for slice 1.)*
- `security master` (critical clock-1) = the `refresh_from_angel` timestamp (known at setup).
- `news` / `social` / `fundamentals` (degradable) = the **analysis run time** (fetched in-graph this
  run); fundamentals uses its own fetch-time threshold.
- A source not actually read is **omitted** → the gate surfaces it (critical → block, degradable →
  warn) exactly as designed; the runner never fabricates a "fresh now" stamp.

### 3. Session runner (`execution/session.py`)
Pure orchestration over already-built pieces; no new trading logic.
- **Setup:** load `UNIVERSE` + `SecurityMaster`; `refresh_from_angel(UNIVERSE)` to populate tokens
  *(LIVE)*; `assert_calendar_ready`.
- **Analysis phase (anytime):** per symbol → `graph.propagate(symbol, date)` *(LIVE LLM)* → read
  `final_state["portfolio_decision"]` → **`build_signal(final_state, symbol, as_of=run_ist,
  freshness=...)`** (the sole bridge; handles the typed *and* `None`-fallback paths — never calls
  `from_portfolio_decision` with `None`) → `persist_signals(path)`. A symbol whose graph run errors is
  **skipped + recorded in the analysis manifest** (see Coverage below), never guessed.
- **Execution phase (09:20–15:25 IST):** `load_signals`; **ONE** `AngelQuoteAdapter.get_quotes(UNIVERSE)`
  *(LIVE quote read)*; build **dual books** — for each of `{signal ₹10,00,000, shadow ₹25,000}` a
  `Portfolio` + `AuditLog` + `Router`, **all primed with the same quote snapshot** (one FULL call,
  not one-per-book); `run_execution_pass(signals)` per book; `build_book_report` per book;
  `SessionReport.write_markdown/jsonl`.
- **Dual-book policy:** same signals, **independent sizing** (each book sizes off its own equity)
  **with a book-specific `ExecutionConfig`** (see below) — the committed defaults would make the ₹25k
  book never trade.
- **Coverage / analysis manifest:** the session records `universe_planned (12) / analyzed N /
  skipped M` with each skipped symbol + reason. This goes into the `SessionReport` header **and** a
  machine-readable manifest JSONL line, so a run with many graph failures can't masquerade as a clean
  smaller run.

### 2a. Book-specific sizing configs (LOCKED — reviewer-driven)

The committed `ExecutionConfig` defaults (`position_cap_init=10%`, `position_cap_hard=15%`,
`deadband_min_notional=₹5,000`) are correct for the ₹10L book but **structurally block the ₹25k
book**: 10% of ₹25k = ₹2,500 target, 15% hard = ₹3,750 — both **below the ₹5,000 deadband**, so the
shadow book would be all-no-trade and the go-live-size gate would measure nothing. Fix: two named
configs (added to `execution/config.py` as factories; whole-share rounding via `sub_economic_skipped`
remains the real small-capital floor).

| Param | `signal_book` (₹10,00,000) | `shadow_book` (₹25,000) | Why |
|---|---|---|---|
| `position_cap_init` | 0.10 | **0.35** | shadow must afford ≥1 share of an expensive name (1 TCS ~₹3,850 ≈ 15% of ₹25k) |
| `position_cap_hard` | 0.15 | **0.40** | concentration is unavoidable — and the *point* — at ₹25k |
| `deadband_min_notional` | ₹5,000 | **₹0** | the ₹5k absolute floor is 20% of ₹25k; drop it and let whole-share rounding (`sub_economic_skipped`) be the floor |
| `deadband_equity_frac` | 0.02 | 0.02 | 2% of ₹25k = ₹500 anti-churn floor, still relative |

This makes the shadow book trade where it *can* afford whole shares and honestly skip (audited
`sub_economic_skipped`) where it can't — exactly the small-capital fidelity the go-live-size gate
exists to measure. Numbers are tunable; this is the locked starting point.

### 4. Entry point (`scripts/paper_session.py`)
Thin runnable: **paper is the hard default**, `--date`, `--kill-switch`, `--analysis-only` /
`--exec-only` (so analysis can run off-window and the exec pass run inside the window),
`--books both|signal|shadow`. Prints the two report paths.

## Build order (each: tests-first → impl → green)

0. **Surface typed PM decision** — `AgentState.portfolio_decision`; PM node stores it;
   `invoke_structured_or_freetext(..., return_parsed=True)` (default `False` keeps `str`). `propagate()`
   return shape **unchanged**. *Tests:* PM node yields the typed decision incl. `conviction`; markdown
   path unchanged; **other callers still receive `str`** (return_parsed default); free-text → `None`.
1. **Freshness probe + builder** — explicit per-symbol OHLCV read stamps `daily OHLCV`; security
   master from refresh time; degradable from run time. *Tests:* critical from real reads, degradable
   from run time, omitted source omitted (never fabricated).
2. **graph→SignalDecision bridge** (`build_signal(state, symbol, as_of, freshness)`) — *Tests:* typed
   path derives confidence; **`portfolio_decision is None` → HOLD** (`pm_decision_unstructured`, never
   calls `from_portfolio_decision` with `None`); `rating_raw` still captured from markdown.
3. **Book-specific configs + coverage** — add `signal_book` / `shadow_book` `ExecutionConfig`
   factories (table in §2a) to `execution/config.py`; extend `SessionReport` with the coverage
   header + analysis-manifest JSONL line. *Tests:* shadow config lets a ₹25k book buy ≥1 share of a
   ₹3,850 name and skips (`sub_economic_skipped`) where it can't; coverage shows planned/analyzed/skipped.
4. **Session runner** — *Tests (offline):* a **FAKE graph** returning canned decisions + **synthetic
   quotes**; assert both books produce reports (each with its own config), one **shared** FULL fetch
   primes both, audit chains verify, gate outcomes correct, exits never blocked by entry-quality,
   skipped symbols appear in coverage. **No live calls in CI.**
5. **LIVE dual-book paper run** *(separate, gated, NOT CI)* — refresh tokens, real graph, real FULL
   quotes, inside the window; eyeball both reports (incl. coverage) + verify both audit chains.

## Offline vs live
- **Offline + fully tested (CI):** steps 0–3 (graph-surfacing, freshness, bridge, session
  orchestration with fakes/synthetic quotes).
- **Live / credentialed / manual (not CI):** step 4 — `refresh_from_angel`, real graph LLM calls,
  Angel FULL quote read. Order placement remains paper throughout.

## Reviewer round-1 resolutions (folded in above)
1. **`propagate()` API preserved** — return shape stays `(final_state, processed_signal)`; the typed
   decision is read from `final_state["portfolio_decision"]` (additive state key). *(was: "propagate
   additionally returns…")*
2. **Free-text fallback LOCKED** — `build_signal` is the sole caller of `from_portfolio_decision` and
   never passes `None`; missing typed decision → **HOLD** (`pm_decision_unstructured`); markdown
   Underweight/Sell→exit-with-position deferred to multi-session carry-over.
3. **Helper change scoped** — `invoke_structured_or_freetext(return_parsed=False)` default preserves
   the `str` contract for sentiment / research manager / trader; only PM opts in.
4. **Freshness has a real capture point** — explicit per-symbol OHLCV probe (not invented by the
   runner); dataflow instrumentation noted as the cleaner long-term option.

## Reviewer round-2 resolutions (folded in above)
1. **Shadow book would never trade** (₹5k deadband > 15%-cap target on ₹25k) → **book-specific
   configs** locked in §2a (shadow: cap 35/40%, `deadband_min_notional=₹0`).
2. **Session-runner bypass** → analysis phase now routes through **`build_signal(...)`**, the sole
   bridge (never `from_portfolio_decision(None)`).
3. **Symbol-error visibility** → **coverage header + analysis manifest** (planned/analyzed/skipped) in
   the session report, so failures can't masquerade as a clean smaller run.

## Reviewer answers (confirmed)
- **Shared quote fetch:** ✅ one FULL snapshot shared across both books.
- **Symbol error:** ✅ skip + log — **and visible in the session report** (now §Coverage).
- **₹25k sizing:** ✅ independent — **with the shadow-specific config** in §2a (not the ₹10L defaults).

*(No open questions remain.)*

## Non-goals (this slice)
Live order placement (Phase 6) · scheduling/cron (manual run first) · backtest (Phase 4) · F&O ·
multi-day signal carry-over (single same-session run first; `as_of` look-ahead guard already refuses
prior-session signals).

## Exit criteria
One dual-book paper session over the 12-name universe: both Markdown + JSONL reports produced (each
with its **coverage header** — planned/analyzed/skipped); the **₹25k shadow book actually trades**
where it can afford whole shares (and honestly `sub_economic_skipped`s where it can't); both audit
chains verify 100%; every order provably passed the gates; exits never blocked by entry-quality
gates; **zero live orders placed**.
