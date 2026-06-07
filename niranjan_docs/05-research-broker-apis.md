# 05 — Research: Indian Broker APIs

> Current as of June 2026. India's broker-API rules are in flux due to SEBI's algo
> framework (mandatory **1 April 2026** — see [07](./07-sebi-compliance.md)). Verify
> exact numbers against official docs before coding. Third-party-sourced figures flagged.

## Comparison table

| Broker | API cost | Data cost | Full automation? | Daily auth | Sandbox? | Python SDK | Historical depth |
|--------|----------|-----------|------------------|------------|----------|------------|------------------|
| **Dhan** | **Free** | **₹499/mo** (live feed + deep hist) | Yes, SEBI-compliant | API key+secret → 24h token (auto-mintable) | **Yes — true sandbox** | `dhanhq` | minute up to **5 yrs**, OI |
| **Angel One (SmartAPI)** | **Free** | **Free** (incl. historical) | Yes | API key + **TOTP** → automatable via `pyotp` | No | `smartapi-python` | 1-min 30d/req; daily decades |
| **Zerodha (Kite)** | Free | **₹500/mo** | Yes, **static IP required for orders** | Interactive daily login, **not officially automatable** | No | `kiteconnect` (most mature) | minute 60d, day 2000d/req |
| **Upstox** | **Free** | **Free** | Yes (+HFT endpoint) | OAuth daily (read-only 1-yr tokens for data only) | No | `upstox-python-sdk` | day/wk/mo from **Jan 2000** |
| **Fyers** | **Free** | **Free** | Yes | OAuth daily, scriptable | **No** (common misconception) | `fyers-apiv3` | minute ~1–2 yr |
| **Flattrade/Shoonya (Finvasia)** | **Free** | **Free** | Yes | userid+pwd+**TOTP** → automatable via `pyotp` | No | `ShoonyaApi-py` | available, free |

## Decisions

### Build & validate (free)
- **Dhan** — the only one with a **true closed sandbox** for risk-free integration tests;
  free order APIs; 1-year API key to auto-mint the daily 24h token.
- **Angel One** — genuinely **₹0 including historical data**; **TOTP fully automatable**
  with `pyotp`, the best unattended-friendly free option.

### Primary live broker → **Angel One SmartAPI**
Free API **and** free data, and **automatable daily auth** (TOTP via `pyotp`) — the
single most important property for an unattended agent. WebSocket streams ticks (we build
candles). No sandbox, so we paper-trade via our **own realistic fill simulator** against
live quotes (slippage/spread/rejection/partials).

### Backup live broker → **Flattrade/Shoonya (Finvasia)**
Free API + free data + scriptable TOTP; cheapest end-to-end (zero delivery brokerage).

### Why not Zerodha first
Best SDK/ecosystem, but: **₹500/mo data**, **static IP mandatory for order placement**
(since Apr 2025), and **daily login not officially automatable** (browser-based by
design; headless-login workarounds are ToS-grey and fragile). Keep as a "mature option if
we'll pay + accept manual-ish auth" — not the default for unattended automation.

### Abstraction
Build our **own thin `Broker` adapter** (see [02](./02-architecture.md)). Optionally adopt
**[OpenAlgo](https://github.com/marketcalls/openalgo)** later — it already unifies
Shoonya/Flattrade/Dhan/others behind one API.

## The daily-token re-auth problem (critical)

SEBI/exchange rules cap session tokens at ~24h and force logout before each pre-open, so
**every broker needs a fresh token daily.** Patterns:

- **Easiest (fully scriptable):** Angel One, Shoonya/Flattrade — store the **TOTP secret
  seed** (not the 6-digit code) in a secret store; mint the code at login with
  `pyotp.TOTP(secret).now()`. A cron at ~08:45 IST gets the day's token before open.
- **Key-mints-token:** Dhan (1-yr key+secret → daily 24h token). Upstox (daily OAuth for
  trading; 1-yr read-only tokens for data/portfolio).
- **Hardest:** Zerodha / Fyers — interactive OAuth, no official TOTP path.

**Recommended ops pattern:**
1. Scheduled **token-refresh job** (~08:45 IST) → writes token to secret store, never source.
2. Agent reads token at runtime; on 401 → re-auth (or alert if manual approval needed).
3. **Static IP** (fixed cloud VM / Elastic IP / VPC NAT) — mandatory for Zerodha orders &
   Upstox static-IP tokens; good practice everywhere under SEBI.
4. Keep all auth inside the **broker adapter** so the agent core stays broker-agnostic.

## Flagged uncertainties (verify before building)
- Dhan/Shoonya exact rate-limit tables (third-party-sourced).
- Whether Dhan's sandbox supports **live-data paper fills** or only request testing.
- Upstox post-May-2025 numeric order-rate categories.
- Fyers sandbox status (treat as **none**).
- Exact product-type naming (CNC/MIS/NRML) per broker order docs.

## Key official sources
- SEBI algo circular: https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
- Dhan: https://dhanhq.co/docs/v2/ · https://github.com/dhan-oss/DhanHQ-py
- Angel One: https://smartapi.angelbroking.com/docs · https://github.com/angel-one/smartapi-python
- Zerodha: https://kite.trade/docs/connect/v3/
- Upstox: https://upstox.com/developer/api-documentation/ · https://github.com/upstox/upstox-python
- Fyers: https://myapi.fyers.in/docsv3 · https://pypi.org/project/fyers-apiv3/
- Shoonya/Flattrade: https://shoonya.com/api-documentation · https://github.com/Shoonya-Dev/ShoonyaApi-py
- OpenAlgo: https://github.com/marketcalls/openalgo
