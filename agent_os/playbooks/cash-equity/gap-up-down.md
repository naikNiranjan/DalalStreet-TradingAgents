---
name: gap-up-down
description: Trade the opening gap with a plan — fade an unsupported gap back toward prior close, or join a news-backed gap on confirmation, never blindly chase the open.
---

## when_to_use

Use at and shortly after the open when a name gaps materially away from the prior close.
Two sub-cases: (a) a **gap with a clear catalyst** (results, order win, regulatory news)
that may continue, and (b) an **unsupported gap** (no catalyst, low-volume, often a
fade-back candidate). Requires a liquid name so the opening auction price is trustworthy.

## data_required

- Prior-session close and the current opening print / first-few-minutes OHLCV.
- Whether a catalyst exists (news item with a source and timestamp); absence is itself
  information.
- Opening volume vs the typical opening range.
- Security-master tradability and a fresh quote (no stale-quote fills).
- Index gap context (is the whole market gapping, or just this name?).

## entry_rules

- **Continuation (news-backed gap):** enter only after the opening range holds in the gap
  direction with supporting volume — confirmation, not the bare open.
- **Fade (unsupported gap):** enter against the gap only when there is no catalyst and
  price stalls/reverses at the opening extreme, targeting partial gap-fill.
- Never act on the very first tick; wait for the opening-range read.
- Confidence at or above 0.60; otherwise stand aside.
- Size risk-first from the opening-range extreme used as the stop.

## invalidation

For a continuation, invalidation is a return back inside the prior-close gap zone (the gap
"fills" against you). For a fade, invalidation is a fresh push beyond the opening extreme
on rising volume (the gap is real and continuing). Exit promptly — opening trades resolve
fast and do not reward hoping.

## risk_limits

- max_position_pct: 10
- stop_rule: Continuation stop at re-entry into the gap zone; fade stop beyond the opening-range extreme.
- max_signals_per_day: 2

## examples

- A name gaps +6% on a strong results beat, holds the opening range for 15 minutes on
  heavy volume — valid continuation entry, stop back inside the gap.
- A name gaps +3% with no news on light volume and stalls at the open — candidate fade
  toward prior close, stop above the opening high.

## tests

- Backtest catalyst-tagged vs uncatalysed gaps separately; continuation and fade have
  different edges and must be measured apart.
- Paper-test opening-range confirmation latency in the dual books; confirm fills are
  realistic given opening spreads.
- Verify no entry fires on the first tick and that stale-quote opens are skipped.
