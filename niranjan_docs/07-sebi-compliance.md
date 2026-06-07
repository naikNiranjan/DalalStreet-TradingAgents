# 07 — SEBI Compliance (retail algo trading)

> Based on SEBI circular **"Safer participation of retail investors in Algorithmic
> trading"** (4 Feb 2025), **fully mandatory from 1 April 2026**.
> Source: https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
> This is a planning summary, **not legal advice** — confirm specifics with your broker.

## Bottom line for this project

A **self-built, personal-use** LLM agent placing **low-frequency** orders (well under
10 orders/sec) through **your own broker's API** **appears to be the intended allowed
path**, subject to confirmation by your broker — not exchange registration. You must: use a
**registered static IP**, **OAuth + 2FA daily auth**, keep it **personal/family-use only**,
and let the broker tag orders with exchange algo IDs. Our architecture already assumes all
of this. **Confirm the specifics with Angel One / Dhan before going live** — the broker is
the principal and the authority on what its API permits.

## The rules that apply to us

| Rule | What it means for us | Where handled |
|------|----------------------|----------------|
| **10 orders/sec (OPS) threshold** | Below 10 OPS **per exchange in any single second** → **no algo registration needed**. Our agent fires discrete, low-frequency orders — comfortably under. Cross it → strategy must be **registered via broker** with the exchange. | Risk engine enforces an order-rate cap well below 10/s as a safety margin. |
| **Static IP** | Only a broker-registered static IP may send API orders; others rejected. | Deploy on a fixed cloud VM / Elastic IP; register with broker. [Hosting = open Q.](./09-open-questions.md) |
| **OAuth + 2FA, daily logout** | OAuth-based auth only; 2FA every session; auto-logout before next pre-open. | Token-refresh cron (~08:45 IST) + `pyotp` TOTP in the broker adapter. |
| **Unique algo IDs / order tagging** | Every algo order (place/modify/cancel) carries an exchange-issued unique algo ID for audit. | Broker handles tagging; we keep our own audit log too. |
| **Principal–agent structure** | The **broker is the principal**, legally responsible for algo orders via its API. Third-party algo providers must be exchange-**empanelled**. | We use our own broker account directly — no third-party provider. |
| **Self-built algos = personal/family only** | Permitted for self + immediate family (spouse, dependent children/parents). **Cannot sell/distribute** without RA license + empanelment. | Project is explicitly personal-use (see [01](./01-goal-and-vision.md) non-goals). |
| **Kill switches & surveillance** | Exchanges can halt algos; brokers must enforce risk controls. | We build our own kill-switch + risk guards on top. |

## Compliance checklist (must be true before going live)

- [ ] Trading through **own broker account** (no third-party distribution).
- [ ] **Static IP** provisioned and **registered with the broker**.
- [ ] **OAuth + 2FA (TOTP)** auth; session **refreshed daily**, auto-logout respected.
- [ ] Order-rate **capped well below 10 OPS** in code (hard guard).
- [ ] Broker **algo-order tagging** confirmed enabled on the account.
- [ ] **Audit log** of every decision→order→fill retained.
- [ ] Strategy kept **personal/family-use**; not advertised, sold, or shared.
- [ ] **Kill-switch** tested and reachable manually at any time.

## Adjacent legal notes (data, not orders)

- **Data scraping** (yfinance, screener, NSE/BSE, forums) is unofficial/ToS-grey — fine for
  **personal/research** use with respectful rate-limiting + caching; **don't** redistribute
  commercially. (See [06](./06-research-data-sources.md).)
- If this ever becomes a **product for others**, the entire compliance picture changes
  (RA license, exchange empanelment, licensed data feeds). Out of scope for v1.

## Watch-list (things that can change)

SEBI's framework is new and evolving; brokers were still rolling out compliant API flows
through 2026. Re-check before go-live: exact OPS counting method, static-IP registration
process per broker, and any new retail-algo restrictions. Treat compliance as a living
requirement, not a one-time checkbox.
