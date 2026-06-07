# Phase 1 — Model Benchmark Results

> Connectivity + capability benchmark of all provisioned Azure Foundry models, run
> 2026-06-07 against Niranjan's account. Tests: basic call (latency), tool-calling,
> structured output (json_schema). These are the two capabilities our agents require.
> Temp keys, gitignored `.env`, **to be rotated.**

## Results — ALL 5 MODELS READY ✅

| Model | Path | Basic | Latency | Tool-call | Structured | Verdict |
|-------|------|-------|---------|-----------|------------|---------|
| **GPT-5.5** | Azure OpenAI (`/`) | ✅ | 2.2s | ✅ | ✅ | T1 ready |
| **Claude Sonnet 4.6** | `/anthropic/v1/messages` | ✅ | 2.8s | ✅ | ✅ (via tools) | T1 ready |
| **Grok-4.3** | `/openai/v1/` | ✅ | 3.1s | ✅ | ✅ | T2 ready |
| **DeepSeek-V4-Pro** | `/openai/v1/` | ✅ | 1.1s | ✅ | ✅ | T2 ready |
| **DeepSeek-V4-Flash** | `/openai/v1/` | ✅ | 1.6s | ✅ | ✅ | T3 ready |

**Latency note:** these are *single trivial calls*. DeepSeek-V4-Pro/Flash are fastest
(1–1.6s) — confirms them for high-volume T2/T3 roles. Real agent prompts (long context +
reasoning) will be slower; per-role latency is measured properly once routing is wired.

## Key findings

1. **One OpenAI-compatible endpoint serves DeepSeek + Grok** (`/openai/v1/`, shared Foundry
   key) — so they slot into the existing `ChatOpenAI(base_url=…)` path easily.
2. **Claude needs the `/anthropic` endpoint** (it 404s on `/openai/v1/`). It works via the
   Anthropic SDK / `ChatAnthropic(base_url=…)` with the same Foundry key (`x-api-key`).
3. **GPT-5.5 is on a separate resource** (`naikn-…-swedencentral`, own key) via Azure OpenAI
   — already proven in Phase 0.
4. **Exact model IDs confirmed** via `/openai/v1/models` discovery: `grok-4.3`,
   `claude-sonnet-4-6`, `DeepSeek-V4-Pro` / `-Flash` (bare aliases resolve; versioned
   `…-2026-04-23` also exist). The resource also exposes GPT-5.x, Llama, Mistral, Phi,
   Qwen, Cohere, Kimi, etc. — a deep bench for later A/B testing.

## What this unblocks vs. still needs building

✅ **Proven:** every target model is reachable and supports tool-calling + structured
output at the raw-endpoint level.

🔧 **Still needs Phase 1 code** before the *engine* can use them:
- `azure_foundry_client.py` — a client that routes to the right path per model
  (OpenAI-compat / Anthropic / Azure-OpenAI). The existing `openai` provider forces the
  Responses API + api.openai.com, so it can't be config-hacked onto Foundry.
- Register provider `azure-foundry` in `factory.py`.
- **Capability registration:** `NormalizedChatOpenAI.with_structured_output` consults a
  per-model capability table (`capabilities.get_capabilities`). The new models
  (gpt-5.5, grok-4.3, DeepSeek-V4-*, claude-sonnet-4-6) must be added there or the engine
  won't know their structured-output method even though the endpoint supports it.
- **Role-routing layer:** map each agent role → model (config, not hardcoded), so the graph
  can use GPT-5.5 for the PM, DeepSeek for analysts, etc.

## Role map — ✅ CONFIRMED (2026-06-07, reviewer's map; A/B-revisable anytime)
Keys are the **actual `graph/setup.py` role names** (= the keys in `config["model_roles"]`).
```python
model_roles = {
    "portfolio_manager": "gpt-5.5",           # T1
    "research_manager":  "claude-sonnet-4-6", # T1
    "market":            "grok-4.3",          # T2
    "news":              "DeepSeek-V4-Pro",   # T2
    "fundamentals":      "DeepSeek-V4-Pro",   # T2
    "social":            "DeepSeek-V4-Flash", # T3 (sentiment)
}
# tier fallbacks: azure_foundry_deep_think_llm="gpt-5.5",
#                 azure_foundry_quick_think_llm="DeepSeek-V4-Flash"
# (unmapped roles: bull/bear researchers, trader, risk debators -> quick fallback)
```

## Next step
Build the `azure-foundry` client + capability entries + role routing on a **branch**
(first source-code change), then **re-run the RELIANCE.NS / HDFCBANK.NS analysis** with
multi-model routing and compare against the Phase 0 single-model (GPT-5.5) baseline.
