# Phase 0 — Baseline Validation Results

> **Scope:** prove the *untouched* engine works on Indian tickers before changing any code.
> No code modified. No credentials committed. Run date: 2026-06-07.

## Environment

| Item | Value |
|------|-------|
| OS | macOS (darwin 25.3.0) |
| Python | **3.13.12** (system 3.9.6 too old; used `python3.13`) |
| Env manager | `venv` at `.venv/` (gitignored). `uv`/`conda` not installed. |
| Install | `pip install -e .` — **success**, all deps resolved |
| Key versions | langchain-core 1.4.1 · langgraph 1.2.4 · yfinance 1.4.1 · pandas 3.0.3 · openai 2.41.0 · anthropic 0.107.0 · backtrader 1.9.78.123 |

## Step 1 — Import smoke test ✅
`TradingAgentsGraph`, `DEFAULT_CONFIG`, `create_llm_client` all import cleanly.
India benchmark map confirmed live: **`.NS` → `^NSEI` (Nifty 50)**, **`.BO` → `^BSESN` (Sensex)**.

## Step 2 — Test suite ✅
`pytest -m "not integration"` → **310 passed, 1 deselected (integration), 75 subtests passed** in ~61s.
Only benign warnings (unknown-model notes, one `SyntaxWarning` in `cli/utils.py`). No failures.

## Step 3 — India data path (credential-free) ✅
Validated the data layer works on NSE tickers **without any LLM key**:

| Ticker | yfinance price fetch | Identity resolution |
|--------|---------------------|---------------------|
| `RELIANCE.NS` | ✅ 5 rows, last close ₹1,291.00 | ✅ Reliance Industries Limited · Energy · Oil & Gas Refining & Marketing · exch NSI |
| `HDFCBANK.NS` | ✅ 5 rows, last close ₹747.05 | ✅ HDFC Bank Limited · Financial Services · Banks - Regional · exch NSI |

This proves prices, company identity, and benchmark resolution all work for India out of the box.

## Step 4 — Full end-to-end analysis run (LLM) ✅

**Model used:** GPT-5.5 via **Azure OpenAI** (Sweden Central, `cognitiveservices.azure.com`
endpoint, api-version `2024-12-01-preview`), provider=`azure`, deep=quick=`gpt-5.5`. Creds
in gitignored `.env` only (temp keys, to be rotated). The Claude Sonnet 4.6 (Azure Foundry
`/anthropic/v1/messages`) model is deferred to Phase 1 — it needs custom client wiring the
untouched engine doesn't have.

- **Connectivity:** repo's own `create_llm_client('azure','gpt-5.5')` → invoke → returned
  `PONG`. Auth + endpoint + deployment confirmed.
- **Scope of run:** analysts = `[market, fundamentals]`, 1 debate round, 1 risk round — a
  lean but **genuinely end-to-end** pass (analysts → bull/bear → trader → risk debate →
  Portfolio Manager → rating). Trade date `2026-06-05`.

| Ticker | Final rating | Time | India-grounded? |
|--------|-------------|------|-----------------|
| `RELIANCE.NS` | **Underweight** | ~296s | ✅ ₹ levels, SMAs, MACD/RSI, 52-wk-low context |
| `HDFCBANK.NS` | **Overweight** | ~351s | ✅ bank KPIs (NIM/CASA/NPA/CET1), TTM rev ₹2.83T, ₹ levels |

Both produced coherent, structured `PortfolioDecision` markdown with India-specific
reasoning. The full multi-agent graph executes correctly on NSE tickers with no code changes.

## Verdict — Phase 0 COMPLETE ✅
Baseline is **healthy and proven**: clean install on Python 3.13, **310 tests green**, India
data path works, and the **full multi-agent pipeline runs end-to-end on `RELIANCE.NS` and
`HDFCBANK.NS`** via Azure GPT-5.5, returning sensible India-grounded ratings. **No blockers
for Phase 1.**

### Observations carried into Phase 1
- A full run is ~5–6 min/ticker on GPT-5.5 reasoning with just 2 analysts → cost/latency
  matters. Phase 1 two-tier routing (cheap quick-think model) + the deferred "wake only if
  changed" logic are well justified.
- Claude Sonnet 4.6 via Azure Foundry's Anthropic endpoint uses `x-api-key` +
  `anthropic-version` headers and a `/anthropic/v1/messages` path → Phase 1 will add an
  `azure-foundry` client (or base_url override on the anthropic client) to wire it in.
- yfinance supplied usable India fundamentals + technicals here; Phase 2 still swaps to
  Angel One as primary for reliability/freshness.

## Notes / observations for later phases
- `uv` is not installed though the repo ships `uv.lock`; we used `venv`+`pip`. Fine for now;
  consider `uv` later for reproducible locks.
- The engine is **rating-only** as documented — confirmed no execution path exists (Phase 3 work).
- yfinance for India worked first try here, but research flagged it as delayed/intraday-limited;
  Phase 2 will add Angel One as the primary price source.
