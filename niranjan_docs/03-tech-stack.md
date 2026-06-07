# 03 — Tech Stack (decisions + rationale)

> Every component we'll use, with the chosen option in **bold** and why. Detailed
> evidence is in the research docs ([04](./04-research-azure-foundry.md),
> [05](./05-research-broker-apis.md), [06](./06-research-data-sources.md)).

## Language / runtime
- **Python 3.13** (repo supports ≥3.10; pin 3.13 to match upstream `conda` guidance).
- **uv** for env + locking (repo already ships `uv.lock`).

## Orchestration (keep)
- **LangGraph** + **LangChain core** — already the backbone; no change.
- Keep checkpoint/resume (`langgraph-checkpoint-sqlite`) and the decision-log memory.

## LLM / AI — Azure AI Foundry native
| Concern | Choice | Why |
|---------|--------|-----|
| Client library | **`langchain-azure-ai` → `AzureAIOpenAIApiChatModel`** | One client reaches the broadest Foundry catalog (GPT, DeepSeek, Llama, Phi, Grok) via the OpenAI-compatible endpoint; reliable tool-calling + structured output. Avoids the deprecated `AzureAIChatCompletionsModel` and its `create_agent` structured-output bug. |
| Routing | **Role-based model routing** (config, not hardcoded) | Each agent role → a model by tier; A/B-testable. See [04 Decision 2](./04-research-azure-foundry.md#decision-2--role-based-model-routing-not-just-deepquick). |
| T1 deep/critical | **GPT-5.5**, **Claude Sonnet 4.6** | Research Mgr, Portfolio Mgr, final risk review, thesis validation. |
| T2 strong+cheaper | **Grok 4.x**, **DeepSeek-V4-Pro** | News/macro/regime analysts, second opinions, later F&O analyst. |
| T3 fast utility | **DeepSeek-V4-Flash** (+ later mini/nano/Phi) | Summarize, classify sentiment, filter headlines, "changed?" checks. |
| Lock defaults via | **Benchmark script** (latency/tool/structured/cost in-account) | Don't assume cost from public pricing — measure. |
| Auth | **Microsoft Entra ID** (`DefaultAzureCredential` dev, `ManagedIdentityCredential` prod) | Keyless, production best practice; disable local key auth. |
| Observability | **`AzureAIOpenTelemetryTracer` → Azure Monitor / App Insights** | Per-node spans, `agent_id` tags, 90-day traces. |
| Endpoint | Foundry **project endpoint** `https://<res>.services.ai.azure.com/api/projects/<proj>` | Works across `langchain-azure-ai` classes with Entra ID. |

**Integration point in repo:** add `azure-foundry` to `llm_clients/factory.py` + a new
`azure_foundry_client.py`; everything else (graph, agents) is provider-agnostic already.

## Broker / execution — India
| Concern | Choice | Why |
|---------|--------|-----|
| Build/validate broker | **Dhan** (true sandbox) **+** **Angel One SmartAPI** (free data) | Dhan is the only one with a real closed sandbox; Angel is fully free incl. historical. |
| Primary live broker | **Angel One SmartAPI** | Free API **and** free data; **TOTP auth fully automatable via `pyotp`** → unattended daily login. |
| Backup live broker | **Flattrade/Shoonya (Finvasia)** | Free API + free data + scriptable TOTP; cheapest end-to-end. |
| Abstraction | **Own `Broker` adapter** (optionally adopt **OpenAlgo** later) | Broker-agnostic core; swap without touching agents. |
| Python SDKs | `smartapi-python` (Angel), `dhanhq` (Dhan), `pyotp` (TOTP) | Official/maintained. |
| Paper trading | **Custom fill simulator** (realistic: slippage/spread/rejection/partials) | Don't depend on a vendor sandbox; works on any broker; honest P&L. |

**Why not Zerodha first:** great SDK but **₹500/mo data**, **static IP required for orders**,
and **daily login is not officially automatable** (hard for unattended agents).

## India data layer
| Need | Primary (free) | Augment | Library |
|------|----------------|---------|---------|
| Prices / intraday / historical | **Angel One SmartAPI** | yfinance (`.NS/.BO`, indices, EOD fallback) | `smartapi-python`, `yfinance` |
| Fundamentals | **screener.in (scrape)** + **Tickertape** | NSE/BSE filings | `Bharat-SM-Data`, custom |
| News | **Moneycontrol + ET + Business Standard RSS** | NewsData.io free tier; BSE announcements | `feedparser`, `requests` |
| Macro context | **data.gov.in API** + **RBI DBIE** + **NSE FII/DII** | MoSPI eSankhyiki | `datagovindia`, `jugaad-data` |
| Sentiment | **r/IndianStockMarket (Reddit API)** + **Telegram Bot API** | Moneycontrol/ValuePickr forums | `praw`, `python-telegram-bot` |
| Mechanics (holidays, circuits, ASM/GSM, F&O ban) | **NSE site** via `jugaad-data`/`nsepython` | `bse` for BSE | `jugaad-data`, `nsepython`, `bse` |

**Drop for India:** Alpha Vantage (weak India fundamentals, harsh limits), StockTwits (US).
**Keep but re-point:** Reddit (→ India subs), benchmark map (already Nifty/Sensex).

## India cost & rules engine (new, deterministic)
- **STT / stamp duty / GST / SEBI turnover / exchange charges / DP charges** modeled per
  segment (delivery/intraday/F&O) for correct net P&L. Reference: Zerodha brokerage calc.
- **Market calendar:** NSE trading + settlement holidays; hours 09:15–15:30 IST; T+1.
- **Surveillance:** circuit/price-band, ASM, GSM, F&O ban (MWPL) checks before any order.

## Backtesting
- **`backtrader`** (already a dependency) wired with the **India cost model** + circuit/holiday
  awareness. Historical OHLCV from Angel/yfinance. Metrics: CAGR, Sharpe, Sortino, max DD,
  hit-rate, turnover, cost drag.

## Scheduling / ops
- **Token-refresh cron** (~08:45 IST) mints the broker's daily token (Angel/Shoonya via
  `pyotp`); writes to a **secret store**, never source.
- **Run loop**: market-hours-gated daily/intraday cadence over the ticker universe.
- **Secrets**: **Azure Key Vault** (prod) / `.env` (dev only).
- **Static IP**: a fixed cloud VM / Elastic IP (required for some brokers; good practice
  under SEBI). Hosting target is an [open question](./09-open-questions.md).

## Testing / quality
- **pytest** (repo already configured, ~30 tests). Add tests for: paper simulator, risk
  guards, cost model, broker adapters (mocked), calendar/circuit logic.
- Keep structured-output + signal-processing tests; add India-data provider tests.

## Cross-cutting
- **Config profiles**: `paper` (default) and `live` selectable via `TRADINGAGENTS_*` /
  new `EXECUTION_*` env vars; reuse the existing env-override mechanism.
- **Observability**: Azure App Insights for LLM + structured logs for execution.
- **Audit**: append-only decision→order→fill log (file/DB) for SEBI trail + post-mortems.

## What stays exactly as-is
LangGraph orchestration, agent role structure, debate logic, checkpointing, reflection
memory, structured outputs, Docker, the test harness, and the Nifty/Sensex benchmark map.
