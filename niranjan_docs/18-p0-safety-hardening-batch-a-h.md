# 18 — P0 Safety Hardening: Batch A (non-finite) + Batch H (audit trust-anchor) — Plan

> Status: **PLAN v2 (round-2 review fixes folded) — awaiting sign-off. No code until signed off.**
> (cadence: plan → review → fix → build tests-first → review → commit)
> Scope: the **two P0 blockers** from the audit catalog
> ([17-codebase-audit-findings](./17-codebase-audit-findings.md)) — and **only** those two.
> Built as **one safety-hardening slice** (shared gate: *the next live-data paper run*).
> Grounded in the **committed `f60d42e`** code, read directly (the F3 lesson).
>
> **v2** folds in review round 1 (Codex + an internal Opus design-review); **round 2** (Codex:
> ledger identity, anchor-creation invariant, README) is folded too. Material changes are tracked
> in §10 so the reviewer can verify each finding was addressed.

---

## 1. Why these two, why now

The paper→live go/no-go ([11-success-metrics](./11-success-metrics.md)) rests on two promises:

1. **The gates never fail open and the sizer never crashes on real data.** The spine's ingress
   is a live Angel `getMarketData(FULL)` quote, which can deliver a **non-finite** number —
   **NaN *or* ±Inf** (a malformed payload: `float('Infinity')`, `float('1e400')`, a missing field
   coerced to NaN). IEEE-754 makes `x < limit` / `x > cap` / `x <= 0` **False** on NaN, so entry
   gates **fail open**; `round(target_notional/ref_price)` **raises** (`ValueError` on NaN,
   **`OverflowError` on Inf**), aborting the pass; and an **Inf** price defeats the existing
   positive-value filters (`inf > 0` is True) — so Inf has a *distinct* blast radius from NaN and
   must be handled explicitly, not assumed to fall out of the NaN fix.
2. **The audit chain is the trust anchor.** The metric is "100% audit coverage AND the chain
   verifies." Today `verify()` returns **True** for a truncated/zeroed file, **raises** on a
   corrupt line (crashing the report), ignores **extra** injected keys, and has **no external
   root-of-trust**. An audit guarantee that can be silently defeated or that crashes the report
   is not a guarantee.

These are the only two batches that decide whether **paper results can be trusted**. B–G follow
as sequenced slices — out of scope here (§7).

## 2. Scope lock

**IN — Batch A (non-finite = NaN ∪ ±Inf):** RG-01, RG-02, RG-03, RG-07, RG-08 (tests), **T7-02**
(folded in — it edits the *same* `g_daily_loss_limit` line as RG-03, in the opposite direction;
§5 D4), + the NaN/Inf **ingress** (quote `ltp/bid/ask`, `_ref_price`, **router MTM filter**,
equity / `day_pnl`).

