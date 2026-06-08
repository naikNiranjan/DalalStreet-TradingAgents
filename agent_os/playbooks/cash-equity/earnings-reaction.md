---
name: earnings-reaction
description: Trade the post-results reaction on confirmation — enter in the direction of a verified earnings surprise after the initial print stabilises, with reduced size.
---

## when_to_use

Use in the session(s) immediately after a company reports quarterly results, when the
reported numbers and guidance are known and the market is repricing the name. The edge is
in trading the **reaction**, not predicting the result — entries are only considered once
results are out and verified. Requires a liquid name and a clear surprise vs expectations.

## data_required

- The reported results vs consensus/prior (revenue, margins, guidance) — sourced and
  dated.
- Pre- and post-results OHLCV and volume.
- Management commentary / guidance direction where available.
- A fresh quote and security-master tradability.
- Sector and Nifty context (was the whole sector re-rated?).

## entry_rules

- Results are **published and verified** — never trade into the print on a guess.
- A clear surprise exists (beat or miss vs expectations) with a coherent price reaction.
- Wait for the post-results range to form; enter in the direction of the surprise on
  confirmation that the level holds with volume.
- Confidence at or above 0.60; mixed/ambiguous results stay out.
- Size risk-first and **reduced** — post-earnings volatility is elevated and gappy.

## invalidation

The thesis is wrong if price reverses back through the post-results level (the initial
reaction is being faded), if a second-read of the results (e.g. poor guidance behind a
headline beat) flips the narrative, or if the move was purely sector-wide. Exit on the
level break or on a narrative flip; do not hold through a clear rejection.

## risk_limits

- max_position_pct: 8
- stop_rule: Exit on a reversal back through the post-results range, or on a guidance/narrative flip against the position.
- max_signals_per_day: 2

## examples

- A name beats on revenue and raises guidance; price gaps up, builds a post-results base
  on volume, then continues — valid confirmed entry, reduced size, stop under the base.
- Counter-example (skip): a headline EPS beat with weak guidance and an immediate fade —
  the reaction contradicts the headline, so the surprise is not clean; stand aside.

## tests

- Backtest reaction entries bucketed by surprise direction and guidance; measure
  follow-through vs fade and the cost of holding through volatility.
- Paper-test reduced sizing and gap risk in the dual books across several earnings dates.
- Verify entries are rejected before results are published and when results are ambiguous.
