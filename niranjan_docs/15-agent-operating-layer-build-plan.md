# 15 — Agent Operating Layer — Build Plan

> Status: **PLAN — awaiting sign-off. No code until signed off.**
> (cadence: plan → review → fix → build tests-first → review → commit)
> This is the build plan for the **first safe slice** of the deferred Agent Operating
> Layer designed in [10-agent-operating-layer](./10-agent-operating-layer.md). It layers
> **on top of** the now-built Phase 3 spine ([12](./12-phase3-execution-spine.md) /
> [13](./13-phase3-build-plan.md) / [14](./14-phase3-integration-slice.md)), serves the
> gates in [11-success-metrics](./11-success-metrics.md), and sits in the deferred
> **Track AOS** of [08-implementation-roadmap](./08-implementation-roadmap.md).
> Hermes ([github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent),
> cloned at `references/hermes-agent`, commit `a317e54`) is **design reference only** — its
> code never enters the trading runtime.

---

## Goal

Turn the working paper-trading bot into a **disciplined trading desk** — not a free-form
autonomous agent. The desk gets written rules it cannot ignore, version-controlled
playbooks that describe *tested* setups, hard tool isolation so the model literally cannot
hold a live-order tool in paper mode, and a memory that is built **from the audit log and
reports, not from vibes**.

This slice builds **four** of the ten capabilities — the foundations that are safe to build
*before* the paper loop has real results — and explicitly **defers the other six** until it
does.

```
agent_os  =  rules (binding)  +  playbooks (tested setups)  +  toolset isolation
             +  report→memory ingestion (evidence-based)
                          ↑ all four sit ABOVE the spine and can never bypass it
```

## The one invariant (LOCKED, v1, 2026-06-08)

**The Agent Operating Layer sits ABOVE the trading core and can never bypass it.** The only
path to any order is, and remains:

```
data → SignalDecision → risk gates → paper broker → audit log
```

The layer may **schedule, remember, scan, summarize, and suggest**. It has **no
order-placement authority of its own — ever.** Concretely (verified against the spine):

- It **never** constructs a `SignalDecision`, **never** calls `build_signal`,
  `SignalDecision.from_portfolio_decision`, `Router._process_one`, `GateContext(...)`, or
  `PaperBroker.place_order`. Those paths belong to the spine.
- It **may** read signals (`execution.router.load_signals`), trigger a run
  (`execution.session.run_session` / `run_analysis_phase` / `run_execution_phase`), and read
  what the spine wrote (`AuditLog.read_all` after `AuditLog.verify`, the `SessionReport`
  JSONL). Every order that results still flows through the **15-gate chain** and
  `PaperBroker`.
- In **paper mode the live-order tool does not exist for the LLM** — not hidden, not
  disabled: *absent* from the tool schema the model receives. Proven by a test.

This is the entire reason the layer is allowed to exist. If any task in this slice cannot be
built without violating it, the task is wrong, not the invariant.

## Why this is a plan, not a patch

Three reasons this needs sign-off before code:

1. **It introduces a new top-level package** (`agent_os/`) and a new safety surface (which
   tools the LLM can see). A bug here is a "the model placed/affected an order it shouldn't
   have" bug — the worst class we have. The isolation mechanism must be reviewed before it
   is trusted.
2. **It must extend, not orphan, the existing memory + reflection loop.** The base repo
   already has a single memory class (`TradingMemoryLog`) and one reflection node
   (`Reflector.reflect_on_final_decision`) with ~70 tests (68 in `tests/test_memory_log.py`)
   and a load-bearing on-disk grammar.
   The plan commits to wrapping them — getting that boundary wrong duplicates or breaks
   memory.
3. **It deliberately borrows from and inverts Hermes** in specific places. The reviewer
   should agree the inversions (no agent-authored playbooks, no LLM-written memory, no live
   tool in the schema) are the right tightening before we encode them.

---

## How it connects to the spine (the integration surface)

Verified against the committed Phase 3 code. These are the **only** seams `agent_os/`
touches.

