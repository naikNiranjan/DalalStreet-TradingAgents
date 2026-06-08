# 17 — Codebase Audit Findings (issue catalog)

> Status: **FINDINGS CATALOG — input to a detailed fix plan (not the fixes).**
> Produced by an adversarial multi-subsystem audit (7 Sonnet auditors, each probing its area
> with throwaway `/tmp` scripts for empirical evidence) over the safety-critical spine, the
> India/Azure adaptations, and test honesty. The two **critical** and the sharpest **high**
> findings were independently re-probed against the real code by the main loop.
> No code is changed by this document. Severity = impact-if-it-fires (this is a paper-first,
> real-money-bound system, so fail-open / look-ahead / silent-loss are weighted heavily).
>
> **Coverage note:** all 7 subsystems complete (the AUDIT + PORTFOLIO + REPORT auditor was
> re-run after a 429; its findings are in §8). The two §8 criticals were re-probed by the main loop.

---

## 1. Severity summary (all 7 subsystems)

| Severity | Count | Meaning |
|----------|------:|---------|
| critical | 4 | fail-open / crash on the safety core or the audit trust-anchor; fix before the next live-data paper run |
| high | 14 | fail-open gate, silent stale fill, look-ahead, graph-crash, or portfolio/report correctness |
| medium | 22 | correctness / de-risk / cost / look-ahead gaps |
| low | 17 | hardening, provenance, debuggability, coverage |
| nit | 6 | comments / labels / brittle constants |
| **total** | **63** | across spine + India/Azure adapters + audit/portfolio/report + test honesty |

## 2. The five themes (how to batch the fix plan)

1. **NaN / non-finite inputs are unguarded everywhere (the big one).** IEEE-754 makes every
   `x < limit` / `x <= 0` / `x > cap` comparison return **False** when `x` is NaN, so the
   confidence floor, daily-loss, position-size, and buying-power gates **all fail open** on a
   NaN, and `sizing.size()` **crashes** (`round(nan)`). Ingress is real: a failed Angel quote
   gives `ltp=nan` → `portfolio.equity()=nan` → propagates. **Single themed fix:** a
   `finite_or_block` helper + validate quotes/equity at ingress (reject non-finite). Findings:
   RG-01, RG-02, RG-03, RG-07.
2. **Look-ahead / time-sign bugs.** Negative "age" (future timestamps) passes the freshness and
   stale-quote gates; news horizon off-by-one at midnight; screener fundamentals are always
   *today's* (look-ahead in backtests); Angel candle date assumes IST. Findings: RG-04/T7-01,
   IDA-02, IDA-03, IDA-08, IDA-09.
3. **Silent fail-open / silent loss.** Stale-quote cache could fill exits at hours-old prices in
   reused-broker+adapter mode (F1 — latent on the committed single-pass runner); REDUCE de-risk
   silently deadbanded on small positions (RG-06/SS-02);
   calendar silently falls back to a 3-holiday default (IDA-01/IDA-11); vendor fallback swallows
   errors with no log (IDA-05); crossed book (bid>ask) passes the spread gate (RG-05).
4. **Test honesty / coverage gaps.** No tests for the NaN/boundary/look-ahead cases above;
   broad `except Exception: pass` masking; non-adverse tick rounding hidden by a weak assertion;
   brittle hard-coded gate count; bare `assert x is not None`. Findings: RG-08, T7-01..06, F4,
   IDA-10, SS-06, S6-009.
5. **The audit chain is not tamper-evident against the simplest attacks, and crashes on
   corruption (the trust anchor).** The whole go-live gate is "100% audit coverage AND chain
   verifies" — but `verify()` returns True for a **truncated tail or a zero-byte file** (no
   committed length/root anchor), True for a **wholesale chain replacement** (no external
   root-of-trust), and **raises** `JSONDecodeError`/`KeyError` on a corrupt/short record instead
   of returning False — which crashes `build_book_report` (and `is_writable` says the path is
   fine while `__init__` throws). Findings: AUD-001..005. This undermines the audit guarantee the
   paper→live decision depends on.

