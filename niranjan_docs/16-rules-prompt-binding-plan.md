# 16 — Binding Rules into the Live Prompts (agent_os Slice 2) — Plan

> Status: **PLAN — awaiting sign-off. No code until signed off.**
> (cadence: plan → review → fix → build tests-first → review → commit)
> Builds on the committed Agent Operating Layer first slice
> ([15-agent-operating-layer-build-plan](./15-agent-operating-layer-build-plan.md),
> commit `6ce5db1`) and the execution spine ([12](./12-phase3-execution-spine.md) /
> [14](./14-phase3-integration-slice.md)). This is the **deferred Task A follow-up** that
> doc 15 explicitly held back: *"this slice ships the loader + the five files + the rendered
> binding block only … wiring it into the live graph/PM prompts is deferred to a later slice,
> after prompt-size/behavior tests."* This is that slice.

---

## Goal

Make the binding rules **actually reach the model that decides** — inject a rules
preamble into the Portfolio Manager prompt (the node that produces the
`PortfolioDecision` → `SignalDecision`), so the desk's *suggestions* are aligned with its
*rules* before they ever hit the gates. Do it **without bloating cost or degrading
analysis**, and without pretending the prompt is a safety mechanism.

## The one framing that must not blur (LOCKED)

**Rules in the prompt are ALIGNMENT, not enforcement.** The deterministic 15-gate chain in
`execution/` remains the *only* thing that can stop an order. The prompt rules exist to make
the LLM's recommendations *coherent with* those gates — so the desk proposes fewer trades
that the gates would just block, and reasons within the desk's risk posture. If the model
ignores a rule, the gate still catches it. We never weaken a gate because "the prompt says
so," and we never trust the prompt to enforce anything.

## The measured reality (why this is a plan, not a one-liner)

Measured against the committed loader:

| Thing | Size |
|------|------|
| Full rendered binding block (`load_rules().text`) | **16,146 chars ≈ 4,036 tokens** |
| TRADING_RULES / RISK_POLICY / NO_TRADE / DATA_SOURCES / FNO | 3924 / 3387 / 3110 / 3188 / 2537 chars |

Two facts make naive injection wrong:

1. **There is no prompt-caching/stable-tier layer in the stack today.** The PM prompt is a
   plain f-string (`portfolio_manager.py:42`); no `cache_control` / ephemeral caching exists
   anywhere in `tradingagents/`. So a 4k-token block is paid **in full on every call** — and
   a session runs the PM once per symbol (12-name universe) per book.
2. **Most of the full block is context the decision node doesn't need verbatim.** The
   load-bearing, decision-relevant content is the **NO_TRADE hard stops** and the **RISK_POLICY
   caps** (+ the binding header). DATA_SOURCES / FNO / the prose in TRADING_RULES are policy
   the *system* enforces, not things the PM must re-read every call.

## Where this ranks (reviewer context — do not over-scope this slice)

Review flagged that the digest is a **cost trim on the PM prompt, not the runtime fix.** A
session's ~2h wall-clock is dominated by the full per-symbol agent graph
(market / news / fundamentals / sentiment / research / risk / PM) across the 12-name universe,
not by the PM prompt size. The high-impact runtime work — ranked — is a **separate optimization
track, out of scope here:**

1. **Parallelize the 12-symbol analysis** (concurrent symbols, rate-limit-controlled) — the real reducer.
2. **Cache stable per-day data** (news / fundamentals / OHLCV / security-master / macro) — stop re-fetching/re-paying.
3. **Model routing** — reasoning-grade models only where it matters; cheap models for extraction / sentiment / classification.
4. **This slice — the rules digest** (medium impact: trims the PM prompt + improves alignment).
5. Skip-unchanged-symbols; per-symbol resume.

Items 1–3 and 5 belong to a future optimization doc and are **not built here.** This slice stays
narrow on purpose: alignment + a bounded PM-prompt trim.

## Decisions to lock (the reviewer's calls)

### D1 — Scope: which prompts get the rules? (recommend: PM only, this slice)
- ✅ **Portfolio Manager** (`agents/managers/portfolio_manager.py`) — the node that emits the
  final categorical decision + conviction. This is where alignment matters most.
- ⏸ **Risk debators** (aggressive/conservative/neutral) — relevant (they argue risk), but
  tripling the injection. **Deferred**; revisit once the PM result is measured.
- ❌ **Data analysts** (fundamentals/news/sentiment/technical) — they gather evidence; rules
  don't change what the data says. No injection.