| Need | Spine API it calls (read/trigger only) | Module |
|------|----------------------------------------|--------|
| Read today's signals | `load_signals(path) → list[SignalDecision]` | `execution/router.py` |
| Run analysis (off-window OK) | `run_analysis_phase(graph, universe, *, as_of, sm_refreshed_at, …) → AnalysisResult` | `execution/session.py` |
| Run execution (in-window) | `run_execution_phase(signals, *, security_master, quote_source, books, …, kill_switch=False) → list[BookRunResult]` | `execution/session.py` |
| Run a full session | `run_session(graph, *, universe, security_master, quote_source, as_of, sm_refreshed_at, out_dir, books=None, …, kill_switch=False) → SessionResult` | `execution/session.py` |
| Emergency stop | pass `kill_switch=True` (every order **blocked AND audited**) | `execution/session.py` → `Router` |
| Read audit (memory) | `AuditLog.verify() → bool`, then `AuditLog.read_all() → list[dict]` | `execution/audit.py` |
| Read outcomes (memory) | `SessionReport` JSONL: line 0 = `CoverageManifest` (`record="coverage"`), lines 1..n = `BookReport` dicts | `execution/report.py` |
| 5-tier vocabulary | `parse_rating`, `RATINGS_5_TIER` (Buy/Overweight/Hold/Underweight/Sell) | `tradingagents/agents/utils/rating.py` |

**These are `agent_os/` *internal orchestration* calls — not LLM-facing tools.** The Python
layer may call `run_session` / `run_analysis_phase` / `run_execution_phase` directly; the
**LLM only ever sees the narrow `paper_toolset` wrappers** (Task C), never these raw runners.

**What the layer must NOT do:** recompute trade outcomes from raw audit rows (it reads the
already-verified `BookReport` that `build_book_report` assembled from those same rows),
construct any spine object, or import/instantiate a live broker.

The canonical order path the layer sits above (unchanged by this slice):

```
graph.propagate(symbol, date) → final_state
  → build_signal(...) → SignalDecision   [bridge.py]
  → persist_signals(...) → signals JSONL   [router.py]
  → Router.run_execution_pass(signals, now)  [router.py]
      → 15 gates: kill_switch · auth_valid · market_open · data_fresh · instrument_tradable
        · confidence_floor(0.60) · stale_quote · spread_liquidity · daily_loss_limit
        · position_size_cap · max_open_positions · buying_power · no_BTST · sector_cap · audit_writable
      → PaperBroker.place_order(...)   [brokers/paper.py]   (only if ALL gates allow)
      → AuditLog.append('signal'|'gate'|'fill'|'block'|'reject'|'unfilled_exit'|'skip', …)
  → build_book_report(...) → BookReport   [report.py]
  → SessionReport.write_markdown/write_jsonl(...)
```

---

## The ten capabilities — prioritized, and what is in *this* slice

From the agreed Hermes-direction priority list. ✅ = built in this slice; ⏸ = deferred
(non-goal for now), built only after the paper loop has real results.

| # | Capability | This slice | Why |
|---|-----------|:---------:|-----|
| 1 | **Skills / Playbooks** | ✅ schema + first cash-equity templates | Encode tested setups; read-only, human-authored |
| 2 | **Rule files** | ✅ all 5, binding | The cheapest, highest-leverage guardrail |
| 3 | **Toolset isolation** | ✅ + test proving no live tool in paper | The core safety surface; must exist before anything autonomous |
| 4 | **Memory tiers** | ✅ ingestion + episodic tier (from audit+report) | Evidence substrate; the rest of memory grows on it |
| 5 | Scheduled jobs (cron) | ⏸ | Needs a proven loop to schedule |
| 6 | Wake-only-if-changed | ⏸ | Cost optimization; premature before #5 |
| 7 | Context packs | ⏸ (design note only) | Builds on memory tiers once they have data |
| 8 | Post-trade reflection (automated) | ⏸ | Extends the existing `Reflector`; needs episodes first |
| 9 | Agent inbox / task queue | ⏸ | Orchestration; only useful with #5/#8 |
| 10 | Safe sidecar | ⏸ | Last, optional, narrow MCP; never places orders |

