# 04 — Research: Azure AI Foundry

> Current as of June 2026. Microsoft has rebranded **Azure AI Foundry → Microsoft
> Foundry**; docs/SDKs carry both names. Verify model availability + pricing before
> rollout (several models are rolling/preview and need eligibility approval).

## What Foundry is (and how it maps to our app)

- **Microsoft Foundry** is the unified platform; **Azure OpenAI is now a component inside
  it**, not a separate product.
- Catalog splits into **Models sold by Azure** (Azure-hosted, enterprise SLA — all Azure
  OpenAI + selected DeepSeek/Llama/Mistral/Grok/Cohere) and **partner/community models**
  (incl. Anthropic Claude, Hugging Face), served **serverless (per-token)** or **managed
  compute (per-GPU-hour)**.
- **For us:** we want one client that reaches the broadest catalog with reliable
  tool-calling + structured output. That points to the **OpenAI-compatible Foundry
  endpoint**.

## Decision 1 — Client library

**Use `langchain-azure-ai` → `AzureAIOpenAIApiChatModel`** (OpenAI-compatible endpoint).

- Broadest single-client catalog reach (GPT, Cohere, Llama, Phi, DeepSeek...).
- Tool calling ✅, structured output ✅, streaming ✅, async ✅, token usage ✅.
- **Avoid** `AzureAIChatCompletionsModel` (Azure AI Inference SDK): those classes are
  **deprecated in favor of OpenAI-compatible APIs**, and there's a known bug —
  `ValueError: Unsupported response_format {'type':'json_schema'...}` with newer
  `create_agent` + `response_format` ([azure-sdk-for-python #44201](https://github.com/azure/azure-sdk-for-python/issues/44201)).
  Our app leans heavily on structured outputs, so we steer clear.

```bash
pip install -U langchain-azure-ai azure-identity
pip install -U "langchain-azure-ai[tools]" "langchain-azure-ai[opentelemetry]"
```
```python
from azure.identity import DefaultAzureCredential
from langchain_azure_ai.chat_models import AzureAIOpenAIApiChatModel

model = AzureAIOpenAIApiChatModel(
    project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],  # Entra ID path
    credential=DefaultAzureCredential(),
    model="gpt-5.2",
)
```

## Decision 2 — ROLE-BASED model routing (not just deep/quick)

**Upgraded design (2026-06-07):** the Azure Foundry client is a **model-routing layer**, not
a two-model switch. Each agent role maps to a model by **tier**, and the mapping is **config,
never hardcoded**, so we can A/B test and let benchmark results pick defaults.

### Models actually provisioned (Niranjan's account)

| Model | Endpoint style | Resource | Provisioned |
|-------|----------------|----------|-------------|
| **GPT-5.5** | Azure OpenAI (`/`) | `naikn-…-swedencentral` | ✅ |
| **Claude Sonnet 4.6** (`claude-sonnet-4-6`) | Anthropic (`/anthropic/v1/messages`) | `contextdb-resource` | ✅ |
| **Grok 4.x** | Azure AI inference (`/models/chat/completions`) | `contextdb-resource` | ✅ |
| **DeepSeek-V4-Pro** | OpenAI-compatible (`/openai/v1/`) | `contextdb-resource` | ✅ |
| **DeepSeek-V4-Flash** | OpenAI-compatible (`/openai/v1/`) | `contextdb-resource` | ✅ |

> Grok 4.3 supports function calling + structured outputs. DeepSeek's official API lists
> `deepseek-v4-flash` / `deepseek-v4-pro`; older `deepseek-chat`/`deepseek-reasoner` aliases
> deprecate 2026-07-24 — use the V4 names.

### Tiers → roles (initial mapping, benchmark-confirmed before lock)

| Tier | Models | Roles |
|------|--------|-------|
| **T1 — deep/critical** | GPT-5.5, Claude Sonnet 4.6 | Research Manager, Portfolio Manager, final risk review, hard thesis validation, post-trade lesson synthesis |
| **T2 — strong+cheaper** | Grok 4.x, DeepSeek-V4-Pro | News analyst, macro analyst, market regime, second-opinion review, (later) option-chain/F&O analyst |
| **T3 — fast/cheap utility** | DeepSeek-V4-Flash (+ later GPT-mini/nano, Phi) | summarization, sentiment classification, data cleanup, headline filtering, report compression, "anything important changed?" checks |

### Config shape — ✅ CONFIRMED initial map (2026-06-07; A/B-revisable, NOT hardcoded)
Keys are the **actual agent role names** from `graph/setup.py` (use these exact keys in
`config["model_roles"]`). Unmapped roles fall back to the tier defaults
(`azure_foundry_deep_think_llm` / `azure_foundry_quick_think_llm`).
```python
model_roles = {
    "portfolio_manager": "gpt-5.5",           # T1 deep
    "research_manager":  "claude-sonnet-4-6", # T1 deep
    "market":            "grok-4.3",          # T2
    "news":              "DeepSeek-V4-Pro",   # T2
    "fundamentals":      "DeepSeek-V4-Pro",   # T2
    "social":            "DeepSeek-V4-Flash", # T3 (sentiment analyst)
    # unmapped: bull_researcher, bear_researcher, trader, aggressive_debator,
    # neutral_debator, conservative_debator -> azure_foundry_quick_think_llm
}
# tier fallbacks (config keys): azure_foundry_deep_think_llm = "gpt-5.5",
#                               azure_foundry_quick_think_llm = "DeepSeek-V4-Flash"
```

**Cost caveat (reviewer):** do NOT assume cost from public pricing — Foundry cost depends on
deployment + account/program. **Benchmark latency + $ per role inside the actual account**
before locking defaults. → see [Decision 7](#decision-7--benchmark-before-locking-defaults).

⚠️ Some newest tiers may need **registration/eligibility approval** — but the five above are
already provisioned, so no lead time for v1.

## Decision 3 — Authentication

- **Production: Microsoft Entra ID (keyless).** `DefaultAzureCredential()` for dev (works
  via `az login`); **`ManagedIdentityCredential`** in Azure. After setup, **disable local
  key auth**.
- Requires a **custom subdomain** on the resource; assign RBAC role **Foundry User** (or
  `Cognitive Services OpenAI User`). Token scope `https://ai.azure.com/.default`.
- **API keys** only for early prototyping (env var / Key Vault), migrate off before prod.

**Env vars:**
```bash
AZURE_AI_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
# prototyping-only key path:
OPENAI_BASE_URL="https://<resource>.services.ai.azure.com/openai/v1"
OPENAI_API_KEY="<key>"
```

## Decision 4 — Orchestration: keep LangGraph; Foundry Agent Service optional

Microsoft's guidance is **complementary, not either/or**. Keep our LangGraph branching/
cycles/conditional routing (Foundry prompt agents can't do that). **Optionally**, later,
package the graph as a **Foundry Hosted agent** (public preview) for managed hosting,
autoscaling, per-agent Entra identity, VNet isolation, and portal-visible traces.

- Hosted-agent caveats: preview; billed on CPU+memory across active sessions; tools aren't
  auto-injected (connect via Toolbox MCP); the default `from_langgraph()` adapter fails
  for non-streaming tool use; classic "Connected Agents" not available.
- **Our stance:** use Foundry **inference** now; consider Hosted-agent deployment later.
  It's additive, not a rewrite. → tracked in [09](./09-open-questions.md).

## Decision 5 — Observability (do from day one)

```python
from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer
tracer = AzureAIOpenTelemetryTracer(connection_string=APP_INSIGHTS_CONN,
                                    enable_content_recording=False,  # PII off in prod
                                    agent_id="dalalstreet")
graph.invoke(state, config={"callbacks": [tracer]})
```
Per-node spans, `gen_ai.agent.id` per agent, viewable in Foundry portal / Azure Monitor.
The repo already passes `callbacks` into LLM construction — wiring this is low-effort.

## Decision 6 — Cost model + resilience

- Billing: **serverless PAYG** (default for bursty multi-agent traffic), **PTUs** (only
  worth it at sustained ~150–200M tokens/mo), **Batch** (latency-tolerant).
- **Levers:** two-tier routing, Model Router, prompt caching, minimal `max_tokens`.
- **Resilience (critical for fan-out):** multi-agent parallel calls trip **sub-minute
  429s** (RPM enforced over 1–10s windows; TPM on *estimated* tokens). Mitigate with
  backoff+jitter, honor `retry-after-ms` / `x-ratelimit-*` headers, minimize `max_tokens`,
  and optionally spread deployments across regions (quota is per-region).

## Decision 7 — Benchmark before locking defaults

Before fixing the role→model map, **measure inside the actual account** (not from public
pricing). A benchmark script tests every provisioned model on:
- **latency** (basic call round-trip)
- **tool-call reliability** (does `bind_tools` produce valid tool calls? — analysts need this)
- **structured-output reliability** (does `with_structured_output(schema)` return a valid
  object? — Research Manager / Trader / Portfolio Manager need this)
- **approximate cost** (token usage from response metadata)

Only models that pass tool-call + structured-output go into T1/T2 manager/analyst roles;
utility-only models can serve T3. Results recorded in `phase1-benchmark-results.md`.

## Decision 8 — Phase 1 build list (what to create / touch)

```
tradingagents/llm_clients/
  azure_foundry_client.py     # NEW — multi-path Foundry client (below)
  factory.py                  # TOUCH — register provider "azure-foundry"
  capabilities.py             # TOUCH — structured-output method per new model
  model_catalog.py            # TOUCH — Foundry model metadata
  api_key_env.py              # TOUCH — provider → API-key env var
tradingagents/graph/
  trading_graph.py:88         # TOUCH — replace deep/quick-only client build with
                              #   a role→LLM map (build each role's model once, cache by id)
  setup.py:49                 # TOUCH — pass the role→LLM map into agent creation so each
                              #   agent gets its role's model (not just quick/deep)
tradingagents/
  default_config.py           # TOUCH — add `model_roles` map + foundry endpoints config
scripts/
  benchmark_foundry.py        # NEW — latency / tool / structured / cost per model
tests/
  test_azure_foundry_*.py     # NEW — client paths, capabilities, role routing
```

> **Role routing is NOT just a new client.** The current engine
> ([`trading_graph.py:88`](../tradingagents/graph/trading_graph.py)) builds only
> `deep_thinking_llm` + `quick_thinking_llm`, and
> [`graph/setup.py:49`](../tradingagents/graph/setup.py) wires those two into every agent.
> True role routing requires passing a **role→LLM map** into agent creation, replacing the
> quick/deep-only wiring. Keep deep/quick as the **fallback** when a role is unmapped.

> **Capability registration needs care (reviewer):** OpenAI-compatible models (DeepSeek,
> Grok) route through `capabilities.py` + `NormalizedChatOpenAI`. **Claude (Anthropic path)
> and GPT-5.5 (Azure OpenAI path) use different client classes and may need their own
> structured-output handling + dedicated tests** — do NOT assume the OpenAI-compatible
> capability table covers all three paths. Test structured output per path.

**Provider paths the Foundry client must support** (all on `contextdb-resource`, one key):
| Path | URL suffix | Models | Client under the hood |
|------|-----------|--------|----------------------|
| OpenAI-compatible | `/openai/v1/` | DeepSeek-V4-Pro/Flash (and likely Grok, GPT-style) | `langchain_openai.ChatOpenAI(base_url=…)` |
| Anthropic | `/anthropic/v1/messages` | claude-sonnet-4-6 | `langchain_anthropic.ChatAnthropic(base_url=…)` |
| Azure AI inference | `/models/chat/completions?api-version=2024-05-01-preview` | Grok | OpenAI-compatible or azure-ai-inference |
| Azure OpenAI (separate resource) | `/` | gpt-5.5 | `AzureChatOpenAI` (already working, Phase 0) |

The existing factory already routes `_OPENAI_COMPATIBLE` providers through `ChatOpenAI`
with a `base_url` — so DeepSeek/Grok slot in cleanly; Claude reuses the anthropic client with
a base_url override. Role routing sits **above** the per-model client.

## Gotchas to remember

- Model **deprecation/retirement** — pin versions in config; track the retirements pages.
- New subscriptions may start at **0 TPM** for newest models until quota is requested.
- Content filtering on by default for serverless (billed separately).
- LangChain/LangGraph **tracing is Python-only**; needs `langchain-azure-ai>=0.1.0`.

## Key official sources
- LangChain + Foundry get-started: https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain
- Foundry Models overview: https://learn.microsoft.com/en-us/azure/foundry/concepts/foundry-models-overview
- Models sold by Azure: https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure
- Model choice guide: https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/model-choice-guide
- Auth & authorization: https://learn.microsoft.com/en-us/azure/foundry/concepts/authentication-authorization-foundry
- Agent Service overview: https://learn.microsoft.com/en-us/azure/foundry/agents/overview
- LangGraph with Agent Service: https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-agents
- LangChain/LangGraph tracing: https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-traces
- Quotas & limits: https://learn.microsoft.com/en-us/azure/foundry/openai/quotas-limits
- Model Router: https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router
- Pricing: https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/aoai/
