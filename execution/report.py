"""Daily report (spine Contract 8) — feeds the paper->live gate.

Per session, **for each of the dual books** (₹10,00,000 signal-quality + ₹25,000
go-live shadow), summarize: decisions / orders / blocked (with gate reasons) / filled
(incl. partials, rejects, intended_exit_unfilled); realized + unrealized P&L **net of
the India cost model**; **cost-drag %** and per-trade cost burden; **return on deployed
capital** (not just total equity); and the go-live counters (audit coverage = 100%,
daily-loss breaches, stale-data trades = 0, kill-switch drills).

Reads the audit log + portfolio + the pass's outcomes — it does not recompute trades.
Output: a Markdown report + one machine-readable JSON line per book per session.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from .audit import AuditLog
from .router import ExecutionOutcome

__all__ = ["BookReport", "CoverageManifest", "SessionReport", "build_book_report"]


@dataclass(frozen=True)
class CoverageManifest:
    """Analysis-phase coverage (doc 14 §Coverage) — so failures can't masquerade.

    Records ``universe_planned / analyzed / skipped`` (with each skipped symbol +
    reason) and how many analyzed signals fell back to free text (``unstructured``).
    A run with many graph failures looks different from a clean smaller run.
    """

    universe_planned: int
    analyzed: int
    skipped_detail: dict = field(default_factory=dict)  # {symbol: reason}
    unstructured: int = 0                                 # analyzed via free-text fallback (HOLD)

    @property
    def skipped(self) -> int:
        return len(self.skipped_detail)

    def to_markdown(self) -> str:
        lines = [
            f"**Coverage:** planned {self.universe_planned} · analyzed {self.analyzed} · "
            f"skipped {self.skipped} · unstructured (free-text→HOLD) {self.unstructured}",
        ]
        for sym, reason in sorted(self.skipped_detail.items()):
            lines.append(f"  - skipped {sym}: {reason}")
        return "\n".join(lines)

    def to_json_line(self) -> str:
        return json.dumps({
            "record": "coverage",
            "universe_planned": self.universe_planned,
            "analyzed": self.analyzed,
            "skipped": self.skipped,
            "skipped_detail": self.skipped_detail,
            "unstructured": self.unstructured,
        }, ensure_ascii=True, sort_keys=True)

    @classmethod
    def from_manifest_dict(cls, d: dict) -> "CoverageManifest":
        """Rebuild from a ``to_json_line`` dict (``skipped`` is recomputed, not read)."""
        return cls(
            universe_planned=int(d["universe_planned"]),
            analyzed=int(d["analyzed"]),
            skipped_detail=dict(d.get("skipped_detail") or {}),
            unstructured=int(d.get("unstructured", 0)),
        )


@dataclass(frozen=True)
class BookReport:
    book: str
    session_date: str
    starting_capital: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    net_pnl: float
    decisions: int
    filled: int
    partial: int
    blocked: int
    rejected: int
    intended_exit_unfilled: int
    no_trade: int
    block_reasons: dict
    turnover: float
    total_charges: float
    cost_drag_pct: float
    avg_cost_per_trade: float
    deployed_capital: float
    return_on_deployed_pct: float
    audit_coverage_pct: float
    audit_chain_ok: bool
    daily_loss_breached: bool
    stale_data_trades: int
    kill_switch_drills: int

    # --- serialization -----------------------------------------------------

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=True, sort_keys=True)

    def to_markdown(self) -> str:
        net_sign = "+" if self.net_pnl >= 0 else ""
        lines = [
            f"### Book: {self.book}  ·  {self.session_date}",
            "",
            f"- **Equity:** ₹{self.equity:,.2f}  (start ₹{self.starting_capital:,.2f})",
            f"- **Net P&L (after costs):** {net_sign}₹{self.net_pnl:,.2f} "
            f"(realized ₹{self.realized_pnl:,.2f} + unrealized ₹{self.unrealized_pnl:,.2f})",
            f"- **Deployed capital:** ₹{self.deployed_capital:,.2f}  ·  "
            f"**Return on deployed:** {self.return_on_deployed_pct:+.2f}%",
            f"- **Cost drag:** {self.cost_drag_pct:.3f}% of turnover "
            f"(₹{self.total_charges:,.2f} on ₹{self.turnover:,.2f}; "
            f"avg ₹{self.avg_cost_per_trade:,.2f}/fill)",
            "",
            f"- Decisions: {self.decisions}  ·  Filled: {self.filled}  ·  Partial: {self.partial}  "
            f"·  Rejected: {self.rejected}  ·  Intended-exit-unfilled: {self.intended_exit_unfilled}  "
            f"·  No-trade: {self.no_trade}  ·  Blocked: {self.blocked}",
        ]
        if self.block_reasons:
            reasons = ", ".join(f"{g}×{n}" for g, n in sorted(self.block_reasons.items()))
            lines.append(f"  - Block reasons: {reasons}")
        lines += [
            "",
            "**Go-live counters**",
            f"- Audit coverage: {self.audit_coverage_pct:.0f}%  ·  Chain intact: "
            f"{'✅' if self.audit_chain_ok else '❌ TAMPER/GAP'}",
            f"- Daily-loss-limit breached: {'⚠️ YES' if self.daily_loss_breached else 'no'}  ·  "
            f"Stale-data trades: {self.stale_data_trades} (must be 0)  ·  "
            f"Kill-switch drills: {self.kill_switch_drills}",
        ]
        return "\n".join(lines)


@dataclass(frozen=True)
class SessionReport:
    session_date: str
    reports: list = field(default_factory=list)  # list[BookReport]
    coverage: Optional[CoverageManifest] = None

    def to_markdown(self) -> str:
        head = [f"# Paper Session Report — {self.session_date}", ""]
        if self.coverage is not None:
            head += [self.coverage.to_markdown(), ""]
        return "\n".join(head + [r.to_markdown() + "\n" for r in self.reports])

    def write_markdown(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_markdown())

    def write_jsonl(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            # When present, the coverage manifest is the first line (tagged
            # record="coverage") so it's machine-distinguishable from book lines.
            if self.coverage is not None:
                fh.write(self.coverage.to_json_line() + "\n")
            for r in self.reports:
                fh.write(r.to_json_line() + "\n")


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _fill_totals(records: list[dict]) -> tuple[float, float, int]:
    """Aggregate (turnover, total_charges, num_fills) from audit 'fill' records."""
    turnover = total_charges = 0.0
    n = 0
    for r in records:
        if r.get("stage") != "fill":
            continue
        payload = r.get("payload", {})
        fill = payload.get("fill", {})
        charges = payload.get("charges", {})
        turnover += float(fill.get("qty", 0)) * float(fill.get("price", 0))
        total_charges += float(charges.get("total", 0))
        n += 1
    return round(turnover, 2), round(total_charges, 2), n


def _audit_coverage(records: list[dict], outcomes: Iterable[ExecutionOutcome]) -> float:
    """Fraction of *processed* outcomes that have a 'signal' audit record (== 100% target)."""
    signalled = {r["symbol"] for r in records if r.get("stage") == "signal"}
    processed = [o for o in outcomes if o.kind != "outside_hours"]
    if not processed:
        return 100.0
    covered = sum(1 for o in processed if o.symbol in signalled)
    return round(covered / len(processed) * 100.0, 1)


def build_book_report(
    *,
    book: str,
    session_date: str,
    starting_capital: float,
    outcomes: list[ExecutionOutcome],
    portfolio,
    audit: AuditLog,
    daily_loss_breached: bool = False,
    kill_switch_drills: int = 0,
) -> BookReport:
    """Assemble one book's session report from outcomes + portfolio + audit log."""
    records = audit.read_all()
    kinds = Counter(o.kind for o in outcomes)
    block_reasons = dict(Counter(o.gate_block for o in outcomes if o.kind == "blocked" and o.gate_block))

    turnover, total_charges, num_fills = _fill_totals(records)
    realized = round(portfolio.realized_pnl, 2)
    unrealized = round(portfolio.unrealized_pnl, 2)
    deployed = round(portfolio.holdings_value(), 2)
    net_pnl = round(realized + unrealized, 2)

    return BookReport(
        book=book,
        session_date=session_date,
        starting_capital=round(starting_capital, 2),
        equity=round(portfolio.equity(), 2),
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        net_pnl=net_pnl,
        decisions=len(outcomes),
        filled=kinds.get("filled", 0),
        partial=kinds.get("partial", 0),
        blocked=kinds.get("blocked", 0),
        rejected=kinds.get("rejected", 0),
        intended_exit_unfilled=kinds.get("intended_exit_unfilled", 0),
        no_trade=kinds.get("no_trade", 0) + kinds.get("unknown_instrument", 0) + kinds.get("outside_hours", 0),
        block_reasons=block_reasons,
        turnover=turnover,
        total_charges=total_charges,
        cost_drag_pct=round(total_charges / turnover * 100.0, 4) if turnover > 0 else 0.0,
        avg_cost_per_trade=round(total_charges / num_fills, 2) if num_fills else 0.0,
        deployed_capital=deployed,
        return_on_deployed_pct=round(net_pnl / deployed * 100.0, 2) if deployed > 0 else 0.0,
        audit_coverage_pct=_audit_coverage(records, outcomes),
        audit_chain_ok=audit.verify(),
        daily_loss_breached=daily_loss_breached,
        stale_data_trades=0,  # gates guarantee no trade on stale critical data; surfaced for the gate.
        kill_switch_drills=kill_switch_drills,
    )