**First safe slice = #2 rules + #1 playbook schema & templates + #3 toolset isolation +
#4 report→memory ingestion.** That is exactly the user's recommendation.

---

## Proposed package layout (created only after sign-off)

```
agent_os/
  __init__.py
  rules/
    TRADING_RULES.md        # how the desk trades (cash equity, India hours, deadband, sizing intent)
    RISK_POLICY.md          # caps, confidence floor 0.60, daily-loss limit, dual-book discipline
    NO_TRADE_RULES.md       # hard "never trade when…" (stale data, kill-switch, off-window, untradable)
    DATA_SOURCES.md         # which source is authoritative for what; freshness clocks; fail-closed
    FNO_RULES.md            # F&O constraints — PAPER-ONLY, no naked selling (capability deferred)
    loader.py               # discover + concat ALL five, char-capped, injection-scanned, fixed order
  playbooks/
    schema.py               # the validated 7-section Playbook contract + strict loader
    catalog.py              # progressive-disclosure index: (name, description) ONLY
    cash-equity/
      breakout.md
      gap-up-down.md
      trend-following.md
      news-shock.md
      earnings-reaction.md
      sector-rotation.md
  toolsets.yaml             # data_/analysis_/paper_/audit_ toolsets; live_toolset registered ONLY in live+armed mode
  toolsets.py               # registry + active_tools(mode) with fail-closed check_fn + paper-mode assertion
  memory/
    ingest.py               # deterministic: verify→read audit + report → episodic tier; cites source
    tiers.py                # tier stores (short/episodic/long/regime/strategy) — bounded, char-capped, file-backed
tests/agent_os/
  test_rules_loader.py
  test_playbook_schema.py
  test_toolset_isolation.py # PROVES no live-order tool exists in paper mode
  test_memory_ingest.py
```

`agent_os/` is **above** `execution/` and `tradingagents/` in the dependency graph: it may
import from them; **they must never import from `agent_os/`** (keeps the spine standalone and
the layer strictly optional). A test asserts this direction.

---

## Build order (tests-first; each task: tests → impl → green)

### Task A — Rule files + binding loader  (capability #2)

The five rule files are human-authored markdown. The loader is the only code.