---

## 3. CRITICAL (fix before the next live-data paper run)

| id | file:loc | claim | suggested fix | conf |
|----|----------|-------|---------------|------|
| **RG-01** | `execution/risk/guards.py:175` `g_confidence_floor` | NaN confidence bypasses the 0.60 floor (`nan < 0.60` is False) → an unvalidated STRONG_BUY/BUY entry passes. SignalDecision is a public frozen dataclass; the gate must be the last line of defence. | `if not math.isfinite(conf) or conf < floor: block` | confirmed (re-probed) |
| **RG-02** | `execution/risk/sizing.py:79` `size()` | NaN `equity`/`ref_price` → `round(nan)` raises `ValueError`, **uncaught**, aborting the whole execution pass. `ref_price = quote.mid or quote.ltp or 0.0` yields `nan` (since `bool(nan)` is True). Reachable via a bad Angel quote → `equity()=nan`. | guard `size()`: `if not math.isfinite(equity) or equity<=0` / `if not math.isfinite(ref_price)` → no-trade; validate equity in router | confirmed (re-probed) |

## 4. HIGH

| id | file:loc | claim | suggested fix | conf |
|----|----------|-------|---------------|------|
| **RG-03** | `guards.py:221,231` daily_loss / position_size | NaN equity makes both size-exposure gates fail open (cap = k·nan = nan; `pnl <= nan` / `prospective > nan` are False). | central: `if not math.isfinite(equity) or equity<=0: block` (or validate in `GateContext.__post_init__`) | confirmed |
| **RG-07** | `guards.py:250` `g_buying_power` | NaN `ref_price` → `price<=0` misses it, `cost=nan`, `nan>bp` False → passes despite no funds. | `if price<=0 or not math.isfinite(price): block` | confirmed (re-probed) |
| **RG-04 / T7-01** | `guards.py` `g_data_fresh`(137), `g_stale_quote`(188) | **Future timestamps pass both clock gates** (negative age; `age>limit` always False) → look-ahead / clock-skew data accepted. No test. | `if age < -tol: block('timestamp in the future')`; add tests | confirmed |
| **RG-05** | `contracts.py:229-239` `Quote.spread_frac`; `guards.py:207` | **Crossed book (bid>ask)** → negative `spread_frac`, never `> spread_hard` → spread gate passes a pathological market. | treat `spread_frac < 0` as crossed_market → block entry (or `has_book=False`) | confirmed (re-probed) |
| **SS-01** | `session.py:198` `run_execution_phase` | `IndexError` when `books=()` and `signals` non-empty (`books[0]` accessed after the signals check). Only if a caller passes empty books, but the signature accepts any Iterable. | `books = tuple(books); if not books: return []` | confirmed |
| **IDA-01** | `dataflows/india_calendar.py:51` `_holiday_set()` | Silent fallback to a **3-entry** holiday default when `nse_holidays` is None — movable holidays (Holi/Good Friday/Diwali) treated as **open**. Any caller bypassing `assert_calendar_ready` is exposed (e.g. `PaperBroker.is_market_open` — see IDA-11). | `logger.warning` + ideally raise `CalendarNotConfigured` when unset | confirmed |
| **S6-001** | `llm_clients/capabilities.py:133` | **Mixed-case** DeepSeek model id (e.g. `deepseek-V4-pro`) misses the case-sensitive pattern → `_DEFAULT` (tool_choice=True) → DeepSeek HTTP 400. | add `re.IGNORECASE` / lower() the id before lookup | confirmed |
| **S6-002** | `agents/utils/structured.py:37` `bind_structured` | Only catches `(NotImplementedError, AttributeError)`; a `TypeError`/`ValueError`/`RuntimeError` from `with_structured_output` **crashes the agent factory at graph-build** instead of falling back to free-text. | widen to `except Exception` (matches the invocation wrapper + docstring) | confirmed |

