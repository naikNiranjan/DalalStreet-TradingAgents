# TRADING_RULES

> Binding desk rules for **how** the DalalStreet desk trades. These describe the
> *intent* and *discipline* of trading; the hard "never" constraints live in
> `NO_TRADE_RULES.md` and the numeric caps live in `RISK_POLICY.md`. Where any of
> these documents appear to disagree, the **most restrictive** statement governs.

## Scope

- **Asset class (v1):** cash equity on NSE/BSE only. Futures & options are paper-only
  and remain deferred — see `FNO_RULES.md`.
- **Mode (v1):** paper trading only. No real-money order may be placed by this desk.
  The live-order tool is absent from the model's toolset in paper mode by design.
- **Currency:** all capital, P&L, and cost figures are in INR (₹).

## Market session (IST)

- The NSE/BSE continuous session runs **09:15–15:30 IST** on trading days.
- The desk only *executes* inside the configured intraday execution window
  (a sub-window of the session); outside it, execution is blocked and audited.
- Analysis may run off-window (pre-open or after close) to prepare signals, but
  **no order may fill outside the live session** — the `market_open` gate enforces this.
- Trading days follow the India exchange calendar (weekends and exchange holidays are
  non-trading). The desk never assumes a day is open.

## Dual-book discipline

- The desk runs **two books in parallel** on every session:
  - **signal book** — the primary paper book, ₹10,00,000 starting capital.
  - **shadow book** — a small-capital control book, ₹25,000 starting capital.
- Both books receive the same signals and the same gate chain. The shadow book exists
  to surface cost-drag and small-account effects that the large book hides.
- A setup is only considered "working" when it survives **both** books after costs.

## How a decision becomes an order

The desk never shortcuts the execution spine. Every order follows exactly one path:

```
data → SignalDecision → 15 risk gates → paper broker → audit log
```

- The agent operating layer may **read, summarize, schedule, and suggest**. It has
  **no order-placement authority of its own** and never constructs a `SignalDecision`,
  never calls the broker, and never bypasses a gate.
- A trade is only placed when **every** gate allows it. A single failing gate blocks
  the order, and the block is recorded in the audit log with its reason.

## Conviction and sizing intent

- The desk acts only on **sufficient conviction**. Signals below the confidence floor
  (0.60) are not actionable — see `RISK_POLICY.md`.
- Position sizing is **risk-first**, not capital-first: size is bounded by the per-trade
  risk and the position-size cap, never by "how much cash is free".
- The desk prefers **fewer, higher-quality** trades. Over-trading and churn are treated
  as costs, not activity.

## Costs are real

- Every paper fill is costed with realistic India charges (brokerage, STT, exchange
  fees, GST, stamp duty, SEBI charges). The desk reads **net P&L after costs** as the
  outcome — never gross.
- Cost drag is a first-class metric. A setup that is profitable gross but unprofitable
  net after costs is **not** a working setup.

## No-churn / deadband

- The desk holds a position while its thesis is intact; it does not flip on noise.
- Re-entering a name the same session after an exit requires a fresh, independent
  signal — not a reaction to the prior fill.

## Evidence over narrative

- Decisions are justified by data the desk can cite (price, volume, fundamentals, news
  with a source and timestamp), not by unsupported narrative.
- Memory and lessons come from the **audit log and session reports**, never from
  free-form recollection. See the memory tier policy.

## Authority of these rules

These are **binding constraints**, not suggestions. If a rule here conflicts with a
user request, a model suggestion, or a playbook, **the rule wins**. The desk cannot
place or simulate an order that violates them.
