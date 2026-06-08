---
name: news-shock
description: React to a material, verified news shock with a planned, confirmed entry — only on sourced, timestamped news, never on rumour or an unconfirmed first print.
---

## when_to_use

Use when a liquid name experiences a sudden, material price move driven by a specific,
**verifiable** news event (results surprise, large order, regulatory action, management
change, sector-wide shock). The point of this playbook is discipline under volatility: act
only on confirmed information, with a plan, after the initial chaos has given a readable
level. Do not trade rumour, unconfirmed social chatter, or a single unsourced headline.

## data_required

- The news item itself, with a **source URL and a timestamp** — unsourced news is not
  tradable.
- Pre- and post-news OHLCV and volume to read the reaction.
- Whether the move is name-specific or sector/market-wide.
- A fresh quote (news-driven tape moves fast; stale quotes are blocked).
- Security-master tradability.

## entry_rules

- News is **verified and sourced** (URL + timestamp); rumour and unconfirmed prints are
  not actionable.
- Wait for a readable post-shock level (an opening range or a stabilisation) rather than
  buying/selling the spike's first tick.
- Trade **in the direction the verified news justifies**, on confirmation that the level
  holds, with volume support.
- Confidence at or above 0.60; news that is ambiguous in impact stays out.
- Size risk-first and **smaller than normal** given elevated volatility.

## invalidation

The thesis is wrong if price reverses back through the post-shock level (the market
disagrees with the reaction), if the news is subsequently contradicted/retracted, or if
the move proves to be market-wide noise rather than name-specific. Exit immediately on a
retraction or a level break — do not rationalise a reversal.

## risk_limits

- max_position_pct: 8
- stop_rule: Exit on a reversal back through the post-shock level, or immediately if the news is retracted/contradicted.
- max_signals_per_day: 2

## examples

- A name reports a large, sourced order win; price gaps up, holds a post-news range for
  20 minutes on volume — valid confirmed entry, smaller size, tight stop under the range.
- Counter-example (skip): an unverified social-media claim spikes a stock — no source, no
  timestamp, not actionable under `DATA_SOURCES.md`.

## tests

- Backtest only on events with verifiable timestamps; measure reaction-follow-through vs
  fade rates by news category.
- Paper-test reduced sizing and confirmation latency in the dual books under high
  volatility.
- Verify the playbook rejects entries when the news lacks a source/timestamp.