## 5. MEDIUM

| id | file:loc | claim | suggested fix |
|----|----------|-------|---------------|
| **F1** *(was HIGH; rescoped)* | `brokers/paper.py:80,130` | **Stale-quote cache fill — latent on the committed path.** In **reused-broker + adapter mode** (repeated `get_quotes` rounds on one `PaperBroker` with a live adapter), a later adapter failure returns `{}` for a symbol while `self._quotes` keeps the prior round's quote → `place_order`→`_simulate` can fill an **exit against an hours-old price** (stale-quote only warns on exits). **HIGH in that mode.** Not reachable on the committed one-session runner, which builds a fresh adapter-less `PaperBroker` per book and primes it with the current shared snapshot (`session.py:214-215`). | evict requested-but-missing symbols from `self._quotes`; or simulate fills only from router-primed quotes — **before** enabling reused-broker/adapter mode |
| **RG-06 / SS-02** | `sizing.py:91-95` | REDUCE de-risk silently **deadbanded** on positions < ~31 shares @ ₹1300 (incl. a fresh BUY-medium entry) → can only EXIT, not trim; no warning. | exempt REDUCE from deadband (like EXIT) **or** audit/log the suppression |
| **SS-04** | `router.py:140` | `day_pnl` snapshotted once before the per-symbol loop → after the first loss fills, later symbols' daily-loss gate still sees pre-loss pnl (can allow buys that should be blocked). | recompute `day_pnl` at the top of the loop body (keep equity snapshot for sizing) |
| **SS-03** | `session.py:130-141` | `build_data_freshness`/`build_signal` are **outside** the per-symbol try/except → a non-PortfolioDecision (e.g. dict) makes `build_signal` raise and **sinks the whole session** ("one bad symbol must not sink the session"). | extend the try/except to cover freshness+bridge; record `skipped[symbol]` |
| **F2** | `security_master.py:59` `round_to_tick` | Non-adverse tick rounding (nearest, not ceil-buy/floor-sell) → paper fills up to ₹0.05/share in the trader's favour; overstates P&L. Test only checks `> ask`. | `ceil_to_tick` for BUY, `floor_to_tick` for SELL |
| **F3** *(corrected — original claim was wrong)* | `config.py:58,65-67` `CostConfig` | The original finding ("Angel delivery is ₹0 since Nov 2022 → set brokerage to 0") is **stale/wrong**: `CostConfig` already models delivery brokerage as **lower of ₹20 or 0.10%, min ₹5** (`brokerage_rate=0.001`, `brokerage_max=20`, `brokerage_min=5`), which **matches Angel One's current published equity-delivery charge** (lower of ₹20 or 0.1% per order, min ₹5 — verified at angelone.in, Jun 2026; Angel reintroduced delivery brokerage). Setting it to ₹0 would **understate** cost and make the shadow-book go/no-go too optimistic. | **No code change.** Verification-only: confirm `brokerage_rate/max/min` against an actual Angel One contract note before live (published rates drift). Citation-needed, not a fix. |
| **IDA-02** | `dataflows/india_news.py:113` | Horizon off-by-one: `pub > curr+1day` includes an article at **exactly** next-day midnight IST → leaks one day. | `pub >= horizon` (strict) |
| **IDA-03** | `dataflows/india_fundamentals.py:78` | screener.in always returns **today's** fundamentals regardless of `curr_date` → undisclosed look-ahead in backtests. | suppress screener for historical dates / add recency disclaimer |
| **IDA-04** | `security_master.py:92` `_pick_token` | BSE token pick is order-dependent (bare base match) → could select an F&O/ETF contract before the cash equity (NSE `-EQ` guard not applied to BSE). | filter `instrumenttype not in (FUT,OPT,CE,PE)` for BSE |
| **IDA-05** | `dataflows/interface.py:164` `route_to_vendor` | Bare `except Exception` swallows primary-vendor failures with **no log** → "Angel down all day, yfinance served" is invisible in production. | `logger.info` on every vendor fallback |
| **S6-003** | `agents/utils/rating.py:43` `parse_rating` | First-occurrence word-scan can return the **wrong** rating from free-text ("Previously Hold… now Sell" → Hold). Mitigated in the execution path (bridge forces HOLD) but live in the CLI `process_signal` path. | anchor to last label/paragraph; prefer the typed PM path |
| **S6-004** | `cli/main.py:1243` | `decision = graph.process_signal(...)` is **dead code** (never read) → any wrong/raised rating is invisible. | remove or wire to a display/audit field |
| **S6-005** | `llm_clients/validators.py:6` | The `'custom'` CLI sentinel passes `validate_model` for deepseek/qwen/glm/minimax → forwarded to the API as a literal model name. | filter `'custom'` out of `VALID_MODELS` |
| **S6-006** | `llm_clients/azure_foundry_client.py:59` `_route` | Prefix tuple `('gpt','o1','o3','o4')` misses `o2`/`o5+` → new Azure OpenAI families silently route to the Foundry endpoint (wrong key). | `re.match(r'^o\d', model)` |
| **S6-007** | `llm_clients/capabilities.py` | Unknown model silently gets `_DEFAULT` (tool_choice=True) with no warning → first call may 400. | `warnings.warn` on unknown model |
| **T7-02** | `guards.py:217` `g_daily_loss_limit` | Zero equity + zero pnl → `0 <= 0` True → **blocks all entries** for a fresh untraded book (false positive). | `if equity<=0: ok('limit undefined')` or strict `<` |
| **T7-03** | `tests/agent_os/test_toolset_isolation.py:177,320` | Two dispatch tests use `except Exception: pass` → green even if dispatch is totally broken. | catch only expected operational exceptions; assert not structural |
| **T7-04** | `tests/execution/test_portfolio.py` | No test for **partial SELL** settlement (partial BUY is tested) → a regression in SELL+partial would go undetected. | add `test_partial_sell_reduces_qty_and_settled` |

