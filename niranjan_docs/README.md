# DalalStreet-TradingAgents — Planning Docs

> **Status:** PLANNING ONLY. No implementation has started. These documents are
> meant to be reviewed (by a second AI agent and by Niranjan) **before** a single
> line of code is written.

This folder contains the complete plan to adapt
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(forked as **DalalStreet-TradingAgents**) into a **fully Azure AI Foundry-native,
Indian-market (NSE/BSE) automated trading agent** — paper-trading first, then live.

## How to read these docs (in order)

| # | Doc | What it answers |
|---|-----|-----------------|
| 00 | [Project Assessment](./00-project-assessment.md) | How good/bad is the base repo? What needs to change? |
| 01 | [Goal & Vision](./01-goal-and-vision.md) | What are we building, for whom, success criteria, non-goals |
| 02 | [Target Architecture](./02-architecture.md) | The system we want to end up with |
| 03 | [Tech Stack](./03-tech-stack.md) | Every library, API, and service — with rationale |
| 04 | [Research: Azure AI Foundry](./04-research-azure-foundry.md) | Model choices, SDKs, auth, cost, observability |
| 05 | [Research: Broker APIs](./05-research-broker-apis.md) | Dhan/Angel/Zerodha/etc. comparison + pick |
| 06 | [Research: India Data Sources](./06-research-data-sources.md) | Prices, fundamentals, news, sentiment, mechanics |
| 07 | [SEBI Compliance](./07-sebi-compliance.md) | Legal rules for retail algo trading (mandatory from Apr 2026) |
| 08 | [Implementation Roadmap](./08-implementation-roadmap.md) | Phased plan, milestones, what to build when |
| 09 | [Open Questions / Decisions](./09-open-questions.md) | Things the reviewer/Niranjan must decide before coding |
| 10 | [Agent Operating Layer](./10-agent-operating-layer.md) | **Deferred** Hermes-inspired layer (skills/memory/cron/toolsets) — built after the spine |
| 11 | [Success Metrics](./11-success-metrics.md) | The paper→live gate. Defined **before** coding so results can't be rationalized |
| 12 | [Phase 3 Execution Spine](./12-phase3-execution-spine.md) | **Plan** locking the Phase 3 contracts (SignalDecision, security master, fail-closed gates, paper fills, audit, report) before code |
| 13 | [Phase 3 Build Plan](./13-phase3-build-plan.md) | **Build order** — turns doc 12 into a sequenced, tests-first task list (Tasks 0–8) with signatures, test lists, acceptance criteria |

> Phase results live alongside the plan docs: [phase0](./phase0-results.md) ·
> [phase1](./phase1-run-results.md) · [phase2](./phase2-results.md).
> Historical review snapshots live in [`archive/`](./archive/) — superseded, kept for provenance only.

## TL;DR

- **Base repo** = a research-grade **multi-agent LLM analysis engine** (LangGraph) that
  outputs a Buy/Sell/Hold *rating*. It does **not** execute trades. It already
  partially supports India via yfinance `.NS`/`.BO` tickers and Nifty/Sensex benchmarks.
- **Our job** = (1) swap all LLMs to **Azure AI Foundry** native, (2) India-ize the
  data/news/sentiment layer, (3) add a **broker-agnostic execution layer with a
  paper/live switch**, (4) add **India market rules** (hours, circuits, costs, SEBI),
  (5) add **backtesting + risk controls**, then go live.
- **Recommended live broker:** Angel One SmartAPI (free, automatable TOTP) for build +
  live; **Dhan** as the sandbox-validation env. Broker stays behind an adapter.
- **Build philosophy: SPINE FIRST.** Build a thin vertical slice (one cash-equity stock,
  paper, realistic fills, fail-closed risk gates, audit) end-to-end *before* layering on
  the Hermes-inspired agent OS (doc 10) or the F&O track. Every advanced feature is added
  with evidence, on top of a working loop — never as scaffolding underneath it.

## Review instructions (for the reviewing agent)

When reviewing, focus on: (1) Is the architecture sound and India/SEBI-compliant?
(2) Are the broker + data + Azure choices the best free/low-cost options as of 2026?
(3) Is the phasing safe — does paper-trading fully precede any real-money risk?
(4) What's missing or risky? Record findings against [09-open-questions.md](./09-open-questions.md).
