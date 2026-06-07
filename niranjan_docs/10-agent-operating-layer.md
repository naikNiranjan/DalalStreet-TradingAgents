# 10 — Agent Operating Layer (deferred)

> **Status: DEFERRED — direction captured now, built only after the trading spine works.**
> Inspired by [Hermes Agent](https://github.com/NousResearch/hermes-agent) (cloned for
> reference at `references/hermes-agent`, commit `a317e54`). Hermes is **reference only** —
> we build our own narrow, strict version. Hermes code never enters the trading runtime.

## The one rule that governs this entire layer

**The Agent Operating Layer sits ABOVE the trading core and can never bypass it.**
It can schedule, remember, scan, summarize, and suggest — but the path to any order is
always:

```
data → SignalDecision → risk gates → paper broker → audit log
```

The operating layer may *ask* the core to run a scan or produce a `SignalDecision`. The
**risk engine still decides allowed/blocked**, and the **paper broker** still simulates the
fill. The operating layer has **no order-placement authority** of its own — ever.

## Why deferred (sequencing rationale)

If we build the agent OS first, we get a fancy autonomous shell with no working trading
loop — the classic failure. If we build the **trading spine first**, every operating-layer
feature then has a real job:

- memory remembers **actual** paper trades, not hypotheticals
- skills describe **tested** setups, not guesses
- cron runs **real** market workflows against a working engine
- reports summarize **real** P&L
- a sidecar (if ever) controls a **real, safe** engine

So: **do not build this first, do not merge Hermes into the core, do capture the direction
now.**

## Three-phase adoption

### Phase A — Trading spine first (this is the real first build)
One stock · cash equity · paper only · hard fail-closed gates · realistic fills · audit ·
basic report. No agent OS yet. (This maps to roadmap Phases 0–5 in
[08-implementation-roadmap.md](./08-implementation-roadmap.md).)

### Phase B — Hermes-inspired operating layer (`agent_os/`, built ourselves)
Layered on once the spine is proven. Components:

| Component | What it is | Critical constraint |
|-----------|-----------|---------------------|
| **Skills / playbooks** | Strategy definitions (breakout, gap-up-fade, option-chain-OI, expiry-day, news-shock, risk-review). Each = rules + data needed + invalidation + examples. | A new/changed skill must be **backtested → paper-tested → reviewed** before it can go active. Never auto-activate a live-affecting skill. |
| **Memory tiers** | short-term (today's market + open paper trades) · episodic (every signal/order/fill/outcome) · long-term (proven lessons) · regime (vol/FII-DII/trend/sector) · strategy (which setup works in which regime). | **Evidence-based** — no "I learned this works" after one trade. Grow from the repo's *existing* reflection log; don't orphan it. |
| **Scheduled jobs (cron)** | 08:45 token/data health · 09:00 pre-market brief · 09:15 open check · 10:00 (F&O) scan · 14:45 close scan · 15:45 P&L/report · EOD memory/reflection. | **"Wake only if changed"** — cheap scripts check if price/OI/news moved enough *before* spending an LLM call. |
| **Toolsets (isolation)** | `data_toolset`, `analysis_toolset`, `paper_toolset`, `audit_toolset` enabled; **`live_toolset` not even visible to the LLM in paper mode**. | The model literally cannot hold a live-order tool until far later. |
| **Context/rule files** | `TRADING_RULES.md`, `RISK_POLICY.md`, `FNO_RULES.md`, `DATA_SOURCES.md`, `NO_TRADE_RULES.md` — non-negotiable rules injected every run. | These are guardrails the agent must follow, not suggestions. |
| **Reports / notifications** | Daily P&L, decisions, lessons. Telegram/Discord delivery optional. | Read-only; no control surface. |

**Build-vs-extend note:** the base repo is *already* a LangGraph multi-agent system with a
reflection-memory loop. Prefer **extending existing graph nodes** (add Regime / Option-Chain
analyst nodes) and **evolving the existing memory** over building a parallel agent framework
or a fresh memory subsystem beside the old one.

### Phase C — Hermes sidecar (optional, much later)
Hermes runs as a separate process that talks to the trading core through a **narrow,
safe API/MCP**:
```
Hermes: "run paper F&O scan"  →  Trading core: report + suggested SignalDecision
                              →  Risk engine: allowed / blocked
                              →  Paper broker: simulated fill / reject
```
Hermes can request scans and reports; it **cannot place live orders or bypass risk gates.**

## What we deliberately do NOT copy from Hermes

Broad chat-platform integrations, general terminal powers, and uncontrolled
self-improvement. For trading, freedom is danger — we want a **narrower, stricter** system.
New strategy → backtest → paper test → review → *maybe* active. Never strategy → live.

## How this changes the system (the payoff)

| Without operating layer | With operating layer |
|-------------------------|----------------------|
| run analysis → paper order → report | pre-market cron → scan news/data → apply tested playbooks → recall past mistakes → run paper trade → audit → post-market review → improve future playbooks |

It turns a simple bot into an **intelligent paper-trading desk** — *after* the desk's core
machinery is proven to work and stay within its risk gates.

## Decision summary

- ✅ Include Hermes ideas as a **deferred** Agent Operating Layer.
- ❌ Do **not** build it first; ❌ do **not** merge Hermes into the trading core.
- ✅ Keep Hermes cloned in `references/hermes-agent` for design guidance only.
- ✅ Operating layer can schedule/remember/suggest; ❌ it can never bypass risk gates or
  place orders directly.