### D2 — Form: condensed *derived* digest, not the full 4k block (recommend)
- Ship a **`render_digest()`** in `agent_os/rules/loader.py` that **derives** a compact binding
  digest **from the same five files** (so it can never drift from the source of truth):
  the binding header + the **NO_TRADE hard-stop list** + the **RISK_POLICY numeric caps**
  (confidence floor 0.60, position cap, max-open, daily-loss, sector cap, no-BTST, no-leverage).
  **Target ≤ 800 tokens — locked first budget (per review).** Relax to ≤ 1,200 **only if** the
  ≤800 digest is shown to drop a NO_TRADE hard-stop or a RISK_POLICY numeric cap — never for prose.
- The **full** files remain available for on-demand reference (a future "load the full rule
  file X" tool), **not** injected every call.
- Test asserts the digest is generated from the files (contains the actual NO_TRADE items +
  the actual cap numbers), so editing a rule file updates the digest — no hand-maintained copy.

### D3 — Placement & caching
- Inject the digest as a **labelled preamble** at the top of the PM prompt, framed binding
  ("These are binding desk constraints; if a rule conflicts with the analysis or a suggestion,
  the rule wins; you cannot recommend a trade that violates a NO_TRADE rule").
- **Caching is out of scope this slice** (no layer exists). Record the digest's token delta;
  if/when a stable-tier cache is added (Anthropic/Azure prompt caching), the preamble is the
  obvious first cache-stable block. Flagged as a follow-up, not built here.

### D4 — Behavioral acceptance (the "behavior tested" doc-15 asked for)
A/B harness on the 12-name universe (offline, mocked LLM where needed):
1. **Token budget:** the PM prompt grows by ≤ the digest budget (≤ ~800 tokens — the locked D2
   budget); assert the measured delta.
2. **No degradation:** with the digest, the PM still returns a valid structured
   `PortfolioDecision` (conviction parses; no format breakage) across the universe.
3. **Alignment works:** a deliberately rule-violating scenario (e.g. a high-confidence BUY on
   a name flagged stale / outside hours in the prompt context) is **declined or downgraded** by
   the PM *and* — regardless of the PM — still blocked by the gate (proving the gate is the
   real backstop and the prompt only nudges).
4. **Provenance:** the digest content matches the rule files (drift test from D2).

### D5 — Default state: OFF (locked per review)
- The `inject_rules_digest` config flag defaults to **`False` (off)**. The digest is **opt-in**
  until the D4 A/B run is inspected and shown to help (no degradation, bounded delta, alignment
  works); flip it on only after that evidence — consistent with *measure before trusting.* This
  resolves open question #2.

## Build order (tests-first; each task: tests → impl → green)

- **B1 — `render_digest()` in `agent_os/rules/loader.py`** + tests: derives the ≤800-token (D2)
  binding digest from the five files; drift test; injection-scan still applies; size asserted.
- **B2 — PM prompt seam** (`agents/managers/portfolio_manager.py`): additive, opt-in preamble
  via an injected `rules_digest: Optional[str] = None` (default `None` ⇒ **behaviour byte-for-byte
  unchanged**, so every existing test stays green). Wired on only when the graph passes it.
- **B3 — graph wiring** (`graph/setup.py` / `trading_graph.py`): load the digest once per run
  (cheap) and pass it to the PM node; config flag `inject_rules_digest` (default `False`/off — locked in D5).
- **B4 — A/B + token-budget tests** (D4): a small harness asserting delta, structured-output
  integrity, the alignment scenario, and the gate-still-blocks invariant.

## Non-goals (this slice — explicit ❌)
- ❌ No change to any gate, contract, or the order path. Prompt rules never enforce.
- ❌ No injection into analysts or (this slice) risk debators.
- ❌ No prompt-caching layer (flagged as a follow-up).
- ❌ No playbook→signal matching (separate, gated increment).
- ❌ No new agent_os capability (#5–#10 stay deferred until the paper loop has real results).

## Exit criteria
1. `render_digest()` derives a ≤800-token binding digest (D2) from the five files; drift test green.
2. PM prompt accepts the digest **opt-in**; default-off path leaves all existing tests unchanged.
3. A/B tests green: bounded token delta, no structured-output degradation, the rule-violating
   scenario is declined/downgraded by the PM **and** blocked by the gate.
4. Full suite green; **zero** regressions in `execution/` or `tradingagents/`.
5. This doc reviewed and signed off before any code.

## Open questions for the reviewer
1. ✅ **RESOLVED (D2 size target)** — locked to **≤800 tokens first**, relax to ≤1,200 only if the
   ≤800 digest drops a NO_TRADE hard-stop or a RISK_POLICY cap (per review). Remaining sub-question:
   should the digest include a one-line pointer to each full rule file, or just NO_TRADE + caps?
2. ✅ **RESOLVED (D5 default)** — locked to **off by default** (opt-in via `inject_rules_digest`),
   flip on only after the first A/B run is inspected (per review).
3. **D1 risk debators** — agree they're deferred to a later slice, or include a tiny NO_TRADE-only
   reminder for them now? *(still open)*
