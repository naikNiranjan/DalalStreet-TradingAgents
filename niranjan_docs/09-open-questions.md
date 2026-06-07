# 09 — Open Questions / Decisions Needed

> Decide these **before coding**. For Niranjan + the reviewing agent. Each has a
> **recommendation** so silence = the recommended default.

---

## ✅ Decisions LOCKED (2026-06-07)

Confirmed by Niranjan; treat as settled inputs to the plan.

| # | Decision | Value |
|---|----------|-------|
| 7 | **Azure** | Subscription **ready** with AI Foundry access + GPT-5.x quota. No onboarding step needed; Phase 1 targets it directly. |
| 10 | **Brokers** | **Angel One + Dhan accounts already held.** Angel One = primary live; Dhan = sandbox/validation. No account-opening delay. |
| 1 | Go-live capital | **Tiny, fully-affordable-to-lose (₹10–25k range)** for Phase 6. |
| 2 | Paper-trading window | **1–3 months** of acceptable results before live. |
| 3 | Universe | **5–15 liquid Nifty large-caps** (RELIANCE, HDFCBANK, TCS, INFY, ICICIBANK…). No mid/small-caps or GSM/ASM names early. |
| 4 | Cadence | **Once-daily** decisions first (near open or close); not intraday. |
| 5 | Product type | **CNC delivery only**; MIS intraday + F&O are **out of the first cash-equity spine and all live v1 trading** (F&O returns later as a deferred paper-only track — see row below). |
| 6 | Risk caps | **≤15% capital per position, ≤3% daily loss limit, ≤8 open positions** (tunable). |
| 8 | Foundry Agent Service hosting | **Later** — use Foundry inference now; consider Hosted-agent post-Phase-5. |
| 9 | Models | Deep = **`gpt-5.2/5.4`**, quick = **`gpt-5.4-mini/nano`**; A/B vs DeepSeek-V4/Phi-4-mini later. |
| 13 | Secrets store | **Azure Key Vault** (prod), `.env` (dev only). |
| 14 | Package layout | **Top-level `execution/` + `india_data/`** beside `tradingagents/`. |
| 15 | Upstream sync | **Yes** — periodically pull TauricResearch updates (reinforces "add beside"). |
| — | **Hermes / Agent OS** | **Deferred operating layer**, not first build. Captured in [doc 10](./10-agent-operating-layer.md). Build our own `agent_os/` after the spine; Hermes = reference only at `references/hermes-agent`; never bypasses risk gates. |
| — | **F&O** | **In scope but deferred + paper-only** track (Tuesday expiry; Dhan option chain). Real-money F&O not ready until it survives multiple expiry cycles. See [roadmap "Deferred tracks"](./08-implementation-roadmap.md). |
| — | **Build order** | **SPINE FIRST** — one cash-equity stock, paper, realistic fills, fail-closed gates, audit, end-to-end *before* AOS/F&O breadth. |

## ❓ Still OPEN (need a decision before the relevant phase)

| # | Question | Needed by | Recommendation |
|---|----------|-----------|----------------|
| 11 | OK to **auto-store the TOTP secret seed** in Key Vault for unattended daily Angel One login? | Phase 5 | Yes (your own account; required for hands-off). |
| 12 | **Hosting + static IP** — where does it run? SEBI needs a broker-registered static IP for live orders. | Phase 6 | Small **Azure VM with a static IP**, co-located with Foundry. |
| 16 | ✅ **RESOLVED** — go-live metric thresholds. | Defined pre-Phase-0 | Locked in [11-success-metrics.md](./11-success-metrics.md): ≥30 sessions, ≥25 trades, ≤5% max DD, 0 post-limit/stale-data trades, 100% audit, ≥3 kill-switch drills, positive expectancy after costs+slippage, manual approval. Sharpe/win-rate secondary. |
| 7b | Monthly **Azure LLM budget cap**? | Phase 1 | Set a soft cap; two-tier routing + mini/nano keep cost low. |

---

## Original question list (for reference / reviewer context)

## A. Strategy & risk
1. **Capital for go-live (tiny phase)?** — Recommend a small fixed amount you can fully
   afford to lose (e.g. ₹10–25k) for Phase 6.
2. **Paper-trading window before live?** — Recommend **1–3 months** of unattended paper
   trading with acceptable risk-adjusted results as the hard gate.
3. **Trading universe?** — Recommend starting with **5–15 liquid Nifty large-caps**
   (RELIANCE, HDFCBANK, TCS, INFY, ICICIBANK...). Avoid mid/small-caps + GSM/ASM names early.
4. **Cadence?** — Recommend **once-daily** decisions near open or close first (not
   intraday), to stay simple + well under SEBI's order-rate line.
5. **Product type?** — Recommend **CNC delivery** first (no leverage/margin); MIS intraday
   and F&O explicitly **out of scope for v1**.
6. **Risk caps?** — Need numbers: max % capital per position, daily loss limit %, max open
   positions. Recommend: ≤15% per name, ≤3% daily loss limit, ≤8 open positions.

## B. Azure
7. **Subscription & budget?** — Do you have an Azure subscription with Foundry access +
   quota for `gpt-5.x`? Newest models may need eligibility approval (lead time). Monthly
   LLM budget cap?
8. **Foundry Agent Service hosting — now or later?** — Recommend **later** (use Foundry
   *inference* now; consider Hosted-agent deployment post-Phase-5). It's additive.
9. **Model picks confirm?** — Deep = `gpt-5.2/5.4`; quick = `gpt-5.4-mini/nano`. OK, or
   prefer DeepSeek-V4/Phi-4-mini for cost? (Can A/B later.)

## C. Broker
10. **Primary broker confirm?** — Recommend **Angel One** (free + automatable TOTP) live,
    **Dhan** sandbox for validation. Do you already have accounts? (Account opening + API
    enablement has lead time.)
11. **Are you OK auto-storing the TOTP secret seed** in a secret store for unattended
    daily auth? (Required for hands-off operation; it's your own account.)

## D. Infra / ops
12. **Hosting + static IP?** — SEBI needs a broker-registered static IP for live orders.
    Options: a small **cloud VM (Azure) with a static/Elastic IP** (recommended, co-located
    with Foundry), or home static IP. Decide before Phase 6.
13. **Secrets store?** — Recommend **Azure Key Vault** in prod, `.env` for dev only.
14. **Where do the new packages live?** — Recommend **top-level `execution/` + `india_data/`**
    beside the upstream `tradingagents/` package to keep upstream merges clean. Alt: nest
    under `tradingagents/`. Reviewer to confirm.

## E. Process
15. **Upstream sync policy?** — Do we periodically pull TauricResearch updates? Recommend
    **yes**, which reinforces "add beside, don't rewrite."
16. **Definition of "good enough to go live"?** — Concrete metric thresholds (min Sharpe?
    max drawdown tolerance? min hit-rate?) — set these with the reviewer before Phase 6.

---

## For the reviewing agent — please assess
- Is the **architecture** ([02](./02-architecture.md)) sound, safe, and SEBI-compliant?
- Are the **Azure / broker / data** picks the best free/low-cost options for 2026? Anything
  better or cheaper we missed?
- Is the **phasing** ([08](./08-implementation-roadmap.md)) genuinely safe — does paper
  trading fully precede real money, with deterministic risk gates around the LLM?
- What's **missing or risky**? (e.g. slippage modeling, corporate-action handling, data
  outages, LLM hallucination guards, tax/reporting, disaster recovery.)
- Record findings here under each item, or append a `10-review-findings.md`.
