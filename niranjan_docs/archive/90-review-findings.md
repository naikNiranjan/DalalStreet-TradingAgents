# 10 - Review Findings from Codex

> Review date: 2026-06-07. This is an append-only planning review of
> `niranjan_docs/00` through `09`. No implementation code was reviewed or changed.

## Verdict

The plan is directionally strong and worth keeping. The best decisions are:

- Keep the existing LangGraph analysis engine as an advice engine.
- Add execution beside it instead of rewriting the fork.
- Make paper trading the default path.
- Put deterministic risk gates between the LLM and any order.
- Treat India market mechanics, costs, and compliance as first-class requirements.

I would approve the plan for continued design work, but not for coding yet. A few
architecture and sequencing details should be revised first.

## Must-fix before coding

### 1. Move core risk controls earlier

The roadmap currently introduces execution and paper trading in Phase 3, then adds
many risk guards in Phase 5. That is too late. Even simulated trading will produce
misleading results if position sizing, exposure caps, stop rules, kill-switch behavior,
order-rate caps, costs, and slippage are not present from the first paper-trading run.

Recommended revision:

- Phase 3 should include minimum viable risk gates, audit log, cost model, and
  kill-switch.
- Phase 5 should become hardening, broker compliance validation, operational
  resilience, and failure drills.

### 2. Define the strategy contract between analysis and execution

The plan says "rating to intent" but does not yet define the exact contract. This is
the most important boundary in the system.

Add a deterministic `SignalDecision` object before execution:

