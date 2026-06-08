---
name: trend-following
description: Ride an established uptrend by entering on orderly pullbacks to a moving-average / structure support, staying with the trend until structure breaks.
---

## when_to_use

Use when a name is in a clean, established uptrend — higher highs and higher lows, price
above a rising medium-term moving average — and offers an orderly pullback into support.
Best when the broader market and the name's sector are also trending up. Avoid in
rangebound or downtrending names; this is not a bottom-fishing playbook.

## data_required

- Daily and intraday OHLCV showing the trend structure (swing highs/lows).
- A medium-term moving average (e.g. 20/50-session) and its slope.
- Pullback depth and volume (healthy pullbacks come on lighter volume).
- Relative strength vs Nifty 50 and the sector.
- Security-master tradability and fresh quotes.

## entry_rules

- Trend is intact: price above a rising MA, prior structure of higher highs/lows.
- Enter on a **pullback** into the moving-average / prior-breakout support that holds,
  ideally with a reversal signal off support — not on a vertical extension.
- Pullback is orderly (lighter volume), not a high-volume breakdown.
- Confidence at or above 0.60.
- Size risk-first using the swing-low below support as the invalidation.

## invalidation

The thesis is wrong if price closes below the most recent higher-low / the support being
bought, breaking the trend structure, or if the MA rolls over. Exit on a confirmed
structure break; trail the stop up under successive higher-lows as the trend advances.

## risk_limits

- max_position_pct: 15
- stop_rule: Exit on a close below the most recent swing low / bought support; trail under higher-lows.
- max_signals_per_day: 3

## examples

- A leader trends up for weeks, pulls back to a rising 20-day MA on light volume, and
  reverses — valid entry, stop below the pullback low.
- Counter-example (skip): price is extended far above the MA with no pullback — chasing
  here has poor risk-reward and no defined stop.

## tests

- Backtest pullback-to-MA entries in confirmed uptrends; measure expectancy and the cost
  of false structure-breaks.
- Paper-test the trailing-stop logic across multi-week holds in the dual books.
- Verify entries are rejected when the trend filter (price above rising MA) is not met.
