---
name: breakout
description: Enter on a confirmed breakout above a well-defined resistance level on expanding volume, in the direction of the higher-timeframe trend.
---

## when_to_use

Use when a liquid cash-equity name has built a clear, multi-session base or consolidation
under a horizontal resistance level, and price breaks above it on a volume expansion. Best
in a constructive market regime (Nifty 50 above its short-term moving average) where
follow-through is more likely. Not for choppy, rangebound tape or names with no clean level.

## data_required

- Fresh intraday and daily OHLCV for the symbol (broker feed authoritative; yfinance
  fallback for analysis only).
- A defined resistance level from at least the prior 10–20 sessions of price action.
- Volume baseline (e.g. 20-session average) to judge the breakout expansion.
- Security-master confirmation that the instrument is tradable.
- Nifty 50 / sector context for regime alignment.

## entry_rules

- Price closes/holds **above** the identified resistance level (not an intrabar wick).
- Breakout volume is meaningfully above the recent average (expansion, not a drift).
- Higher-timeframe trend is up or neutral-turning-up; do not fade a downtrend.
- Confidence in the setup is at or above the desk floor (0.60); below that it is not
  actionable.
- Entry is sized risk-first from the distance to the invalidation level.

## invalidation

The thesis is wrong if price closes back **below** the broken resistance level (a failed
breakout / fakeout), or if the breakout occurs on weak volume and immediately stalls. The
stop sits just below the breakout level. Exit on invalidation — do not average down into a
failed breakout.

## risk_limits

- max_position_pct: 12
- stop_rule: Exit if price closes back below the broken resistance level; hard stop just below that level.
- max_signals_per_day: 3

## examples

- A name consolidates ₹980–₹1000 for two weeks, then breaks ₹1000 on 2x average volume
  with the Nifty trending up — valid breakout entry, stop below ₹1000.
- Counter-example (skip): price pokes above ₹1000 on thin volume during a Nifty down day
  and immediately fades — no volume expansion, no regime support, not actionable.

## tests

- Backtest the level-break + volume-expansion rule across a basket of liquid names over
  multiple regimes; measure net-of-cost expectancy and failed-breakout rate.
- Paper-test in the dual books; confirm the shadow book survives cost drag.
- Verify the stop logic triggers on a close back below the level in historical fakeouts.
