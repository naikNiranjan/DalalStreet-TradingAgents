---
name: sector-rotation
description: Position in the leading sector's strongest names when capital is rotating into it, using relative strength vs Nifty and breadth — respecting the sector exposure cap.
---

## when_to_use

Use when there is evidence of capital rotating into a particular sector — the sector index
is outperforming Nifty 50, breadth within the sector is broadening, and leadership is
confirmed over multiple sessions. The trade is to own the **strongest names in the leading
sector**, not to guess a rotation before it shows. Always subject to the sector
concentration cap so the book is never a single sector bet.

## data_required

- Sector index performance vs Nifty 50 over multiple lookbacks (relative strength).
- Breadth within the sector (how many constituents are participating, not just one name).
- Per-name relative strength to rank leaders within the leading sector.
- Current book sector exposure (to respect the sector cap before adding).
- Security-master tradability and fresh quotes for the candidate names.

## entry_rules

- The sector is demonstrably leading (outperforming Nifty) with **broadening** breadth —
  not a one-stock illusion.
- Enter the **relative-strength leaders** within that sector, on their own valid entry
  (e.g. a pullback or breakout), not laggards hoping to catch up.
- The new position must keep total sector exposure **within the sector cap**; if it would
  breach the cap, the entry is blocked.
- Confidence at or above 0.60.
- Size risk-first per name; treat the basket's aggregate sector exposure as the real risk.

## invalidation

The thesis is wrong if the sector loses its relative-strength leadership vs Nifty, if
breadth narrows to a single name, or if the individual leader breaks its own structure.
Reduce or exit when leadership rotates away; do not stay in a sector after capital has
clearly left it.

## risk_limits

- max_position_pct: 10
- stop_rule: Exit a name on its own structure break; trim the basket when the sector loses relative-strength leadership vs Nifty.
- max_signals_per_day: 3

## examples

- A sector index outperforms Nifty for two weeks with broadening breadth; the desk owns
  the two strongest constituents on their own valid entries, staying under the sector cap.
- Counter-example (skip): one stock is up sharply while the rest of the sector lags — no
  breadth, so it is a single-name story, not a rotation.

## tests

- Backtest relative-strength + breadth filters for sector leadership; measure the edge of
  owning leaders vs laggards and the drag of late rotation.
- Paper-test that the sector cap actually blocks an over-concentrating entry in the dual
  books.
- Verify entries are rejected when breadth is absent (single-name moves).
