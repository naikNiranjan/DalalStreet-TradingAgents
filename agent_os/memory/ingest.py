"""Report -> memory ingestion for the agent_os episodic tier.

DETERMINISTIC, EVIDENCE-BASED, FAIL-CLOSED.

The ONLY write path is ``ingest_session``.  There is no LLM-written memory
path.  Every episode is derived algorithmically from the audit log and session
report — not from model-generated text.

THE ONE INVARIANT: this module never constructs a SignalDecision, never calls
a broker, and never bypasses a risk gate.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from execution.audit import AuditLog
from execution.report import BookReport, CoverageManifest
from agent_os.memory.tiers import TierStore


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IdentityMismatch(ValueError):
    """Raised when the audit log contains more than one distinct run_id.

    Audit files can be appended across runs; if multiple run_ids are present
    the session identity is ambiguous and ingestion is rejected to prevent
    mixing evidence from different trading sessions.
    """


class IngestRejected(ValueError):
    """Raised when ingestion is rejected for a deterministic, non-chain reason
    (e.g. (session_date, book) not found in the report).
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_session(
    *,
    audit: AuditLog,
    report_jsonl_path: str,
    session_date: str,
    book: str,
    tier_store: TierStore,
    max_symbols: int = 50,
    expected_run_id: Optional[str] = None,
) -> Optional[dict]:
    """Ingest one trading session into the episodic memory tier.

    Parameters
    ----------
    audit:
        The AuditLog for the session.  Its ``verify()`` must return True;
        otherwise ingestion is fail-closed (returns None).
    report_jsonl_path:
        Path to the session JSONL report written by ``SessionReport.write_jsonl``.
        The first line may be a coverage record (``"record": "coverage"``) or
        directly a BookReport line (legacy / no-coverage run).
    session_date:
        The trading date string to look up in the report (e.g. "2026-01-15").
    book:
        The book name to look up in the report (e.g. "signal" or "golive").
    tier_store:
        TierStore to write the episode into.  Exactly one entry is appended on
        success.
    max_symbols:
        Maximum number of symbol_outcomes to include in the episode.
    expected_run_id:
        The run_id the audit MUST belong to. Defaults to ``f"{session_date}-{book}"``
        — the convention the spine uses (``execution/session.py``). The audit's single
        run_id must equal this, otherwise ingestion fails closed (audit evidence from a
        different run/book is never spliced onto this book's report).

    Returns
    -------
    dict
        The episode dict written to the tier store.
    None
        Returned (instead of raising) when ``audit.verify()`` is False.
        All other failure conditions raise.

    Raises
    ------
    IdentityMismatch
        If the audit log contains records from more than one distinct run_id, OR the
        audit's run_id does not match ``expected_run_id`` / ``f"{session_date}-{book}"``.
    IngestRejected
        If the requested (session_date, book) combination is not found in the
        report.
    FileNotFoundError
        If ``report_jsonl_path`` does not exist.
    """

    # --- Step 1: verify audit chain ------------------------------------------
    if not audit.verify():
        # Fail closed: broken chain -> no write, return None
        return None

    # --- Step 2: read audit records, enforce run_id identity -----------------
    records = audit.read_all()
    run_ids = {r["run_id"] for r in records if "run_id" in r}
    if len(run_ids) > 1:
        raise IdentityMismatch(
            f"Audit log contains {len(run_ids)} distinct run_ids: {sorted(run_ids)}. "
            "Cannot ingest a session with mixed-identity audit records."
        )
    run_id = next(iter(run_ids)) if run_ids else audit.run_id

    # --- Step 3: parse report JSONL tolerantly --------------------------------
    if not os.path.exists(report_jsonl_path):
        raise FileNotFoundError(f"Report file not found: {report_jsonl_path}")

    coverage: Optional[CoverageManifest] = None
    book_reports: list[BookReport] = []

    with open(report_jsonl_path, "r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]

    if not lines:
        raise IngestRejected("Report file is empty")

    line_iter = iter(lines)
    first_line = next(line_iter)
    first_dict = json.loads(first_line)

    # Coverage-tolerant parsing: line 0 is coverage IFF it has "record"=="coverage"
    if first_dict.get("record") == "coverage":
        coverage = CoverageManifest.from_manifest_dict(first_dict)
        # Remaining lines are BookReport dicts
        remaining_lines = list(line_iter)
    else:
        # No coverage line; first_dict is a BookReport
        remaining_lines = [first_line] + list(line_iter)

    for raw_line in remaining_lines:
        d = json.loads(raw_line)
        # Never mis-read a coverage line as a BookReport
        if d.get("record") == "coverage":
            continue
        book_reports.append(_dict_to_book_report(d))

    # --- Step 4: find the matching (session_date, book) BookReport -----------
    matched: Optional[BookReport] = None
    for br in book_reports:
        if br.session_date == session_date and br.book == book:
            matched = br
            break

    if matched is None:
        raise IngestRejected(
            f"No BookReport found for (session_date={session_date!r}, book={book!r}) "
            f"in {report_jsonl_path}. Available: "
            f"{[(r.session_date, r.book) for r in book_reports]}"
        )

    # --- Step 4.5: AUDIT IDENTITY must match the session identity ------------
    # (Reviewer finding 1.) The report can legitimately contain a matching
    # (session_date, book), but the AUDIT evidence must belong to the SAME run.
    # The spine names every run_id f"{session_date}-{spec.name}" (execution/session.py),
    # so the audit's single run_id must equal f"{session_date}-{book}" (or an explicit
    # expected_run_id). Otherwise we'd splice another run's audit rows (symbol_outcomes)
    # onto this book's report — fail closed.
    expected = expected_run_id if expected_run_id is not None else f"{session_date}-{book}"
    if run_id != expected:
        raise IdentityMismatch(
            f"Audit run_id {run_id!r} does not match the session identity {expected!r} "
            f"(session_date={session_date!r}, book={book!r}). Refusing to ingest audit "
            f"evidence from a different run/book into this episode."
        )

    # --- Step 5: build symbol_outcomes from audit rows -----------------------
    symbol_outcomes = _build_symbol_outcomes(records, max_symbols=max_symbols)

    # --- Step 6: build episode dict with source citation --------------------
    episode: dict = {
        # Identity
        "session_date": matched.session_date,
        "book": matched.book,
        # BookReport outcome fields
        "net_pnl": matched.net_pnl,
        "filled": matched.filled,
        "blocked": matched.blocked,
        "block_reasons": matched.block_reasons,
        "audit_coverage_pct": matched.audit_coverage_pct,
        "audit_chain_ok": matched.audit_chain_ok,
        "daily_loss_breached": matched.daily_loss_breached,
        "stale_data_trades": matched.stale_data_trades,
        "kill_switch_drills": matched.kill_switch_drills,
        "cost_drag_pct": matched.cost_drag_pct,
        # Derived
        "symbol_outcomes": symbol_outcomes,
        # Provenance — no source -> no entry (this invariant is enforced below)
        "source": {
            "run_id": run_id,
            "report_path": os.path.abspath(report_jsonl_path),
        },
    }

    # Fail closed: if source is somehow missing run_id, refuse to write
    if not episode["source"].get("run_id"):
        raise IngestRejected("Cannot ingest: source.run_id is absent")

    # --- Step 7: write exactly one episode to the tier store ------------------
    tier_store.append(episode)
    return episode


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dict_to_book_report(d: dict) -> BookReport:
    """Reconstruct a BookReport from a to_json_line() dict."""
    return BookReport(
        book=d["book"],
        session_date=d["session_date"],
        starting_capital=float(d["starting_capital"]),
        equity=float(d["equity"]),
        realized_pnl=float(d["realized_pnl"]),
        unrealized_pnl=float(d["unrealized_pnl"]),
        net_pnl=float(d["net_pnl"]),
        decisions=int(d["decisions"]),
        filled=int(d["filled"]),
        partial=int(d["partial"]),
        blocked=int(d["blocked"]),
        rejected=int(d["rejected"]),
        intended_exit_unfilled=int(d["intended_exit_unfilled"]),
        no_trade=int(d["no_trade"]),
        block_reasons=dict(d.get("block_reasons") or {}),
        turnover=float(d["turnover"]),
        total_charges=float(d["total_charges"]),
        cost_drag_pct=float(d["cost_drag_pct"]),
        avg_cost_per_trade=float(d["avg_cost_per_trade"]),
        deployed_capital=float(d["deployed_capital"]),
        return_on_deployed_pct=float(d["return_on_deployed_pct"]),
        audit_coverage_pct=float(d["audit_coverage_pct"]),
        audit_chain_ok=bool(d["audit_chain_ok"]),
        daily_loss_breached=bool(d["daily_loss_breached"]),
        stale_data_trades=int(d["stale_data_trades"]),
        kill_switch_drills=int(d["kill_switch_drills"]),
    )


def _build_symbol_outcomes(records: list[dict], *, max_symbols: int) -> dict:
    """Aggregate per-symbol outcomes from audit records, bounded to max_symbols.

    For each symbol we collect:
    - filled: True if any "fill" record exists
    - blocked: True if any "block" record exists
    - block_reason: first block reason found, or None

    The result is bounded to max_symbols entries (first-seen order).
    """
    outcomes: dict[str, dict] = {}
    for rec in records:
        symbol = rec.get("symbol")
        if not symbol:
            continue
        if symbol not in outcomes:
            if len(outcomes) >= max_symbols:
                continue  # Cap at max_symbols
            outcomes[symbol] = {"filled": False, "blocked": False, "block_reason": None}
        stage = rec.get("stage", "")
        if stage == "fill":
            outcomes[symbol]["filled"] = True
        elif stage == "block":
            # The spine router (execution/router.py) always writes stage='block'.
            # It never writes 'blocked'.  Matching only 'block' prevents stale
            # aliases from silently accepting data from an unexpected source.
            outcomes[symbol]["blocked"] = True
            payload = rec.get("payload") or {}
            if outcomes[symbol]["block_reason"] is None:
                outcomes[symbol]["block_reason"] = payload.get("gate") or payload.get("reason")
    return outcomes
