# NO_TRADE_RULES

> Binding hard stops. These are the conditions under which the desk **must not trade**,
> full stop. Unlike sizing or conviction rules, these are not trade-offs to balance —
> each one is an absolute block. If any single condition below holds, **no order may be
> placed or simulated** for the affected symbol (or, where noted, for the whole book).

## Absolute blocks (the desk never trades when…)

1. **Kill-switch engaged.** When the kill-switch is on, every order is blocked and
   audited — for every symbol, in every book, with no partial fills. This overrides all
   other logic.

2. **Market closed / outside the execution window.** No order may fill outside the live
   session or outside the configured intraday execution window. Off-window analysis is
   allowed; off-window fills are not.

3. **Stale or missing critical data.** If the price/quote used to size or place a trade
   is stale (older than the freshness clock) or missing, the desk does not trade that
   name. Fresh data is a precondition, not a nicety.

4. **Broker auth invalid.** If broker authentication is not valid, the desk does not
   trade — it cannot trust that an order would be placed or reported correctly.

5. **Untradable / unknown instrument.** If the symbol is not confirmed tradable in the
   security master, the desk does not trade it.

6. **Confidence below floor.** A signal with confidence below 0.60 is not actionable.

7. **Daily loss limit breached.** Once a book breaches its daily loss limit, no new
   entries are placed for the rest of the session (exits of existing positions may still
   occur).

8. **Spread / liquidity outside tolerance.** If the spread is too wide or liquidity too
   thin to model a realistic fill, the desk does not trade the name.

9. **Insufficient buying power.** The desk never places an order it cannot fund. No
   leverage in cash-equity v1.

10. **Sector cap reached.** If a new entry would push sector exposure past the cap, the
    entry is blocked.

11. **Position-size cap reached.** No single position may exceed the per-position size
    cap.

12. **Maximum open positions reached.** No new entries beyond the max-open-positions cap.

13. **No defined invalidation.** A setup with no pre-defined invalidation/stop is not
    actionable — the desk would not know when it is wrong.

14. **Audit not writable.** If the desk cannot write the audit record for an order, it
    does not place the order. An untraceable trade is forbidden.

15. **BTST dependency.** The desk does not place trades that depend on selling shares
    before they are settled and delivered.

## On conflict

If a request, a model suggestion, or a playbook asks the desk to trade while any
condition above holds, the desk **declines and records why**. There is no override path
in paper mode, and no order-placement authority exists in the agent operating layer to
attempt one.

## Authority of these rules

These are **binding constraints** and they are **fail-closed**: when the desk is
uncertain whether a no-trade condition holds, it treats it as holding and does not trade.