## 6. LOW / NIT (hardening + coverage; condensed)

- **RG-08** (test): no tests for NaN-confidence / NaN-equity / crossed-market / future-quote / future-freshness → add them alongside the §3–4 fixes.
- **RG-09 / SS-05 / SS-06** (nit/low): `spread_warn` comment misleading; duplicate `_in_execution_window` in session vs router; `test_sizing` deadband comment cites the wrong threshold.
- **F4 / F5 / F6** (low/nit): no test for adapter-fail→stale-cache (F1's guard); silent quote loss on Angel token collision; NSE txn-rate source citation missing.
- **IDA-06..IDA-11** (low/nit): double-suffix ticker (`.NS.NS`) confusing error; no screener in-process cache (ToS request volume); Angel `todate` over-fetches one day (filter corrects it); candle date assumes IST (no offset assertion); no look-ahead test for `get_indicators_angel`; `PaperBroker.is_market_open` bypasses configured holidays.
- **S6-008..S6-011** (low/nit): unrecognised rating → HOLD but keeps conviction-confidence (misleading audit); `supports_json_schema/json_mode` are metadata-only (unenforced); conviction never persisted to the memory log; `parse_rating` multi-line label edge.
- **T7-05 / T7-06** (low): `assert len(results) == 15` brittle → use `len(_GATES)`; ~11 bare `assert episode is not None` in memory tests → assert content.

## 7. Suggested batching for the fix plan (starting point — yours to refine)

1. **Batch A — non-finite hardening (P0):** a `require_finite`/`finite_or_block` helper used by every numeric gate + `sizing.size()` + validate quote `ltp/mid`/`equity` at ingress (reject non-finite → block + audit). Closes RG-01/02/03/07. Add the missing NaN tests (RG-08).
2. **Batch B — time-sign / look-ahead:** future-timestamp block in `g_data_fresh`/`g_stale_quote` (RG-04/T7-01); news horizon strict `>=` (IDA-02); screener historical suppression (IDA-03); Angel `todate`/IST hardening (IDA-08/09). Tests for each.
3. **Batch C — silent fail-open:** stale-quote cache eviction (F1 — latent today; do before enabling reused-broker/adapter mode); REDUCE deadband audit/exempt (RG-06/SS-02); calendar fail-loud (IDA-01/IDA-11); vendor-fallback logging (IDA-05); crossed-book block (RG-05). 
4. **Batch D — session robustness:** wrap bridge in the per-symbol try/except (SS-03); empty-books guard (SS-01); loop-local `day_pnl` (SS-04).
5. **Batch E — LLM/structured resilience:** case-insensitive capabilities + unknown-model warning (S6-001/007); widen `bind_structured` except (S6-002); `o\d` routing (S6-006); `'custom'` sentinel (S6-005); `parse_rating`/dead-code cleanup (S6-003/004).
6. **Batch F — cost fidelity:** adverse tick rounding (F2). *(F3 dropped as a code fix — the brokerage model already matches Angel One's current delivery charge; the "set to ₹0" claim was stale. Remaining F3 action is a pre-live contract-note verification, not a code change.)*
7. **Batch G — test honesty:** partial-SELL (T7-04); broad-except tightening (T7-03); `len(_GATES)` (T7-05); content assertions (T7-06).
8. **Batch H — audit integrity (trust anchor; see §8):** committed length/root anchor so truncation/wipe fails verify (AUD-001); `verify()` returns False (not raises) on corruption (AUD-002); external root-of-trust vs wholesale replacement (AUD-003); `_tail_hash` corrupt-tail handling + gate#10 alignment (AUD-004); portfolio `mark_to_market`/negative-cash/side-mismatch guards (PF-001/002/003); `BookReport.from_dict` (RPT-001).

> Each batch should be built **tests-first** and go through the plan → review → fix → review →
> commit cadence. **One consistent gate (used by §3 and §8): before the next _live-data paper run_,
> Batch A (non-finite — real Angel quotes are the NaN ingress) and Batch H (the audit trust-anchor the
> paper→live go/no-go metric depends on) are the non-negotiable must-fix-first.** Batches B–D are
> safety-relevant and should follow as soon as possible (sequenced); within them RG-05 (crossed-book),
> IDA-01 (calendar fail-loud) and SS-04 (loop-local day_pnl) are highest-value because they can
> fail-open on *live* data. Real-money live requires all of A–H (+ priced E–G).

## 8. AUDIT + PORTFOLIO + REPORT (15 findings)

Good news first: the hash-chain **does** detect a tampered payload, a tampered hash, a deleted
middle record, reordering, and a wrong `prev_hash`; T+1 correctly blocks selling unsettled qty
(probed). The gaps below are the ones to fix.

### CRITICAL (audit trust-anchor)

| id | file:loc | claim | suggested fix | conf |
|----|----------|-------|---------------|------|
| **AUD-001** | `execution/audit.py:120` `verify()` | **Fail-open on truncation.** Deleting the last N records — or zeroing the whole file — leaves a valid chain; `verify()` returns True. No committed length/root anchor, so the most basic tamper (drop records) is undetectable. | persist committed chain length (+last hash) in a sidecar / external append-only store; assert on verify() | confirmed (re-probed) |
| **AUD-002** | `execution/audit.py:96,132` `read_all`/`verify` | A corrupt/partial line makes `verify()` **raise `JSONDecodeError`** (not return False) → `build_book_report` (`audit_chain_ok=audit.verify()`) **crashes the report**. Same for `_tail_hash` in `__init__` (crashes session open) while `is_writable` says the path is fine (gate#10 false positive). | wrap `json.loads` → on parse error return False / treat as corrupt-tail; `_tail_hash` use `.get('hash', GENESIS)` | confirmed (re-probed) |

### HIGH

| id | file:loc | claim | suggested fix | conf |
|----|----------|-------|---------------|------|
| **AUD-003** | `audit.py:120` `verify()` | No external root-of-trust → a **wholesale chain replacement** with a new internally-consistent chain (different payloads/run_id) passes verify()=True. | append the day's root hash to a write-once external store; compare on verify() | confirmed |
| **AUD-004** | `audit.py:86` `_tail_hash` | `KeyError` if an existing record lacks a `hash` key → crashes `AuditLog.__init__` while `is_writable` returns True → gate#10 `audit_writable` is a false positive. | `records[-1].get('hash', GENESIS_HASH)` + warn | confirmed |
| **PF-001** | `portfolio.py:207` `equity`/`unrealized_pnl` | If `mark_to_market` was never called, `ltp` defaults to **0.0** → `equity()` omits stock value and `unrealized_pnl` = −cost_basis (deeply negative). Misleading numbers flow into the report. | init `ltp=nan`/None; guard MTM-derived values to nan/0 when unmarked | confirmed |
| **PF-002** | `portfolio.py:100` `apply_fill` | No guard against **negative `settled_cash`** — a BUY larger than cash silently drives equity negative (portfolio doesn't enforce the invariant even though gates should block upstream). | assert `settled_cash >= -eps` after a BUY debit | confirmed |
| **PF-003** | `portfolio.py:100` `apply_fill` | No cross-check that `fill.side == charges.side` → BUY charges on a SELL fill records negative proceeds and a wildly wrong realized_pnl, silently. | assert `fill.side is charges.side` at entry | confirmed |
| **RPT-001** | `report.py:107` `BookReport` | **No `from_dict`/`from_json_line`** (CoverageManifest has one) → the session JSONL can't be deserialized back; any consumer (backtest compare, paper→live gate) re-implements field mapping → divergence risk. | add `@classmethod from_dict` with explicit type coercions | confirmed |

### MEDIUM / LOW (condensed)

- **AUD-005** (med): `verify()` ignores **extra JSON keys** (only `_BODY_KEYS` are hashed) → attacker-injected metadata undetected. Fix: assert record key-set == body+`hash`.
- **PF-004** (med): `mark_to_market` skips symbols absent from the price map (stale marks indistinguishable from fresh) and **propagates NaN ltp** into equity/holdings silently. Fix: validate finite prices; reset stale ltp to nan.
- **RPT-002** (med): `to_json_line` uses default `json.dumps` (`allow_nan=True`) → NaN/Inf emit **invalid JSON** (rejected by strict parsers). Fix: `allow_nan=False` (raise eagerly).
- **RPT-003** (med): `outside_hours` is folded into `no_trade` but **excluded from the audit-coverage denominator** → an all-outside-hours session reports `audit_coverage=100%` with zero audit records. Fix: use one consistent filter rule; document.
- **PF-005** (low): zero-qty **ghost positions** retained in `_positions` after full sells (harmless but accumulates). Fix: drop symbol when total_qty==0 and no unsettled lots.
- **RPT-004** (low): `block_reasons` drops blocked outcomes with `gate_block=None` (`and o.gate_block`) → incomplete reason breakdown. Fix: `is not None` / map None→'unknown'.
- **RPT-005** (low): `_fill_totals` counts a **phantom fill** (increments `num_fills`) even when the payload's fill qty/price are empty → skews `avg_cost_per_trade`. Fix: only count when qty>0 and price>0.

> Add **Batch H — audit-integrity** to the fix plan: AUD-001/002/003/004 (the trust anchor) +
> PF-001/002/003 + RPT-001. This is **safety-critical and must land before the next live-data paper
> run** (with Batch A) — the paper→live gate's "100% audit, chain verifies" promise is only as strong as `verify()`.
