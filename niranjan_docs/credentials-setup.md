# Credentials Setup — WHERE to add your keys

> **TL;DR:** put keys in the **gitignored `.env`** at the repo root (dev), and in **Azure
> Key Vault** later (prod). Never paste keys into committed files, chat, or git.
> `.env` is already in `.gitignore` (verified). After testing, **rotate the temp keys.**

## The env-var scheme (this is where keys go)

Add these to `DalalStreet-TradingAgents/.env`:

```bash
# ── GPT-5.5  (Azure OpenAI — its own resource) ───────────────────────────────
AZURE_OPENAI_API_KEY=<gpt-5.5 key>
AZURE_OPENAI_ENDPOINT=https://naikn-modqynl8-swedencentral.cognitiveservices.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-5.5
OPENAI_API_VERSION=2024-12-01-preview

# ── Foundry resource (Claude, Grok, DeepSeek — ONE shared key) ───────────────
AZURE_FOUNDRY_API_KEY=<contextdb-resource key>
AZURE_FOUNDRY_OPENAI_ENDPOINT=https://contextdb-resource.services.ai.azure.com/openai/v1/
AZURE_FOUNDRY_ANTHROPIC_ENDPOINT=https://contextdb-resource.services.ai.azure.com/anthropic
AZURE_FOUNDRY_MODELS_ENDPOINT=https://contextdb-resource.services.ai.azure.com/models
```

That's it — those two key values are all you manage. The role→model map (which model does
which job) lives in **config/code**, not in `.env`.

## Which key unlocks which model

| Model | Key var | Endpoint var | Model/deployment id |
|-------|---------|--------------|---------------------|
| GPT-5.5 | `AZURE_OPENAI_API_KEY` | `AZURE_OPENAI_ENDPOINT` | `gpt-5.5` |
| Claude Sonnet 4.6 | `AZURE_FOUNDRY_API_KEY` | `AZURE_FOUNDRY_ANTHROPIC_ENDPOINT` | `claude-sonnet-4-6` |
| Grok 4.x | `AZURE_FOUNDRY_API_KEY` | `AZURE_FOUNDRY_MODELS_ENDPOINT` | `grok-4.3` *(exact id confirmed by benchmark discovery)* |
| DeepSeek-V4-Pro | `AZURE_FOUNDRY_API_KEY` | `AZURE_FOUNDRY_OPENAI_ENDPOINT` | `DeepSeek-V4-Pro` |
| DeepSeek-V4-Flash | `AZURE_FOUNDRY_API_KEY` | `AZURE_FOUNDRY_OPENAI_ENDPOINT` | `DeepSeek-V4-Flash` |

## After you rotate keys

When you reset the keys, just replace the two values (`AZURE_OPENAI_API_KEY` and
`AZURE_FOUNDRY_API_KEY`) in `.env`. Nothing else changes — endpoints and model ids stay the
same. Then re-run the benchmark / analysis.

## Production (later)

Move both keys into **Azure Key Vault**; the app reads them at runtime via managed identity
(`DefaultAzureCredential`) instead of `.env`. Tracked in
[09-open-questions.md](./09-open-questions.md) (#11/#13) and
[03-tech-stack.md](./03-tech-stack.md).

## Safety checklist
- [x] `.env` is gitignored (`git check-ignore .env` → `.env`).
- [ ] **Rotate the temp keys** after benchmarking (they were shared in chat).
- [ ] Never commit `.env`; never paste keys into docs/chat.
- [ ] Prod: keys in Key Vault, not `.env`.