- **Behavior:** discover the five files from `agent_os/rules/`, concatenate them in a **fixed
  order**, cap each at a char limit (Hermes uses 20,000), scan for prompt-injection markers,
  and **render** a single binding rule block with **non-negotiable framing** ("These are
  binding constraints; if a rule conflicts with a request or a model suggestion, the rule
  wins; you cannot place or simulate an order that violates them."), ready to be placed in the
  **stable** (cache-stable) prompt tier.
- **Scope this slice (LOCKED, v1, 2026-06-08):** Task A ships the loader + the five files +
  the rendered binding block **only**. It does **not** wire the block into the live analysis
  graph / PM prompts this slice — that injection is **deferred** to a later slice, after tests
  prove prompt size and behavior (resolves open question #3 below).
- **Inversion of Hermes:** Hermes loads *one* context file (first-match-wins) and frames it
  as advisory ("should be followed"). We load **all five, always**, framed as **binding**.
- **Tests:** all five load; missing file → loader **fails closed** (raises, does not silently
  skip); fixed concat order is stable; over-cap file is truncated + flagged; injection marker
  in a rule file is detected; the binding header is present.

### Task B — Playbook schema + first cash-equity templates  (capability #1)

A playbook is a directory-per-setup markdown file with **agentskills.io-compatible YAML
frontmatter** (`name`, `description`) + a body of the user's **exact 7 required sections**:

```
when_to_use · data_required · entry_rules · invalidation · risk_limits · examples · tests
```

- **Schema-enforced (stricter than Hermes):** Hermes validates *only* frontmatter and treats
  body sections as convention. We **validate all 7 sections** + a machine-readable
  `risk_limits` block (max position %, stop/invalidation rule, max signals/day). **A playbook
  missing `risk_limits` fails to load — it does not load anyway.**
- **Progressive disclosure (reuse from Hermes):** `catalog.py` exposes **only**
  `(name, description)` pairs; the full body is loaded on demand. Keeps per-run context small.
- **Read-only, human-authored (inversion of Hermes):** **no** agent create/edit/patch of
  playbooks at runtime. Hermes's `skill_manage` + `SKILLS_GUIDANCE` self-authoring is exactly
  the free-form self-improvement we forbid. Playbooks are version-controlled files.
- **A playbook describes `SignalDecision` inputs/thresholds only** — it can carry **no order
  authority**. New/changed playbook → backtest → paper-test → review → *maybe* active. Never
  playbook → live.
- **Templates this slice:** `breakout`, `gap-up-down`, `trend-following`, `news-shock`,
  `earnings-reaction`, `sector-rotation` (cash equity). F&O OI playbooks are **deferred**.
- **Tests:** valid playbook loads; each of the 7 missing sections → reject; missing/empty
  `risk_limits` → reject; oversize body → reject; catalog exposes name+description only (never
  the body); every shipped template validates; `risk_limits` parses to machine-readable caps.

### Task C — Toolset isolation + the proof  (capability #3)

The core safety surface. Reuse Hermes's `registry.get_definitions(tool_names)` model
**in spirit, verbatim**: the LLM's tool schema list is built from an **explicit per-run
allowlist**, and each tool is gated by a **fail-closed `check_fn`** (any exception →
unavailable).

- **Toolsets (`toolsets.yaml`):**
  - `data_toolset` — read prices/news/fundamentals/OHLCV, security-master lookup (read-only)
  - `analysis_toolset` — run the graph / analysts (produces signals via the spine, no orders)
  - `paper_toolset` — **narrow wrappers only** (never the raw `run_session` /
    `run_analysis_phase` / `run_execution_phase`): each wrapper hard-codes `mode=paper`,
    derives `out_dir`/`signals_path` from the session date (not caller-supplied), uses the
    current session date, and accepts **no** arbitrary path / object / graph injection. The
    model can *request* a paper run; it cannot point one at arbitrary files or smuggle in
    objects. (Reviewer round 1.)
  - `audit_toolset` — `AuditLog.read_all` / `AuditLog.verify` (read-only)
  - `live_toolset` — **not registered at all in paper mode.** The live/order tool is registered
    **only** when `mode == "live"` **AND** an explicit human-arming flag is set. In paper mode
    it is never registered — so it is neither in the schema the LLM sees nor dispatchable by
    name (a forced dispatch hits "unknown tool"). This is **stricter** than Hermes, which keeps
    every tool registered and merely filters the active set.
- **Stricter than Hermes (three hard rules):**
  1. **Conditional registration:** the live/order tool is registered only under
     `mode == "live"` + human-arming; in paper mode it does not exist in the registry at all.
  2. **Defense in depth:** even when registered, its `check_fn` returns `False` unless
     `mode == "live"` + armed, **and** its handler refuses unless the same holds — and even
     then the order path still goes data → SignalDecision → gates → broker → audit. The LLM
     **never** gets a direct order tool; only data/analysis/paper-simulate/audit-read tools.
  3. **Request-assembly assertion:** if any live-tool name leaks into the active tool list
     while `mode == "paper"`, **fail the run** (don't trust the caller passed the right set).
- **Deliberate divergence from Hermes (stated so the test is honest):** Hermes's
  `registry.dispatch` of an unknown tool *returns* an error string (it does not raise) and
  **never consults `check_fn`** (only `get_definitions` gates on it). So we do **not** lean on
  Hermes's dispatch semantics for safety: in paper mode the live tool is **unregistered**
  (→ genuine "unknown tool"), and our wrapper additionally **refuses** (raises / structured
  refusal) any live dispatch outside live+armed mode rather than returning a soft error string.
- **Skip from Hermes:** `toolset_distributions.py` (probabilistic tool sampling) — the
  opposite of the determinism we want.
- **The proof test (the headline of this slice)** — in paper mode:
  1. `active_tools("paper")` schema list contains **no** live-order tool (assert by name);
  2. `get_definitions(...)` over the paper allowlist never includes the live schema (its
     `check_fn` is `False` in paper even if the name were present);
  3. the **leak assertion** fires if a live-tool name is injected into the paper active set;
  4. a **direct dispatch** of the live tool in paper mode is **refused** — "unknown tool"
     (it is unregistered) **and** (defense in depth) our wrapper raises / structured-refuses
     rather than running a handler — explicitly **not** relying on Hermes's error-string return;
  5. each `check_fn` fails closed on exception; `audit_toolset` is read-only.

### Task D — Report → memory ingestion + episodic tier  (capability #4)

Memory comes from the **audit log and reports**, not from the model. Reuse Hermes's
bounded, char-limited, file-backed, **frozen-snapshot-into-prompt** model and its
`on_session_end` ingestion-hook shape — but invert who writes.

- **Deterministic ingestion (`ingest.py`), inversion of Hermes:** Hermes lets the LLM write
  memory freely via a memory tool. **We forbid that — the LLM gets no memory-write tool.**
  Memory entries are produced **only** by a deterministic post-session job that:
  1. `AuditLog.verify()` → if `False`, **stop** (broken chain → records not trusted).
  2. `AuditLog.read_all()` → raw stage records. **Fail closed on identity mismatch:** audit
     files can be appended across runs, so if the file mixes `run_id`s, or its records don't
     match the expected `(session_date, book)`, **reject** — an episode must come from a
     single matching run (test asserts this).
  3. Parse `runs/session-{date}.jsonl`: line 0 → `CoverageManifest.from_manifest_dict` **when
     it is a coverage record**; if the first line is a `BookReport` (legacy / no-coverage
     report), parse the book lines safely and record coverage as **absent** — never mis-read a
     book line as coverage. Lines 1..n → `BookReport` dicts (the costed, dual-book outcome —
     `audit_coverage_pct`, `stale_data_trades`, `daily_loss_breached`, `kill_switch_drills`,
     `filled`, `blocked`, `net_pnl`, `cost_drag_pct`).
  4. Write **one episode per book per session**, keyed by `(session_date, book_name)`, **plus
     a capped `symbol_outcomes` summary** derived from the audit rows (per-symbol
     filled/blocked + block reason, bounded in size) so the episode stays compact yet useful
     for learning.
  - **Every entry cites its source** (`run_id` / audit row / report path). No source → no
    entry. This is the single biggest divergence from Hermes and the point of the design.
- **Extend, do not orphan (verified against the base repo):**
  - Keep `TradingMemoryLog` (`tradingagents/agents/utils/memory.py`) as the **long-term store**
    and system of record; the `<!-- ENTRY_END -->` separator, `[date|ticker|rating|…]` tag,
    and `DECISION:`/`REFLECTION:` grammar are load-bearing (~70 tests; 68 in
    `tests/test_memory_log.py`) — tiers add **sidecar files**, never mutate this grammar.
  - Keep `Reflector.reflect_on_final_decision` (`tradingagents/graph/reflection.py`); later
    feed it audit/report outcomes (capability #8) instead of yfinance-only returns.
  - Keep the read/write seams: `store_decision` after `_log_state`, and
    `get_past_context → AgentState.past_context → create_initial_state → Portfolio Manager`.
  - Reuse `parse_rating` / `RATINGS_5_TIER` for any tier label — do not invent a parallel scale.
  - **Do not** resurrect `FinancialSituationMemory` / Chroma / BM25 / `reflect_and_remember`
    (`TestLegacyRemoval` asserts they stay gone).
- **The 5 tiers (only the episodic tier is *populated* this slice):**
  - **short-term** — ephemeral per-run context (open positions + today's freshness); design only.
  - **episodic** — ✅ **built**: one record per book per session from audit + report, plus a
    capped per-symbol `symbol_outcomes` summary.
  - **long-term** — ⏸ the existing `trading_memory.md` REFLECTION corpus; the *rule* is to
    promote a lesson to "proven" only after multiple `SessionReport` outcomes corroborate it
    (evidence-gated) — **design note; no promotion logic is built this slice** (it cannot run
    until enough episodes exist).
  - **regime** / **strategy** — ⏸ derived tiers (regime label per episode; playbook×regime
    win-rate), built once enough episodes exist.
- **Tests:** ingestion writes an episode from a fixture audit + report; broken chain
  (`verify()==False`) → no write; **mixed `run_id`s or a `(session_date, book)` mismatch →
  reject (fail closed)**; **a no-coverage / legacy report (first line is a `BookReport`) parses
  safely with coverage recorded as absent**; **`symbol_outcomes` is bounded/capped**; every
  episode cites its source; tier files are char-capped; no LLM-write path exists (no
  memory-write tool registered); `RATINGS_5_TIER` reused; the base-repo grammar is untouched
  (existing memory tests still green).

---

## Hermes reuse map (so the reviewer sees exactly what we borrow vs. invert)

| Capability | Reuse from Hermes | Invert / tighten | Ignore |
|-----------|-------------------|------------------|--------|
| Playbooks | directory-per-skill, frontmatter+body, **progressive disclosure** (catalog = name+desc) | sections **schema-enforced**; **no agent-authored** playbooks; describes signals, not orders | — |
| Toolsets | `registry.get_definitions(tool_names)` + fail-closed `check_fn`; webhook-safe-allowlist pattern | live tool **unregistered in paper** (stricter than Hermes's filter-active-set); **leak assertion**; live `check_fn`+handler = mode+human-arming; **don't trust Hermes's non-raising `dispatch`** | `toolset_distributions.py` |
| Rules | 3-tier (stable/context/volatile) cache-ordered prompt; char-capped, injection-scanned loader; date-only timestamp | **all five rule files always**, fixed order, in the **stable** tier, **binding** (not advisory, not first-match-wins) | runtime-mutable SOUL/persona |
| Memory | bounded char-limited file-backed **frozen-snapshot**; `on_session_end` ingestion hook shape | **deterministic ingestion from audit+report**; **no LLM memory-write tool**; every entry **cites its source**; 5 tiers | mem0/hindsight/knowledge-graph providers |

---

## Non-goals (this slice — explicit ❌)

- ❌ **No live-order tool registered, emitted, or dispatchable in paper mode.** In a future
  live mode it may be registered **only** under explicit human arming, and even then it cannot
  bypass the spine (data → SignalDecision → gates → broker → audit).
- ❌ **No agent-authored or runtime-patched playbooks** — human-authored, version-controlled,
  read-only.
- ❌ **No LLM-written memory** — memory is ingested deterministically from audit + reports.
- ❌ **No scheduled jobs / cron** (#5), **no wake-if-changed** (#6), **no automated context
  packs** (#7, design note only), **no automated post-trade reflection** (#8), **no agent
  inbox / task queue** (#9), **no Hermes sidecar** (#10), **no subagent fleet.**
- ❌ **No F&O activation** — `FNO_RULES.md` is written (paper-only, no naked selling) but F&O
  playbooks stay deferred per [11-success-metrics](./11-success-metrics.md)'s F&O gate.
- ❌ **No new order path, no spine bypass, no `agent_os/` import from the spine into the spine.**
- ❌ **No vector DB / Chroma / BM25 resurrection.**

## Exit criteria (what "this slice is done" means)

1. **Rules:** all five files exist; loader concatenates them in fixed order with binding
   framing; missing file fails closed. ✅ tests green.
2. **Playbooks:** schema validates all 7 sections + machine-readable `risk_limits`; the six
   cash-equity templates validate; catalog exposes name+description only. ✅ tests green.
3. **Toolset isolation:** **the proof test passes** — in paper mode the live-order tool is
   **unregistered**, so it is absent from the LLM's tool schema and a direct dispatch is
   refused ("unknown tool" + wrapper refusal, not a soft error string); the leak assertion
   fires; `check_fn`s fail closed. ✅ tests green.
4. **Memory:** ingestion writes a verified episode from audit + report, every entry cites its
   source; broken-chain, mixed-`run_id`, or `(date, book)` mismatch → no write; a no-coverage
   report parses safely; the per-symbol summary is capped; no LLM-write path exists; base-repo
   memory grammar and its ~70 tests are untouched. ✅ tests green.
5. **Direction:** `agent_os/` imports from the spine; the spine does **not** import from
   `agent_os/`. ✅ test asserts it.
6. Full suite green (current baseline 630 passed / 1 skipped); **zero** regressions in
   `execution/` or `tradingagents/`.
7. This doc is **reviewed and signed off** before any of the above is built.

## Reviewer round 1 — resolutions (folded in, v1, 2026-06-08)

1. **`paper_toolset` exposes narrow wrappers only** — not the raw `run_session` /
   `run_analysis_phase` / `run_execution_phase`. Each wrapper hard-codes `mode=paper`, derives
   paths from the session date, uses the current date, and rejects arbitrary path/object/graph
   injection — so the model can request a paper run but cannot point one at arbitrary files
   (Task C).
2. **Rule-loader scope LOCKED** — this slice ships the loader + the five files + the rendered
   binding block **only**; wiring it into the live graph/PM prompts is **deferred** to a later
   slice, after prompt-size/behavior tests (Task A; resolves old open question #3).
3. **Memory ingestion fails closed on identity mismatch** — a reused/appended audit file with
   mixed `run_id`s, or records not matching `(session_date, book)`, is **rejected**; an episode
   must come from a single matching run (Task D + test).
4. **Episode granularity LOCKED** — one episode per `(date, book)` **plus** a capped
   `symbol_outcomes` summary from the audit rows (Task D; resolves old open question #2).
5. **Report JSONL parser is coverage-tolerant** — a legacy / no-coverage report (first line is
   a `BookReport`) parses safely with coverage recorded as **absent**; a book line is never
   mis-read as coverage (Task D + test).

## Self-review pass — resolutions (pre-handoff, v1, 2026-06-08)

Found by an internal adversarial review (spine-accuracy / design-consistency / safety-invariant
lenses), verified against the code before handing to the external reviewer:

6. **Toolset-isolation proof test corrected (was the major one).** The headline test originally
   said a live-tool dispatch "must raise unknown tool." Verified against
   `references/hermes-agent/tools/registry.py`: Hermes's `dispatch` *returns* an error string
   (never raises) and **never consults `check_fn`** (only `get_definitions` does) — so for a
   *registered* live tool that assertion is both false against the reused model and the wrong
   property. Fix: in paper mode the live tool is **unregistered** (→ genuine "unknown tool"),
   with handler-level **refusal** as defense in depth; the proof test now asserts schema
   absence + `get_definitions` exclusion + leak assertion + refused direct dispatch, and states
   the deliberate divergence from Hermes (Task C + Exit criterion #3).
7. **Spine signatures made order-faithful** — the `run_execution_phase` and `run_session` rows
   in the integration table now show the real keyword-only param order
   (`security_master`, `quote_source` before `books`; `books=None` marked optional), matching
   `execution/session.py`.
8. **Test-count corrected** — the base-repo memory grammar is guarded by **~70 tests
   (68 in `tests/test_memory_log.py`)**, not "~50"; fixed in all three places.
9. **Long-term tier tagged deferred** — the `trading_memory.md` promotion rule is now marked
   ⏸ / **design note** (no promotion logic this slice), parallel to short-term/regime/strategy,
   so only the episodic tier reads as in-scope.

## Open questions for the reviewer

1. **Char caps:** adopt Hermes's 20,000-char cap per rule file and ~2,200-char per memory tier
   entry (and a cap for the `symbol_outcomes` summary), or set our own?
2. **Playbook→signal binding:** playbooks are descriptive this slice (no code consumes them at
   runtime). Confirm that's the intended scope — matching a playbook to a `SignalDecision` is a
   later capability, gated by backtest→paper→review.
