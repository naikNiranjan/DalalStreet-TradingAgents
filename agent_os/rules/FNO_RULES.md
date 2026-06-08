# FNO_RULES

> Binding constraints for Futures & Options. **F&O is deferred and PAPER-ONLY.** This
> file is written now so the rule exists before the capability does — but no F&O playbook
> is active and no F&O order may be placed in v1. F&O activation is gated separately by
> the success-metrics F&O gate and is out of scope for the first slice.

## Status

- **Deferred.** The desk trades **cash equity only** in v1. There are no active F&O
  playbooks. Any F&O activity, when eventually enabled, is **paper-only** first and must
  pass its own go-live gate before being considered.
- This document binds *if and when* F&O is ever enabled. Until then, the operative rule
  is simply: **the desk does not trade F&O.**

## Hard F&O constraints (apply the moment F&O is ever enabled)

1. **Paper-only first.** F&O may only be traded in paper mode until a separate F&O
   go-live gate is explicitly passed. No live F&O without that sign-off.

2. **No naked option selling.** The desk does not sell options without a defined,
   funded hedge or covering position. Undefined-risk short option exposure is forbidden.

3. **Defined-risk only.** Every F&O position must have a bounded, pre-computed maximum
   loss before entry. Strategies whose loss is unbounded are not permitted.

4. **No selling for premium without protection.** Income strategies that rely on
   collecting premium must be structured (spreads, covered positions) so the maximum
   loss is known and capped.

5. **Margin discipline.** F&O positions respect exchange and broker margin requirements;
   the desk never assumes margin it does not have, and never trades to a margin call.

6. **Expiry and assignment awareness.** Positions are managed with explicit awareness of
   expiry, settlement, and assignment risk — never carried blindly into expiry.

7. **Same spine, same gates.** F&O, when enabled, flows through the identical execution
   spine and gate chain as cash equity: data → SignalDecision → gates → broker → audit.
   The agent operating layer gains no new order authority for F&O.

8. **Liquidity and OI guards.** F&O instruments must clear liquidity and open-interest
   checks; thin or illiquid contracts are not traded.

## Authority of these rules

These are **binding constraints**. Until the F&O capability is explicitly enabled through
its own gate, the desk **does not trade F&O at all**. When F&O is eventually enabled, the
above constraints are non-negotiable, and where they conflict with a request or a model
suggestion, **these rules win**.
