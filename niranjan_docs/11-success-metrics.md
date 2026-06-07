# 11 — Go-Live Success Metrics (define BEFORE coding)

> **These are the gate between paper and real money.** Written down *now*, before any
> results exist, so good-enough can't be rationalized after the fact. Crossing **all**
> primary gates is necessary but **not sufficient** — final go-live also needs explicit
> manual approval (see bottom).

## Why these metrics (and not just Sharpe/win-rate)

With small sample sizes, **Sharpe ratio and win-rate lie** — a handful of lucky trades can
flatter both. Early on, what actually matters is **safety + discipline + edge sign**:
expectancy (is the edge positive after costs?), drawdown (is risk contained?), audit
quality, stale-data avoidance, and kill-switch reliability. Sharpe/win-rate are tracked but
**secondary** until the sample is large enough to trust.

## Primary gates (ALL must pass before a go-live review)

| # | Metric | Threshold | Why it matters |
|---|--------|-----------|----------------|
| 1 | **Paper duration** | ≥ **30 trading sessions** before any live review | Enough days to span varied conditions. |
| 2 | **Completed paper trades** | ≥ **25** round-trip trades | Minimum sample to judge expectancy. |
| 3 | **Max drawdown** | ≤ **5%** of paper capital | Risk containment — the headline safety number. |
| 4 | **Daily-loss-limit discipline** | **0** accepted trades after the daily loss limit was hit | Proves the hard gate actually blocks. |
| 5 | **Audit completeness** | **100%** of decisions / orders / fills logged | No blind spots; SEBI audit trail intact. |
| 6 | **Data safety** | **0** trades placed on stale/critical-missing data | Fail-closed behavior verified in the wild. |
| 7 | **Kill-switch reliability** | **≥ 3** successful kill-switch drill tests | The manual stop must work, every time. |
| 8 | **Net results** | **Positive expectancy** after costs **and** slippage | The edge is real once honest costs are subtracted. |
| 9 | **Live readiness** | **Manual review approval** by Niranjan | Human gate; no automatic promotion to live. |

## Secondary metrics (track, don't gate early)

These are recorded from day one but **not** used as go/no-go until the sample is large
enough (rule of thumb: ≥ 50–100 trades) to be meaningful:

- **Sharpe / Sortino** — risk-adjusted return (noisy at small N).
- **Win rate** — can be high with negative expectancy (or vice-versa); read *with* avg
  win/avg loss, never alone.
- **Profit factor**, **avg win / avg loss**, **turnover**, **cost drag %**, **exposure /
  time-in-market**.

## F&O track — separate, stricter gate (deferred)

The cash-equity gates above do **not** authorize F&O. The F&O paper track
([roadmap deferred tracks](./08-implementation-roadmap.md)) has its own gate:

- Must **survive multiple expiry cycles** (≥ **3** monthly expiries) in paper.
- **0** naked-option-selling orders accepted (hard-blocked).
- Positive expectancy after costs/slippage on F&O specifically.
- Separate manual approval. Real-money F&O is **not-ready** until all the above hold.

## How metrics are produced

- The Phase 3 daily **P&L + metrics report** computes #1–#8 automatically from the audit log.
- #4, #6, #7 are **event counters** (gate-block events, stale-data-block events, kill-switch
  drills) — they must read **0 / 0 / ≥3** respectively, sourced from the audit log, not
  self-reported by the agent.
- Review #9 is a human checklist sign-off referencing this doc.

## Revisit policy

These thresholds are an **initial** contract. They may be tightened after seeing real
paper results (never silently loosened to force a go-live). Any change is recorded here with
a date and reason.

*Initial version: 2026-06-07. Source: review-agent recommendation, accepted.*
