# RISK_POLICY

> Binding numeric risk limits for the DalalStreet desk. These are the caps the
> 15-gate chain enforces and the ceilings every playbook's `risk_limits` block must
> respect. A playbook may be **more** conservative than these numbers; it may never be
> looser. Where a playbook and this policy disagree, **this policy wins**.

## Capital (paper, v1)

| Book | Starting capital | Purpose |
|------|------------------|---------|
| signal | ₹10,00,000 | primary paper book |
| shadow | ₹25,000 | small-capital control book |

## Hard caps (enforced by gates)

- **Confidence floor: 0.60.** Any signal with confidence below 0.60 is not actionable.
  The `confidence_floor` gate blocks it.
- **Per-position size cap.** No single position may exceed the configured percentage of
  the book's equity (position-size-cap gate). Playbooks set their own `max_position_pct`
  at or below this ceiling.
- **Maximum open positions.** The number of concurrent open positions per book is
  capped (max-open-positions gate). New entries beyond the cap are blocked.
- **Daily loss limit.** If a book's realized loss for the session breaches the daily
  loss limit, **further entries are blocked for the rest of the session**
  (daily-loss-limit gate). Existing positions may still be exited.
- **Buying-power.** An order that would exceed available buying power is blocked — the
  desk never uses leverage it does not have. No margin in cash-equity v1.
- **Sector concentration cap.** Aggregate exposure to any one sector is capped
  (sector-cap gate) so the book is not silently one bet.

## Liquidity and data-quality guards

- **Stale-quote guard.** An order priced on a stale quote is blocked. Fresh market data
  is a precondition for any fill.
- **Spread / liquidity guard.** Names with spreads or liquidity outside tolerance are
  not traded — the fill would not be realistic and the risk is mispriced.
- **Instrument tradability.** Only instruments confirmed tradable in the security master
  are eligible. Unknown or untradable symbols are blocked.

## Settlement discipline

- **No BTST (Buy-Today-Sell-Tomorrow) reliance.** The desk does not build positions that
  depend on selling shares before they are settled and delivered. The no-BTST gate
  enforces this.

## Per-trade risk

- Sizing is **risk-first**: position size is derived from the distance to the trade's
  invalidation/stop, bounded by the per-position size cap — never from free cash alone.
- Every actionable setup must define its **invalidation** (where the thesis is wrong)
  before entry. A trade with no defined invalidation is not actionable.

## Kill-switch

- When the kill-switch is engaged, **every** order is blocked **and** audited — no
  exceptions, no partials. The kill-switch is the desk's emergency stop and overrides
  all other logic. See `NO_TRADE_RULES.md`.

## Audit and verifiability

- Every order, gate decision, fill, and block is written to a hash-chained audit log.
- **Audit coverage target is 100%** and the chain must verify intact. A session whose
  audit chain does not verify is not a trusted session, and its outcomes are not promoted
  into memory.

## Authority of these rules

These are **binding constraints**. If a number here conflicts with a request, a model
suggestion, or a playbook, **this policy wins**. The desk cannot place or simulate an
order that violates a cap.