**IN — Batch H (audit trust-anchor, doc 17 §7.8):** AUD-001, AUD-002, AUD-003, AUD-004,
**AUD-005** (folded in — it's part of `verify()` purity; was contradictorily "optional" in v1),
PF-001, PF-002, PF-003, **PF-004** (NaN/stale marks), RPT-001, **RPT-002** (`allow_nan`).

**Offered, Codex's call (§9 Q3):** RPT-003 (coverage denominator) — independent medium, cheap.

**OUT (explicit):** Batch B (RG-04/T7-01 future-timestamps, IDA-02/03/08/09), Batch C (F1 stale-
cache, **RG-06/SS-02 the general priceable small-position REDUCE deadband** — note §5 D3 only
fixes the *non-finite-price* REDUCE case here, **RG-05** crossed-book, IDA-01/11 calendar,
IDA-05), Batch D (**SS-01/SS-03/SS-04** — incl. the session-level bridge/freshness try/except;
§5 D8 only adds the *router fill-path* per-symbol catch), Batch E, Batch F (F2), Batch G, §8
leftovers (PF-005, RPT-004/005). A true external/WORM root-of-trust store (S3 Object-Lock / KMS)
is **live-trading** hardening, deferred (§5 D5/D6).

## 3. What the committed code actually does (grounding — corrected in v2)

Read from `f60d42e`; re-verified with `/tmp` probes (incl. the v1 Inf error):

- **Gate classes & exit-asymmetry are explicit** (`guards.py:8-18`). The NaN/Inf-affected gates
  are **all entry-side** and each tests its exit-exemption (`action not in ENTRY_ACTIONS` /
  `order.is_exit`) **before** any non-finite numeric read: `g_confidence_floor` (`:171-180`),
  `g_daily_loss_limit` / `g_position_size_cap` (exit-exempt `_ok`, `:217-234`), `g_buying_power`
  (exit-exempt, `:246-258`). **⇒ adding `not is_finite → block` *after* the existing exit guard
  blocks only ENTRIES and never vetoes an exit** — the hardening is invariant-safe by placement.
- **`_ref_price` reachable non-finite** (`router.py:152-157`, `quote.mid or quote.ltp or 0.0`):
  `Quote.mid` is **None** when bid/ask aren't both `> 0` (`contracts.py:222-226`), so for *NaN*
  bid/ask `mid` is None; **but for +Inf bid/ask `mid = (inf+inf)/2 = +Inf`** — so `isfinite(mid)`
  in D2 is **load-bearing, not redundant**. `quote.ltp` can be NaN/Inf, and `None or nan or 0.0
  == nan`, `None or inf or 0.0 == inf`. A **negative** ltp is also truthy.
- **CORRECTION (v1 was wrong): the router MTM filter does NOT screen Inf.** `router.py:139`/`:149`
  mark with `{sym: q.ltp ... if q.ltp > 0}`. `nan > 0` is False (NaN screened) **but `inf > 0` is
  True** — so an **Inf `ltp` passes the filter, marks the held position `ltp=+Inf`, and makes
  `portfolio.equity() = +Inf` directly via the committed router step.** ⇒ Inf-equity is reachable
  on the live path *today* (v1 wrongly claimed equity non-finiteness was only reachable via the
  public API / PF-004). NaN-equity remains reachable via the public `GateContext`/`size()` surface
  + PF-004. **Both** must be guarded; the router MTM filter must become finite-and-positive.
- **Sizing runs BEFORE the gates** (`router.py:186` then `:208`), so `size()` (`sizing.py:79`
  `round(target_notional/ref_price)`) crashes **before** any gate — `round(nan)`→`ValueError`,
  `round(inf)`→`OverflowError`. The entry `ref_price <= 0` guard (`sizing.py:76`) misses both
  (`nan/inf <= 0` is False). **EXIT** (target 0, qty from holdings, bypasses deadband) is
  crash-free; **REDUCE** uses `ref_price` for notional + deadband (`sizing.py:72-74,88,91-95`), so
  with `ref_price=0` a de-risk REDUCE becomes a silent **deadband no-trade** (not crash, not
  intended_exit_unfilled) — must be handled (D3).
- **Router has a clean entry-block path for "no usable price"** (`router.py:195-203`): a sizing
  `no_price` on an ENTRY runs the gate chain on a nominal order so an entry-quality gate vetoes +
  audits, then a defensive `quote_fetch_failed`/`no_book` block. **⇒ routing non-finite ref_price
  into `no_price` reuses this proven path.**
- **PaperBroker fill path** (`paper.py:129-168`): `_simulate` fills an EXIT against `quote.bid`
  guarded by `has_book` (`contracts.py:243-245`: `bid>0 and ask>0 and qtys>0`). NaN bid/ask ⇒
  `has_book` False ⇒ `intended_exit_unfilled` (correct). **+Inf bid/ask ⇒ `has_book` True ⇒ a
  bogus Inf fill price** for the exit → flows into `apply_fill`/`realized_pnl`. ⇒ ingress
  validation must sit at **`parse_full_response`** so no Inf quote ever reaches the broker.
- **Sizing crash precedes gates** — so the fix must be in `size()` itself (gate alone insufficient).
- **Audit mechanics** (`audit.py`): `read_all` bare `json.loads(line)` (`:96`) propagates
  `JSONDecodeError` out of `verify()` (`:120-132`) and `_tail_hash` (`:83-86`, `records[-1]["hash"]`
  → also `KeyError`); empty/truncated file ⇒ chains valid from `GENESIS_HASH` ⇒ `verify()` True;
  only `_BODY_KEYS` (`:28`) are hashed ⇒ extra keys ignored; no length/root anchor.
- **Portfolio** (`portfolio.py`): `_PositionState.ltp` default `0.0` (`:50`) ⇒ unmarked
  `unrealized_pnl = -cost_basis`, `holdings_value = 0` (PF-001). **`holdings_value`/`unrealized_pnl`
  sum over ALL positions including ones NOT signalled this session** (`:204-208`) — so a
  held-but-unsignalled symbol is a **routine** unmarked state, not an error. `mark_to_market`
  `float(px)` (`:148`) passes NaN/Inf, skips absent symbols (PF-004). `apply_fill` (`:100-125`)
  has no negative-`settled_cash` guard (PF-002), no `fill.side == charges.side` check (PF-003).
- **Report** (`report.py`): `to_json_line = json.dumps(asdict(...))` (`:107`, `allow_nan=True`,
  RPT-002); **no `from_dict`** (RPT-001) — and a hand-rolled duplicate deserializer already exists
  at `agent_os/memory/ingest.py:_dict_to_book_report` (the divergence RPT-001 warns about);
  `write_jsonl` (`:158-165`) loops `to_json_line()` with **no try/except** (one non-finite field ⇒
  the whole session JSONL write raises, dropping *both* books); `build_book_report` calls
  `audit.verify()` at `:244` (AUD-002 crash site); `_audit_coverage` excludes `outside_hours` from
  the denominator (`:193`) while `no_trade` folds it in (RPT-003).

## 4. The framing that must not blur (LOCKED)

- **Non-finite (NaN *and* ±Inf) guards are ENTRY-side and fail CLOSED.** A non-finite input
  blocks/no-trades an **entry** and is **audited**; it must **never** veto an EXIT/REDUCE. An exit
  that genuinely can't be priced is the broker's `intended_exit_unfilled` (trapped position stays
  visible) — never a gate veto. Preserved gate-by-gate (place the finite check *after* each
  gate's existing exit-exemption).
- **`verify()` is a pure read that returns `bool` and NEVER raises.** Corruption / truncation /
  zero-byte / extra-keys / wholesale-replacement ⇒ `False`. But **a damaged tail at open is a
  trade-time fail-closed**: `AuditLog.__init__` must not crash *and* must set `audit_writable`
  False (gate#10 blocks trading on a log that can't be safely resumed) — separating "report can
  still render the ❌ badge" from "we must not trade."
- **The report artifact is ALWAYS written and ALWAYS valid JSON, and never silently-zero.**
  Non-finite money is coerced to an explicit `null` + a loud `marks_incomplete` flag; it is never
  emitted as `NaN` (invalid JSON) and never crashes `write_jsonl`. A session with incomplete marks
  is surfaced as **not go-live-eligible**.
- **Per-symbol fail-closed, not book-wide poison.** One unpriceable holding must not freeze every
  other entry or crash the report; equity stays **finite** (conservative fallback) and the
  specific unpriceable symbol's own entry is the thing that's blocked.

## 5. Decisions to lock (the reviewer's calls)

### D1 — one helper, three layers
`execution/risk/_numeric.py: is_finite_number(x) -> bool` (False for NaN, +Inf, -Inf, None,
non-numeric). Used at **(1) ingress** (quote parse), **(2) sizer**, **(3) gate** (last line of
defence on the public `GateContext`). Tests cover NaN, +Inf, −Inf, None, int, float, str.

### D2 — finite ingress for price (recommend)
- `_ref_price` (`router.py`): `mid = quote.mid; if mid is not None and is_finite_number(mid):
  return mid; if is_finite_number(quote.ltp) and quote.ltp > 0: return quote.ltp; return 0.0` ⇒
  NaN/Inf/negative ltp yields `0.0` → existing `no_price` entry-block path.
- **Router MTM filter** (`router.py:139` and `:149`): `{sym: q.ltp ... if is_finite_number(q.ltp)
  and q.ltp > 0}` ⇒ an Inf/NaN/negative ltp no longer marks the portfolio (closes the
  committed-path Inf-equity ingress). The now-unmarked held symbol is handled by D7 (finite
  fallback + flag), not by NaN-poison.

### D3 — sizer + gate finite guards; REDUCE de-risk preserved; T7-02 reconciled
- `size()` entry path: `if not is_finite_number(equity) or equity <= 0 or not
  is_finite_number(ref_price) or ref_price <= 0 or not is_finite_number(signal.confidence):
  return _no_trade(..., "no_price")` (reuse `no_price` → router entry-block; **Q1**). No
  `round(nan/inf)`.
- **REDUCE:** when `ref_price` is non-finite or `<= 0`, **bypass the deadband** and return the
  holdings-derived qty (`floor_to_lot(current_qty * reduce_fraction)`), so a de-risk **proceeds**
  → broker → `intended_exit_unfilled` if unfillable. (The *general priceable* small-position
  REDUCE-deadband is RG-06/SS-02, **Batch C, OUT** — this only covers the non-finite-price case
  the D2 change creates.)
- Gates: `g_confidence_floor` / `g_position_size_cap` / `g_buying_power` block the **entry** when
  their numeric input is non-finite (placed *after* the exit-exemption). `g_buying_power` checks
  the **final selected price** (after the `quote.ask if quote.ask>0 else ref_price` selection,
  `guards.py:249`) so a NaN/Inf `quote.ask` is caught — `if not is_finite_number(price) or price
  <= 0: block`.
- **`g_daily_loss_limit` — T7-02 reconciliation (the v1 collision):** `if not
  is_finite_number(equity): block` (NaN/Inf, defense-in-depth) **then** `if equity <= 0:
  ok("daily-loss limit undefined for non-positive equity")` (T7-02: a fresh untraded book must not
  block all entries). Order matters: non-finite → block; `<= 0` finite → ok.

### D4 — audit anchor lifecycle (AUD-001) — fully specified (v2; sharpened in round 2)
- **Created at `AuditLog.__init__` ONLY for a fresh/empty audit** as `<audit>.anchor.json =
  {run_id, count:0, tail_hash:GENESIS}`. **Creation invariant (round-2 lock):** if the audit file
  is **non-empty and the anchor is absent**, `__init__` does **NOT** recreate it — that is the
  "anchor wiped / log moved in without its anchor" case ⇒ `verify()` returns **False** and
  `is_writable()` returns **False** (fail-closed at trade time; we do not silently re-anchor a
  non-empty log, which would paper over a wipe). A genuinely empty session is still *anchored*
  (`count:0`), so "absent anchor" is only ever the tamper/missing case, never a benign empty one.
  *(Test H2 covers exactly: non-empty audit + deleted anchor ⇒ verify False, is_writable False.)*
- **Updated atomically after each `append`** (write tmp + `os.replace`), append-line-first then
  anchor.
- **`verify()` (non-strict, the per-pass/report badge)** compares on-disk `(count, tail_hash)` to
  the anchor: a clean match ⇒ True; **truncation / zero-byte / record-drop / corruption** change
  count or tail ⇒ False. **Crash-consistency (no false-positive tamper):** if on-disk `count ==
  anchor.count + 1` AND the first `anchor.count` records' tail `== anchor.tail` AND record N+1
  chains validly, treat as a **clean mid-append crash** (True + warn), not tamper. Empty log +
  `count==0` anchor ⇒ True (vacuous-but-anchored). Non-empty log + **absent** anchor ⇒ False
  (wiped anchor). 
- **gate#10:** `is_writable()` also checks the anchor path (and roots-ledger dir, D6) are
  writable, so `audit_writable` reflects every file `append`/seal must touch.

### D5 — `verify()` / `read_all` / `_tail_hash` contract (AUD-002/004/005)
- `_read_records_safe() -> (records, ok)`: a `JSONDecodeError` / short line ⇒ `ok=False`.
  `verify()` returns `False` on `ok=False`, on any record whose key-set ≠ `_BODY_KEYS ∪ {"hash"}`
  (**rejects missing AND extra keys — AUD-005 folded in**), and on any chain break — **never
  raises**.
- `_tail_hash` uses the safe reader + `records[-1].get("hash", GENESIS_HASH)` so `__init__` never
  crashes (AUD-004). **But a non-resumable tail (corrupt / missing-hash) sets an internal
  `_resumable=False` ⇒ `is_writable()` returns False ⇒ gate#10 blocks trading** (fail-closed at
  trade time; we don't append onto a damaged chain). `build_book_report` is unchanged in shape —
  `verify()` now returns `False` (renders `Chain intact: ❌`) instead of crashing.

### D6 — external root-of-trust + verify cadence (AUD-003) — lifecycle locked (v2; identity fixed in round 2)
- **Daily-roots ledger** `runs/audit-roots.jsonl`, **sealed at session close** by an explicit
  `AuditLog.seal_root(ledger_path)` the session layer calls **after** the exec pass and **before**
  `build_book_report`.
- **Ledger identity = the audit log's canonical path** (round-2 fix), NOT `run_id`. `run_id =
  f"{date}-{spec.name}"` (`session.py:210`) is **identical** for a kill-switch *drill* and a *live*
  run on the same date/book (they differ only by `out_dir`/audit path), so keying the ledger on
  `run_id` would falsely flag the second valid run as replacement tamper. Keying on the audit
  **path** distinguishes drill vs live (different `out_dir`s) while still detecting a *same-path*
  replacement. **`run_id` is left UNCHANGED** so the agent_os memory-ingest identity check
  (`run_id == f"{date}-{book}"`, the `IdentityMismatch` guard from commit `6ce5db1`) is untouched.
  Each line: `{audit_path, run_id, date, book, run_kind, count, root_hash}` (`run_kind` =
  drill/paper, for readability).
- **Append-only + monotonic:** the ledger is append-only (a log legitimately re-opened and extended
  the same day appends a *new* seal line for the same path with a larger `count`). `verify(strict)`
  compares the on-disk chain to the **most-recent** sealed line **for that path** and requires the
  on-disk `count >= sealed count` with the sealed prefix intact ⇒ a legitimate later append passes;
  a truncation below, or a divergent replacement of, a sealed root **fails**.
- **Two verify modes, defined callers:** `verify()` (anchor-based) is the **per-pass report
  badge** (`build_book_report`, `report.py:244`). `verify(strict=True, ledger_path=...)`
  additionally compares the day's `(count, root_hash)` to the sealed ledger line ⇒ a **wholesale
  internally-consistent replacement fails**; it is the **paper→live go/no-go check** (run by the
  metric gate, not per pass). The report shows the anchor badge; the go-live gate adds the ledger
  check. For a gate-eligible run, an **absent/mismatched** ledger entry ⇒ strict verify False
  (**Q2**).
- **Residual (deferred, documented):** a same-tree sidecar/ledger is co-tamperable; a true WORM /
  KMS-signed store is **live-trading** hardening, not P0 paper. This slice makes *accidental*
  truncation/corruption and *naive* replacement fail-closed now, and names the residual honestly.

### D7 — portfolio mark honesty WITHOUT NaN-poison or report crash (PF-001/004; fixes the v1 self-collision)
- `_PositionState` gains `marked: bool` (or `ltp=NaN` sentinel). `mark_to_market` (PF-004) **rejects
  non-finite prices** (`is_finite_number`); absent/non-finite ⇒ position stays **unmarked**.
- **`equity()` / `holdings_value()` NEVER return non-finite.** An unmarked held position is valued
  **at cost basis** (its unrealized contribution = 0) — a conservative, finite, documented
  convention — and the portfolio exposes `marks_complete: bool` / `unmarked_symbols: list`.
  **⇒ one unpriceable holding does NOT poison the shared equity snapshot, does NOT freeze other
  entries (W5), and does NOT crash the report.** The unpriceable symbol's *own* entry is still
  blocked (its `ref_price` is non-finite → D3).
- **Report:** `build_book_report` reads `marks_complete`; if False it sets `marks_incomplete=True`
  + `unmarked_symbols=[...]` and the session is flagged **not go-live-eligible**. Any residual
  non-finite money field is coerced to JSON `null` before serialization; `to_json_line` uses
  `allow_nan=False` as a **belt-and-suspenders guard** (it should never fire); `write_jsonl`
  wraps each line so one book can't drop the others. **The JSONL is always written and valid.**
- The gate-level finite guards (D3) remain the last line of defence on the public surface.

### D8 — PF invariants raise + router catches per-symbol (Codex #1 — was an unbacked claim in v1)
`apply_fill` adds fail-loud guards: `settled_cash >= -eps` after a BUY debit (PF-002) and
`fill.side is charges.side` (PF-003) ⇒ `raise ValueError` (like the existing T+1 over-sell guard
`portfolio.py:114`). **New build step:** `router._process_one` wraps `compute_charges` +
`apply_fill` (`router.py:218-220`) in a `try/except (ValueError)` ⇒ audit a `fill_error` record +
return `ExecutionOutcome(kind="rejected", reason="portfolio_invariant_violation")` ⇒ **one bad
fill is recorded, the pass is not sunk.** Tests for both the raise and the router catch. **Q4:**
raise+catch (recommended) vs clamp-and-audit.

### D9 — `BookReport.from_dict` + de-dup the existing deserializer (RPT-001)
Add `BookReport.from_dict`/`from_json_line` with explicit coercions (incl. the D7 `null`/flag
fields) + a `to_json_line → from_dict` round-trip test. **Migrate `agent_os/memory/ingest.py:
_dict_to_book_report` to delegate** to it + a test asserting both paths agree, so there is one
canonical deserializer (else RPT-001 just adds a *third* mapping).

## 6. Build order (tests-first; each task: red test → impl → green)

**Batch A — non-finite (NaN ∪ ±Inf) hardening**
- **A1** `is_finite_number` + tests (NaN/±Inf/None/int/float/str).
- **A2** `size()` finite guards (D3) + tests **parametrized over NaN and ±Inf** for
  equity/ref_price/confidence: ENTRY ⇒ `no_trade` (no `round` crash — assert the outcome, not the
  exception type, since NaN→ValueError but Inf→OverflowError); **REDUCE on unusable price ⇒
  deadband-bypassed de-risk** (proceeds, audited); EXIT ⇒ holdings-derived qty.
- **A3** `_ref_price` + **router MTM filter** finite-and-positive (D2) + tests: NaN ltp ⇒ 0.0;
  **+Inf bid/ask ⇒ `mid` is +Inf ⇒ rejected ⇒ 0.0** (the load-bearing `isfinite(mid)`); Inf/neg
  ltp ⇒ not marked.
- **A4** gate finite guards RG-01/03/07 + **T7-02** (D3) + tests: NaN/Inf input ⇒ **entry blocked**,
  **exit `_ok`/`_warn` unchanged**; `g_buying_power` with `quote.ask=NaN/Inf` ⇒ blocked;
  `g_daily_loss_limit` fresh-zero-equity book ⇒ **ok** (T7-02), non-finite equity ⇒ block.
- **A5** ingress validation in **`execution/brokers/angel_quotes.py:parse_full_response`** (so the
  broker never sees a non-finite quote) + a log on every reject + tests with synthetic NaN **and
  Inf** FULL payloads.
- **A6** RG-08 end-to-end matrix via the router: NaN-quote ENTRY and **Inf-quote ENTRY** ⇒ blocked
  + audited (pass completes, no crash); **NaN-quote EXIT and Inf-quote EXIT** ⇒
  `intended_exit_unfilled` (the Inf-EXIT case is the one that would otherwise bogus-fill).

**Batch H — audit trust-anchor + portfolio/report**
- **H1** `_read_records_safe` + `verify()`/`_tail_hash` never raise; reject missing **and extra**
  keys (AUD-002/004/005); non-resumable tail ⇒ `is_writable()` False (D5) + tests (corrupt line,
  short record, missing-`hash` tail, extra-key record; `build_book_report` renders ❌ not crash).
- **H2** anchor sidecar created-at-init + atomic per-append + crash-consistency (D4) + tests:
  truncate / zero / drop-middle ⇒ False; clean mid-append-crash ⇒ True+warn; empty+anchor ⇒ True;
  non-empty + absent anchor ⇒ False; gate#10 covers anchor writeability.
- **H3** `seal_root` + `verify(strict=True, ledger)` + defined callers (D6) + tests: wholesale
  internally-consistent replacement ⇒ strict False; absent ledger entry for a gate-eligible run ⇒
  strict False.
- **H4** portfolio: `marked`/finite `mark_to_market` (PF-004) + **finite equity fallback** + 
  `marks_complete`/`unmarked_symbols` (D7) + tests: unmarked held (incl. **held-but-unsignalled**)
  ⇒ equity finite (cost-valued), NOT NaN, entries for *other* symbols proceed; Inf price rejected.
- **H5** PF-002 negative-cash + PF-003 side-mismatch raises **and** the router per-symbol catch
  (D8) + tests (raise; pass not sunk; `fill_error` audited).
- **H6** report: coerce non-finite money → `null` + `marks_incomplete`/`unmarked_symbols` flag;
  `allow_nan=False` guard; resilient `write_jsonl` (RPT-002 + D7) + tests: held-but-unsignalled ⇒
  report renders with the flag, JSONL valid, both books written, not go-live-eligible.
- **H7** `BookReport.from_dict`/`from_json_line` + migrate `ingest.py` duplicate to delegate
  (RPT-001/D9) + round-trip test + both-paths-agree test.
- **H8** (offered, Q3) RPT-003 coverage-denominator consistency + test.

## 7. Non-goals (this slice — explicit ❌)
- ❌ Batches B–G and §8 leftovers; **RG-05** crossed-book, **IDA-01** calendar fail-loud,
  **SS-03/SS-04** (session bridge try/except + loop-local `day_pnl`), **RG-06/SS-02** general
  priceable-REDUCE deadband — these are the highest-value of the *next* batches but are not
  P0-for-paper and stay out to keep this slice reviewable. (§5 D3 fixes only the
  *non-finite-price* REDUCE case; §5 D8 only the *router fill-path* catch.)
- ❌ A true external/WORM audit store (live-trading hardening).
- ❌ Any change to the gate chain's order/membership, the order path, or the exit-asymmetry rule.

## 8. Acceptance criteria
1. Re-running the doc-17 probes (and the new Inf probes) for every IN finding shows fail-closed:
   NaN **and Inf** quote ENTRY ⇒ blocked+audited+no-crash; NaN **and Inf** quote EXIT ⇒
   `intended_exit_unfilled` (no bogus Inf fill); truncated/zeroed/corrupt/extra-key/replaced audit
   ⇒ `verify()`/`verify(strict)` False; corrupt audit ⇒ report renders (no crash); damaged tail at
   open ⇒ `audit_writable` False (gate#10 blocks trading); held-but-unsignalled symbol ⇒ equity
   finite, entries for other symbols proceed, JSONL valid + `marks_incomplete`.
2. The exit-asymmetry invariant holds **gate-by-gate** under NaN and Inf (explicit tests), and
   T7-02 (fresh-zero-equity book) is **not** regressed.
3. Full suite green; **zero regressions** in `execution/`, `tradingagents/`, `agent_os/`.
4. Tests-first negative tests for every IN finding (RG-08 matrix + the H-series), parametrized
   over NaN and ±Inf where numeric.
5. This doc reviewed and signed off before any code.

## 9. Open questions for the reviewer
1. **Q1 (sizing reason):** reuse `"no_price"` for non-finite entry inputs (routes through the
   existing nominal-gate block) or a distinct `"non_finite"` reason mapped the same way?
2. **Q2 (root-of-trust strength):** anchor (created-at-init, atomic) + sealed daily-roots ledger
   as in D4/D6; strict verify is the **go-live gate** caller (report shows the anchor badge).
   Absent/mismatched ledger for a gate-eligible run ⇒ strict False (recommend). Same-tree sidecar
   acceptable for paper with WORM deferred to live?
3. **Q3 (RPT-003):** fold the coverage-denominator fix into this slice (cheap) or defer?
4. **Q4 (PF invariants):** PF-002/003 raise + router per-symbol catch (recommended) vs
   clamp-and-audit?
5. **Q5 (equity fallback):** value an unmarked held position at **cost basis** (finite, unrealized
   0, flagged `marks_incomplete`, session not go-live-eligible) — confirm vs alternatives
   (last-known stale mark; or block the whole pass). Recommend cost-basis + flag (no book-wide
   freeze, honest, fail-closed at the go-live gate).

## 10. Changes in v2 (review round 1 — finding → change, for the reviewer)
- **Codex #1 / D8:** PF-002/003 "caught per-symbol" is now a real build step (H5) — router wraps
  the fill path; was an unbacked claim in v1.
- **Codex #2 / D4+D6:** audit root **lifecycle** fully specified — anchor created-at-init + atomic;
  `seal_root` at session close; `verify()` (badge) vs `verify(strict)` (go-live gate) callers;
  absent ⇒ False; gate#10 covers the new files.
- **Codex #3 / D3:** REDUCE on a non-finite/zero price now **bypasses the deadband** (de-risk
  proceeds → `intended_exit_unfilled`) instead of a silent deadband no-trade; full-EXIT was already
  safe. General priceable-REDUCE deadband (RG-06) explicitly stays OUT.
- **Codex #4 / D5:** AUD-005 (extra-keys) **folded firmly IN** (part of `verify()` purity); the v1
  "optional vs specified" contradiction removed.
- **Codex #5 + internal blocker / D7+H6:** the v1 "unmarked ⇒ NaN equity + `allow_nan=False`"
  design **crashed the report** on a routine held-but-unsignalled symbol; replaced with a **finite
  cost-basis fallback + `marks_incomplete` flag + resilient `write_jsonl`** — JSON always valid, no
  book-wide entry freeze.
- **Internal blocker / D3:** **T7-02** (the `g_daily_loss_limit equity<=0` branch) was silently
  dropped and **collided** with the RG-03 fix; now folded IN and reconciled (non-finite→block,
  `==0`→ok).
- **Internal important / §3, D2, A2-A6, H4:** **Inf** was under-specified (v1 reasoned NaN-only).
  Corrected the §3 reachability claim (Inf passes the router MTM filter → +Inf equity *today*);
  added finite-and-positive MTM filter, `isfinite(mid)`, the Inf-EXIT bogus-fill case, and
  NaN-and-Inf parametrized tests; noted `round(inf)`→OverflowError.
- **Internal important / D3:** `g_buying_power` finite-guard now checks the **final selected
  price** (covers `quote.ask=NaN/Inf`), not just `ref_price`.
- **Internal / D9:** RPT-001 now also **migrates the existing duplicate** deserializer
  (`agent_os/memory/ingest.py`) to the new `from_dict` so there's one canonical mapping.
- **Internal / A5:** ingress validation pinned to `parse_full_response` (so the PaperBroker never
  sees a non-finite quote), not just `get_quotes`.

### Round 2 (Codex — finding → change)
- **Codex #1 / D6:** the daily-roots **ledger identity** now keys on the **audit path** (+ `run_kind`),
  not `run_id` — a drill and a live run share `run_id = f"{date}-{book}"` (`session.py:210`) and
  would have falsely collided. Ledger is append-only + monotonic. `run_id` is **unchanged**, so the
  memory-ingest `IdentityMismatch` check is untouched.
- **Codex #2 / D4:** the **anchor-creation invariant** is sharpened — create `{0, GENESIS}` only for
  a fresh/empty audit; a **non-empty audit with an absent anchor is NOT re-anchored** (the wipe
  case) ⇒ `verify()` False + `is_writable()` False; explicit test in H2.
- **Codex #3 / README:** row 18 updated to state NaN **∪ ±Inf** + the finite cost-basis fallback +
  `marks_incomplete` (was understating the Inf fix and still implying the v1 NaN-poison design).
