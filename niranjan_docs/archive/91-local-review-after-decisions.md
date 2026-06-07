# 11 - Local Review After Locked Decisions

> Review date: 2026-06-07. Local-only review. No commit or push requested.
> Scope: updated `08-implementation-roadmap.md`, `09-open-questions.md`, and prior
> `10-review-findings.md`.

## Summary

The updated `09-open-questions.md` is useful: the highest lead-time items are now
mostly settled. Azure Foundry access, GPT-5.x quota, Angel One, Dhan, initial capital,
paper-trading window, universe, cadence, product type, risk caps, secrets store,
package layout, and upstream sync policy are now locked.

This reduces project uncertainty, but the implementation roadmap still needs revision
before coding. Most of the safety architecture from `10-review-findings.md` has not yet
been folded into `08-implementation-roadmap.md`.

## What looks good now

- Azure onboarding risk is lower because Foundry access and quota are confirmed.
- Broker account lead time is lower because Angel One and Dhan accounts already exist.
- v1 scope is correctly conservative: CNC delivery, liquid Nifty large-caps, once-daily
  cadence, no MIS/F&O, small live capital.
- The package layout decision is now clean: top-level `execution/` and `india_data/`
  beside `tradingagents/`.
- Remaining open items are correctly tied to phases rather than blocking all planning.

## Remaining blockers before implementation planning

### 1. Roadmap still has risk gates too late

`08-implementation-roadmap.md` still introduces execution + paper trading in Phase 3,
then leaves position sizing, stop-loss, daily loss limit, exposure caps, order-rate
cap, kill-switch, and market-rule guards to Phase 5.

This should change before implementation tasks are written. Paper trading should never
run without minimum viable risk gates, cost model, audit log, and fail-closed behavior.

Recommended edit:

- Phase 3: strategy contract, cost model, minimum risk engine, audit log.
- Phase 4: backtest harness.
- Phase 5: paper trading with realistic fills and reports.
- Phase 6: ops hardening and broker-adapter validation.
- Phase 7: live dry run / manual approval.
- Phase 8: tiny live automation.

### 2. `SignalDecision` contract is still not in the roadmap

The plan still says "rating -> intent" but does not define the object passed from
analysis to execution. This boundary must be explicit before coding.

Add a local contract such as:

```text
SignalDecision:
  symbol
  timestamp
  rating
  confidence
  target_position_pct
  max_order_value
  time_horizon
  stop_loss_pct
  take_profit_pct
  reason_codes
  data_freshness
  no_trade_reason
```

The execution layer should consume `SignalDecision`, not free-form markdown.

### 3. Security master is still missing

The system needs a security master before broker integration, because Indian broker
orders need more than Yahoo symbols.

Minimum fields:

```text
canonical_symbol
exchange
trading_symbol
instrument_token
isin
series
tick_size
lot_size
segment
broker_symbol
```

Without this, Angel One, Dhan, yfinance, NSE, BSE, and future backtesting data can drift
into inconsistent symbol formats.

### 4. Paper fill realism is still underspecified

The roadmap still says `PaperBroker` fills vs live LTP. That is too optimistic.

Phase 5 should require:

- spread/slippage model
- partial fills or rejections
- limit-vs-market behavior
- circuit and price-band rejection
- latency between signal and fill
- pessimistic assumptions by liquidity bucket

### 5. Data freshness and fail-closed behavior still need to be explicit

Before live or paper trading, every critical data response should include:

```text
source
as_of_time
freshness_seconds
is_realtime
is_adjusted
confidence
failure_reason
```

If quotes, risk state, broker auth, audit log, or position sync are unavailable, the
system should default to no trade.

### 6. Long-term and short-term memory architecture is still missing

The base repo has decision memory, but the target project needs a clearer memory design:

- session memory: current market day, orders, positions, risk state;
- episodic memory: decision -> signal -> order -> fill -> outcome;
- long-term lessons: only after enough evidence, with decay/confidence;
- market-regime memory: volatility, breadth, FII/DII flow, sector rotation;
- prompt/config memory: model, prompt version, data snapshot.

This can be its own doc or a section in architecture.

### 7. Go-live thresholds are still open

`09` correctly leaves live-readiness metrics open until backtest + paper results exist.
That is fine for now, but the roadmap should say that Phase 6/7 cannot begin until those
thresholds are explicitly written down.

Suggested starting defaults after data exists:

- max drawdown cap
- minimum number of paper trades
- no unresolved critical bugs for a fixed number of sessions
- no missed audit logs
- no failed kill-switch drills
- net performance after costs, not gross P&L

## Recommendation

Do not start implementation planning yet. First revise `08-implementation-roadmap.md`
to absorb the safety findings from `10-review-findings.md`, and add either:

- a short `12-strategy-execution-contract.md`, or
- dedicated sections in `02-architecture.md`

covering `SignalDecision`, security master, data freshness, fail-closed behavior, and
memory layers.

Once that is done, implementation planning can safely start with Phase 0 and Phase 1.
