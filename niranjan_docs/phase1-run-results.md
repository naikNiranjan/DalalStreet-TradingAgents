# Phase 1 — Implementation + Routed Run Results

> Branch: `feature/azure-foundry-provider`. Run 2026-06-07. Temp keys (gitignored
> `.env`), to be rotated. No commit yet.

## What was built

| File | Change |
|------|--------|
| `tradingagents/llm_clients/azure_foundry_client.py` | **NEW** — per-model routing: `claude-*`→Anthropic endpoint, `gpt/o*`→Azure OpenAI resource, else→OpenAI-compatible Foundry endpoint. Reuses existing normalized client classes. |
| `tradingagents/llm_clients/factory.py` | Registered provider `azure-foundry`. |
| `tradingagents/llm_clients/capabilities.py` | Added capitalized `DeepSeek-V4-Pro/-Flash` (+ `^DeepSeek-V\d` pattern, date-suffixed) so they suppress `tool_choice` (DeepSeek V4 400s otherwise); added `grok-4.3`. |
| `tradingagents/llm_clients/api_key_env.py` | `azure-foundry` → `AZURE_FOUNDRY_API_KEY`. |
| `tradingagents/default_config.py` | Added `model_roles` map (consulted only when provider is azure-foundry). |
| `tradingagents/graph/trading_graph.py` | Replaced deep/quick-only build with `_build_llms()` → `(deep, quick, role_llms)`, model cache by id; passes `role_llms` to GraphSetup. |
| `tradingagents/graph/setup.py` | `role_llms` param + `_llm_for(role, tier)`; every agent now routes by role with tier fallback. |
| `tests/test_azure_foundry_provider.py` | **NEW** — 19 tests: factory, routing, capabilities, role fallback, build caching. |

## Verification

- **Regression:** full suite **310 passed** (1 integration deselected) — no behavior change for non-foundry providers.
- **New tests:** **19 passed** (routing/capabilities/role-fallback/caching, all offline).
- **Live client smoke (all 5 paths):** gpt-5.5→`NormalizedAzureChatOpenAI`, claude-sonnet-4-6→`NormalizedChatAnthropic`, grok-4.3→`NormalizedChatOpenAI`, DeepSeek-V4-Pro/-Flash→`DeepSeekChatOpenAI` — all returned `PONG`.

## Routed end-to-end run (RELIANCE.NS + HDFCBANK.NS, 2026-06-05)

**Role routing in effect** (confirmed at runtime):
```
portfolio_manager -> gpt-5.5            (NormalizedAzureChatOpenAI)
research_manager  -> claude-sonnet-4-6  (NormalizedChatAnthropic)
market            -> grok-4.3           (NormalizedChatOpenAI)
fundamentals      -> DeepSeek-V4-Pro    (DeepSeekChatOpenAI)
fallback quick    -> DeepSeek-V4-Flash  (bull/bear/trader/risk debators)
fallback deep     -> gpt-5.5
```

| Ticker | Phase 0 (single GPT-5.5) | Phase 1 (multi-model routing) | Time |
|--------|--------------------------|-------------------------------|------|
| `RELIANCE.NS` | Underweight | **Hold** | ~341s |
| `HDFCBANK.NS` | Overweight | **Overweight** | ~481s |

Both runs produced coherent, India-grounded `PortfolioDecision` output (₹ levels, FCF
₹692B, net income ₹807.75B for RIL; phased accumulation + capital/merger context for HDFC
Bank). HDFC Bank rating matched Phase 0; RELIANCE softened from Underweight→Hold — expected,
since different models legitimately weigh the same evidence differently (this is *why* the
role map is A/B-testable, and why the deterministic risk layer — not the rating alone — will
own sizing).

## Notes
- Multi-model latency is similar to Phase 0 (~5.5–8 min/ticker) — the deep models (gpt-5.5,
  claude) dominate; DeepSeek-Flash fallback keeps the many quick roles cheap/fast.
- `tool_choice` suppression for DeepSeek V4 worked (no 400s) — the capability fix held.
- Next: cost/latency-per-role benchmark (`scripts/benchmark_foundry.py`) before locking the
  map, then Phase 2 (India data layer). Routing map remains config-driven and A/B-revisable.

## Post-review fixes (2026-06-07)

Local review raised three issues; all fixed:

1. **🟠 azure-foundry tier fallbacks now encoded in config.** Added
   `azure_foundry_deep_think_llm="gpt-5.5"` / `azure_foundry_quick_think_llm="DeepSeek-V4-Flash"`
   to `default_config.py`; `_build_llms()` uses them when provider is azure-foundry. A plain
   `llm_provider=azure-foundry` run no longer falls back to the openai-only `gpt-5.4-mini`
   (verified: quick fallback resolves to DeepSeek-V4-Flash).
2. **🟠 Azure OpenAI preflight for GPT roles.** `_build_azure_openai()` now `_require_env`s
   `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`, so a GPT role with missing Azure-OpenAI
   creds fails at graph-build time with a clear message instead of late on first call.
3. **🟡 Doc role keys aligned to code.** `04` + benchmark doc role maps now use the actual
   `graph/setup.py` keys (`market`/`news`/`fundamentals`/`social`), not conceptual names.

Re-verified: **21 foundry tests pass, 331 total pass, `git diff --check` clean.**

## Status
Phase 1 code complete on branch `feature/azure-foundry-provider`, tests green (331), routed
run verified, review fixes applied. **Not committed** — ready to commit on your go.