```text
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

The LLM can recommend, but code should decide whether the recommendation is actionable.
Examples: no trade below confidence threshold, no trade on stale data, no averaging down
unless allowed, no sell in CNC if holdings are insufficient.

### 3. Move historical validation before prolonged paper trading

The current sequence is paper trading first, backtesting second. A better sequence is:

1. Minimal backtest harness with India costs and corporate-action adjusted prices.
2. Short paper-trading dry run to validate live plumbing.
3. Longer paper-trading validation window.

This avoids spending weeks paper-trading a strategy that fails basic historical sanity
checks.

### 4. Add a security master and instrument-token layer

Indian broker APIs often need exchange tokens, segment codes, series, tick size, lot
size, and ISIN, not only Yahoo-style symbols like `RELIANCE.NS`.

Add a `security_master` component that maps:

```text
canonical_symbol: RELIANCE.NS
exchange: NSE
trading_symbol: RELIANCE-EQ
instrument_token
isin
series
tick_size
lot_size
segment
broker_symbol
```

This should be treated as core infrastructure, not a broker-adapter detail.

### 5. Add corporate-action handling

Backtests and memory reflection will be wrong if splits, bonuses, dividends, symbol
changes, mergers, and delistings are ignored. Add a corporate-action adjustment step
for price history and a warning when data is unadjusted.

### 6. Make paper fills more realistic than "fill at LTP"

Filling every paper order at LTP will overstate performance. At minimum, model:

- bid/ask spread
- slippage by liquidity bucket
- partial fill or rejected fill states
- market order vs limit order behavior
- price band and circuit rejections
- latency between signal and simulated fill

Paper trading should be pessimistic enough that live trading is not a shock.

### 7. Strengthen data quality contracts

The data plan is good, but it needs explicit freshness and reliability rules.

Each data provider should return:

```text
source
as_of_time
freshness_seconds
is_realtime
is_adjusted
confidence
failure_reason
```

The agent should refuse to trade when critical data is stale, missing, or mixed across
conflicting sources.

### 8. Reword compliance claims more carefully

The SEBI section is useful, but phrases like "fully permitted" should be softened.
The practical rule should be:

- personal-use, low-frequency API trading appears to be the intended allowed path
  under the retail algo framework;
- live trading still requires broker-specific confirmation for static IP,
  authentication, algo tagging, order ID behavior, and current implementation rules;
- no live orders until the broker confirms the account is enabled for compliant API
  algo flow.

This keeps the plan legally cautious.

### 9. Add a real memory architecture

The base repo has decision-log memory, but Niranjan's target needs explicit short-term
and long-term memory.

Recommended layers:

- short-term run memory: current market session, open positions, active orders,
  latest news, current risk state;
- episodic memory: every decision, signal, order, fill, rejection, P&L outcome;
- long-term lessons: validated lessons after enough outcome data, with decay and
  confidence;
- market-regime memory: volatility, trend, breadth, rates, FII/DII flow, sector rotation;
- prompt/version memory: model, prompt, config, data snapshot used for each decision.

Long-term memory should not write a "lesson" after every trade. It should wait for
enough evidence and record confidence.

### 10. Define real-time scope in v1 vs v2

"Real-time connection" can mean streaming data, intraday signals, or high-frequency
execution. For safety, v1 should use real-time quotes for paper fills and monitoring,
but make trading decisions on a low-frequency cadence.

Recommended split:

- v1: daily or once/twice per day decisions, live LTP for paper fills, cash equities only.
- v2: intraday cadence with websocket candles and stricter rate limits.
- not in scope: high-frequency or tick-by-tick autonomous trading.

## Should-add before implementation plan

### 11. Explicit no-trade and fail-closed behavior

Document default behavior for every failure:

- LLM unavailable: no trade.
- quote stale: no trade.
- broker auth failed: no trade.
- news unavailable: allow analysis, but block live order if policy says news is critical.
- risk state unavailable: no trade.
- audit log unavailable: no trade.

Trading systems should fail closed.

### 12. Portfolio and tax/reporting outputs

Add daily reports for:

- gross and net P&L
- costs paid by category
- open risk
- turnover
- realized and unrealized P&L
- drawdown
- decision accuracy
- order rejection reasons
- capital utilization

Tax reporting can be later, but execution logs should retain enough data to reconstruct it.

### 13. Manual approval mode before full live mode

Before fully automated live orders, add an intermediate mode:

```text
paper -> live_dry_run -> live_manual_approval -> live_auto_tiny
```

`live_manual_approval` generates broker-ready orders but waits for Niranjan to approve
them. This is a useful bridge between paper and live autonomy.

### 14. Broker adapter acceptance tests

Before any live order, require mocked and sandbox tests for:

- login and token refresh
- quote fetch
- order placement
- order rejection
- cancel
- position sync
- broker outage
- duplicate order prevention
- idempotency after process restart

### 15. Azure cost guardrails

Add hard budget controls, not only observability:

- per-run token budget
- per-day LLM spend estimate
- max analyses per day
- cheaper model fallback for non-critical agents
- no live order if the analysis was truncated or incomplete

## Suggested roadmap revision

```text
Phase 0: Baseline Indian ticker run
Phase 1: Azure Foundry provider and observability
Phase 2: India data contracts, security master, market calendar
Phase 3: Strategy contract, cost model, minimum risk engine, audit log
Phase 4: Historical backtest harness with corporate-action awareness
Phase 5: Paper trading with realistic fills and daily reports
Phase 6: Ops hardening, compliance validation, broker adapter tests
Phase 7: Live dry run and manual approval mode
Phase 8: Tiny live automation after explicit gate
```

## External verification snapshot

These sources should be re-checked during implementation because APIs and regulations
can change quickly:

- SEBI circular: https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
- SEBI implementation timeline extension: https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html
- Microsoft Foundry LangChain integration: https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-models
- DhanHQ docs: https://docs.dhanhq.co/
- Dhan sandbox: https://sandbox.dhan.co/v2/
- Angel One SmartAPI docs: https://smartapi.angelbroking.com/docs

## Recommendation

Proceed with planning, but revise the docs before writing implementation tasks. The
highest-value next step is to turn this review into changes in `08-implementation-roadmap.md`
and `09-open-questions.md`, then create a detailed implementation plan only for Phase 0
and Phase 1 first.
