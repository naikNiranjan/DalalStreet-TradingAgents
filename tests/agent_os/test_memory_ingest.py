"""Tests for agent_os/memory/tiers.py and agent_os/memory/ingest.py.

TDD: This file is written FIRST. The tests define the required behaviour.
All tests should fail until the implementation modules are written.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path so execution.* and agent_os.* are importable
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from execution.audit import AuditLog, GENESIS_HASH
from execution.report import BookReport, CoverageManifest, SessionReport
from tradingagents.agents.utils.rating import RATINGS_5_TIER, parse_rating

from agent_os.memory.tiers import TIERS, TierStore
from agent_os.memory.ingest import ingest_session, IdentityMismatch, IngestRejected
from agent_os.memory.tiers import _MIN_ENTRY_CHARS


# ===========================================================================
# Helpers / factories
# ===========================================================================

def _make_audit(tmp_path: pathlib.Path, run_id: str = "2026-01-15-signal", n_symbols: int = 3) -> AuditLog:
    """Create a valid AuditLog with signal + fill records for n_symbols."""
    path = str(tmp_path / f"audit_{run_id}.jsonl")
    log = AuditLog(path, run_id=run_id)
    symbols = [f"SYM{i:03d}" for i in range(n_symbols)]
    for sym in symbols:
        log.append("signal", sym, {"rating": "Buy"})
        log.append("fill", sym, {"fill": {"qty": 10, "price": 100.0}, "charges": {"total": 5.0}})
    return log


def _make_book_report(
    book: str = "signal",
    session_date: str = "2026-01-15",
    net_pnl: float = 1500.0,
    filled: int = 3,
    blocked: int = 1,
    block_reasons: dict = None,
) -> BookReport:
    """Create a minimal valid BookReport."""
    return BookReport(
        book=book,
        session_date=session_date,
        starting_capital=1000000.0,
        equity=1001500.0,
        realized_pnl=1500.0,
        unrealized_pnl=0.0,
        net_pnl=net_pnl,
        decisions=5,
        filled=filled,
        partial=0,
        blocked=blocked,
        rejected=0,
        intended_exit_unfilled=0,
        no_trade=1,
        block_reasons=block_reasons or {"daily_loss": 1},
        turnover=30000.0,
        total_charges=150.0,
        cost_drag_pct=0.5,
        avg_cost_per_trade=50.0,
        deployed_capital=30000.0,
        return_on_deployed_pct=5.0,
        audit_coverage_pct=100.0,
        audit_chain_ok=True,
        daily_loss_breached=False,
        stale_data_trades=0,
        kill_switch_drills=0,
    )


def _write_session_jsonl(
    path: pathlib.Path,
    book_reports: list,
    coverage: CoverageManifest | None = None,
) -> None:
    """Write a SessionReport-style JSONL at the given path."""
    sr = SessionReport(
        session_date=book_reports[0].session_date if book_reports else "2026-01-15",
        reports=book_reports,
        coverage=coverage,
    )
    sr.write_jsonl(str(path))


# ===========================================================================
# 1. TIERS constant structure
# ===========================================================================

class TestTiersConstant:
    def test_tiers_has_required_keys(self):
        """TIERS must have short_term, episodic, long_term, regime, strategy."""
        required = {"short_term", "episodic", "long_term", "regime", "strategy"}
        assert required.issubset(set(TIERS.keys())), \
            f"Missing keys: {required - set(TIERS.keys())}"

    def test_episodic_tier_populated(self):
        """Episodic is the only tier populated in this slice (others may be None/empty)."""
        assert TIERS["episodic"] is not None
        # episodic should have at least max_entries configured
        assert TIERS["episodic"].get("max_entries") is not None


# ===========================================================================
# 2. TierStore
# ===========================================================================

class TestTierStore:
    def test_tier_store_creates_jsonl_file(self, tmp_path):
        """TierStore writes to a JSONL sidecar file at <base_dir>/<tier>.jsonl."""
        store = TierStore(str(tmp_path), "episodic", max_entries=100, max_chars_per_entry=2000)
        store.append({"key": "value"})
        expected = tmp_path / "episodic.jsonl"
        assert expected.exists(), "TierStore must create <tier>.jsonl in base_dir"

    def test_append_and_read_all(self, tmp_path):
        """append() persists entries; read_all() returns them as dicts."""
        store = TierStore(str(tmp_path), "episodic", max_entries=100, max_chars_per_entry=2000)
        store.append({"session_date": "2026-01-15", "book": "signal", "net_pnl": 1500.0})
        store.append({"session_date": "2026-01-16", "book": "signal", "net_pnl": -200.0})
        entries = store.read_all()
        assert len(entries) == 2
        assert entries[0]["session_date"] == "2026-01-15"
        assert entries[1]["net_pnl"] == -200.0

    def test_max_entries_trims_oldest(self, tmp_path):
        """When max_entries is exceeded, the OLDEST entries are trimmed."""
        store = TierStore(str(tmp_path), "episodic", max_entries=3, max_chars_per_entry=2000)
        for i in range(5):
            store.append({"n": i})
        entries = store.read_all()
        assert len(entries) == 3, f"Expected 3 entries (trimmed), got {len(entries)}"
        # Oldest (n=0, n=1) should be gone; newest (n=2, n=3, n=4) remain
        values = [e["n"] for e in entries]
        assert values == [2, 3, 4], f"Expected newest 3 entries, got {values}"

    def test_max_chars_per_entry_caps_entry(self, tmp_path):
        """Entries larger than max_chars_per_entry are truncated/capped."""
        limit = 64  # _MIN_ENTRY_CHARS — the safe minimum for convergence
        store = TierStore(str(tmp_path), "episodic", max_entries=100, max_chars_per_entry=limit)
        big_entry = {"data": "x" * 10000}
        store.append(big_entry)
        entries = store.read_all()
        assert len(entries) == 1
        # Serialized form must fit within the limit
        serialized = json.dumps(entries[0], ensure_ascii=True)
        assert len(serialized) <= limit, \
            f"Stored entry serialized to {len(serialized)} chars, limit is {limit}"

    def test_not_trading_memory_md(self, tmp_path):
        """TierStore MUST NOT write to trading_memory.md."""
        store = TierStore(str(tmp_path), "episodic", max_entries=100, max_chars_per_entry=2000)
        store.append({"key": "val"})
        md_file = tmp_path / "trading_memory.md"
        assert not md_file.exists(), "TierStore must not touch trading_memory.md"

    def test_sidecar_file_name(self, tmp_path):
        """The JSONL file name matches the tier name exactly."""
        store = TierStore(str(tmp_path), "episodic", max_entries=10, max_chars_per_entry=500)
        store.append({"x": 1})
        assert (tmp_path / "episodic.jsonl").exists()
        # No other .jsonl files should be created
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1

    def test_ratings_5_tier_reused(self, tmp_path):
        """Any rating label stored in a tier entry must come from RATINGS_5_TIER."""
        store = TierStore(str(tmp_path), "episodic", max_entries=10, max_chars_per_entry=2000)
        # Store an entry that contains a rating
        rating = parse_rating("I recommend a Buy on this stock")
        assert rating in RATINGS_5_TIER, f"parse_rating returned {rating!r} which is not in RATINGS_5_TIER"
        store.append({"rating": rating})
        entries = store.read_all()
        assert entries[0]["rating"] in RATINGS_5_TIER


# ===========================================================================
# 3. ingest_session — happy path
# ===========================================================================

class TestIngestSessionHappyPath:
    def test_ingestion_writes_one_episode(self, tmp_path):
        """Verified audit + matching report -> exactly one episode written."""
        audit = _make_audit(tmp_path, n_symbols=3)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)

        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )

        assert episode is not None, "ingest_session must return the episode dict"
        entries = store.read_all()
        assert len(entries) == 1, f"Expected 1 episode, got {len(entries)}"

    def test_episode_has_book_report_outcome_fields(self, tmp_path):
        """Episode must include the BookReport outcome fields."""
        audit = _make_audit(tmp_path)
        br = _make_book_report(
            book="signal",
            session_date="2026-01-15",
            net_pnl=2500.0,
            filled=3,
            blocked=1,
        )
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])
        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)

        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )

        assert episode["net_pnl"] == 2500.0
        assert episode["filled"] == 3
        assert episode["blocked"] == 1
        # All required outcome fields
        for field in ("net_pnl", "filled", "blocked", "block_reasons",
                      "audit_coverage_pct", "audit_chain_ok",
                      "daily_loss_breached", "stale_data_trades",
                      "kill_switch_drills", "cost_drag_pct"):
            assert field in episode, f"Missing field: {field}"

    def test_episode_has_source_citation(self, tmp_path):
        """Episode must include source = {run_id, report_path}."""
        audit = _make_audit(tmp_path)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])
        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)

        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )

        assert "source" in episode, "Episode must have 'source' field"
        assert episode["source"]["run_id"] == "2026-01-15-signal"
        assert "report_path" in episode["source"]

    def test_episode_has_symbol_outcomes(self, tmp_path):
        """Episode must contain symbol_outcomes derived from audit rows."""
        audit = _make_audit(tmp_path, n_symbols=3)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])
        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)

        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )

        assert "symbol_outcomes" in episode
        assert isinstance(episode["symbol_outcomes"], dict)
        assert len(episode["symbol_outcomes"]) == 3


# ===========================================================================
# 4. ingest_session — fail-closed conditions
# ===========================================================================

class TestIngestSessionFailClosed:
    def test_broken_chain_no_write(self, tmp_path):
        """When audit.verify() == False, ingest_session must return None (not raise) and NOT write."""
        audit = _make_audit(tmp_path)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])

        # Tamper the audit file to break the chain
        audit_path = pathlib.Path(audit.path)
        lines = audit_path.read_text().splitlines()
        # Modify the hash of the first line to break the chain
        first = json.loads(lines[0])
        first["hash"] = "deadbeef" * 8  # 64-char wrong hash
        lines[0] = json.dumps(first)
        audit_path.write_text("\n".join(lines) + "\n")

        assert not audit.verify(), "Audit should not verify after tampering"

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)

        # Spec: verify()==False -> RETURN None, do NOT raise.
        result = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )
        assert result is None, \
            f"ingest_session must return None on broken chain, got {result!r}"
        assert len(store.read_all()) == 0, \
            "No episode must be written when audit chain is broken"

    def test_mixed_run_ids_rejected(self, tmp_path):
        """Audit rows with more than one distinct run_id -> raise IdentityMismatch."""
        # Create audit log with run-001
        path = str(tmp_path / "audit_mixed.jsonl")
        log1 = AuditLog(path, run_id="run-001")
        log1.append("signal", "SYMA", {"rating": "Buy"})

        # Manually append a record with a different run_id (breaking identity invariant)
        # We need to build the chain-compatible record but with wrong run_id
        # Simplest approach: re-open as run-002 (which resumes the chain)
        log2 = AuditLog(path, run_id="run-002")
        log2.append("signal", "SYMB", {"rating": "Sell"})

        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)

        with pytest.raises(IdentityMismatch):
            ingest_session(
                audit=log2,
                report_jsonl_path=str(report_path),
                session_date="2026-01-15",
                book="signal",
                tier_store=store,
            )

        assert len(store.read_all()) == 0, "No episode on identity mismatch"

    def test_date_book_mismatch_rejected(self, tmp_path):
        """Requesting a (session_date, book) not in the report -> raises IngestRejected."""
        audit = _make_audit(tmp_path)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)

        with pytest.raises(IngestRejected):
            ingest_session(
                audit=audit,
                report_jsonl_path=str(report_path),
                session_date="2026-01-15",
                book="golive",  # NOT in the report
                tier_store=store,
            )
        assert len(store.read_all()) == 0, "No episode on book mismatch"

    def test_wrong_date_rejected(self, tmp_path):
        """Requesting a date not in the report -> raises IngestRejected."""
        audit = _make_audit(tmp_path)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)

        with pytest.raises(IngestRejected):
            ingest_session(
                audit=audit,
                report_jsonl_path=str(report_path),
                session_date="2026-12-31",  # Wrong date
                book="signal",
                tier_store=store,
            )
        assert len(store.read_all()) == 0, "No episode on date mismatch"


# ===========================================================================
# 5. Coverage-tolerant parsing
# ===========================================================================

class TestCoverageTolerancy:
    def test_with_coverage_line_first(self, tmp_path):
        """A report with coverage line first is parsed correctly."""
        audit = _make_audit(tmp_path)
        cm = CoverageManifest(
            universe_planned=5,
            analyzed=4,
            skipped_detail={"BADSTOCK": "no data"},
        )
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br], coverage=cm)

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)
        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )
        assert episode is not None
        assert len(store.read_all()) == 1

    def test_no_coverage_line_legacy_report(self, tmp_path):
        """A report with no coverage line (legacy) is parsed safely; coverage is ABSENT."""
        audit = _make_audit(tmp_path)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        # Write WITHOUT coverage
        _write_session_jsonl(report_path, [br], coverage=None)

        # Verify the first line is NOT a coverage record
        first_line = json.loads(report_path.read_text().splitlines()[0])
        assert "record" not in first_line or first_line.get("record") != "coverage", \
            "First line should be a BookReport, not a coverage record"

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)
        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )
        assert episode is not None
        assert len(store.read_all()) == 1

    def test_book_line_never_mis_read_as_coverage(self, tmp_path):
        """A BookReport line must never be mis-read as a coverage record."""
        audit = _make_audit(tmp_path)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br], coverage=None)

        # Verify the BookReport JSON doesn't have "record" == "coverage"
        first_line = json.loads(report_path.read_text().splitlines()[0])
        assert first_line.get("record") != "coverage"
        assert "book" in first_line  # It's a BookReport

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4000)
        # Should succeed (book line parsed as BookReport, not coverage)
        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )
        assert episode is not None


# ===========================================================================
# 6. symbol_outcomes bounded by max_symbols
# ===========================================================================

class TestSymbolOutcomesBounded:
    def test_symbol_outcomes_bounded_to_max_symbols(self, tmp_path):
        """symbol_outcomes is bounded to max_symbols (default 50)."""
        n = 60  # More than the default max_symbols=50
        audit = _make_audit(tmp_path, n_symbols=n)
        br = _make_book_report(book="signal", session_date="2026-01-15", filled=n)
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=10000)
        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
            max_symbols=50,
        )
        assert episode is not None
        assert len(episode["symbol_outcomes"]) <= 50, \
            f"symbol_outcomes must be bounded to 50, got {len(episode['symbol_outcomes'])}"

    def test_custom_max_symbols(self, tmp_path):
        """A custom max_symbols value is respected."""
        n = 10
        audit = _make_audit(tmp_path, n_symbols=n)
        br = _make_book_report(book="signal", session_date="2026-01-15", filled=n)
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])

        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=10000)
        episode = ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
            max_symbols=3,
        )
        assert episode is not None
        assert len(episode["symbol_outcomes"]) <= 3


# ===========================================================================
# 7. Char-cap enforcement in TierStore
# ===========================================================================

class TestCharCapEnforcement:
    def test_oversize_entry_is_capped(self, tmp_path):
        """An entry exceeding max_chars_per_entry must be capped at the limit."""
        limit = 100
        store = TierStore(str(tmp_path), "episodic", max_entries=10, max_chars_per_entry=limit)
        big = {"description": "A" * 5000, "value": 42}
        store.append(big)
        entries = store.read_all()
        assert len(entries) == 1
        stored_serialized = json.dumps(entries[0], ensure_ascii=True)
        assert len(stored_serialized) <= limit, \
            f"Stored entry length {len(stored_serialized)} exceeds limit {limit}"

    def test_small_entry_not_capped(self, tmp_path):
        """Entries within the limit are stored intact."""
        limit = 500
        store = TierStore(str(tmp_path), "episodic", max_entries=10, max_chars_per_entry=limit)
        small = {"key": "value", "num": 42}
        store.append(small)
        entries = store.read_all()
        assert entries[0]["key"] == "value"
        assert entries[0]["num"] == 42


# ===========================================================================
# 8. RATINGS_5_TIER reuse — no parallel scale invented
# ===========================================================================

class TestRatings5TierReuse:
    def test_ratings_5_tier_is_canonical_source(self):
        """RATINGS_5_TIER from rating.py is the canonical 5-tier vocabulary."""
        assert set(RATINGS_5_TIER) == {"Buy", "Overweight", "Hold", "Underweight", "Sell"}

    def test_parse_rating_returns_from_ratings_5_tier(self):
        """parse_rating always returns a value in RATINGS_5_TIER."""
        texts = [
            "Rating: Buy", "Rating: Sell", "Rating: Hold",
            "Overweight on this stock", "Underweight", "",
            "unknown garbage",
        ]
        for text in texts:
            result = parse_rating(text)
            assert result in RATINGS_5_TIER, \
                f"parse_rating({text!r}) returned {result!r} not in RATINGS_5_TIER"

    def test_tiers_do_not_define_parallel_rating_scale(self):
        """agent_os/memory/tiers.py must not define its own rating vocabulary."""
        import agent_os.memory.tiers as tiers_module
        # Check that there's no RATINGS-like constant that isn't the canonical one
        for attr in dir(tiers_module):
            val = getattr(tiers_module, attr)
            if isinstance(val, (tuple, list, set, frozenset)) and attr.upper() == attr:
                # It's an ALL_CAPS constant — if it looks like a rating list, it must match
                if len(val) > 0 and all(isinstance(v, str) for v in val):
                    candidate_set = set(val)
                    canonical_set = set(RATINGS_5_TIER)
                    if candidate_set.issubset({"Buy", "Overweight", "Hold", "Underweight", "Sell",
                                               "buy", "sell", "hold"}):
                        assert candidate_set <= canonical_set or candidate_set == canonical_set, \
                            f"Module defines a parallel rating scale: {candidate_set}"


# ===========================================================================
# 9. Base-repo grammar untouched (sidecar invariant)
# ===========================================================================

class TestSidecarInvariant:
    def test_tier_writes_sidecar_not_trading_memory_md(self, tmp_path):
        """TierStore writes to <tier>.jsonl, NOT trading_memory.md."""
        store = TierStore(str(tmp_path), "episodic", max_entries=10, max_chars_per_entry=2000)
        store.append({"note": "test"})
        assert not (tmp_path / "trading_memory.md").exists()
        assert (tmp_path / "episodic.jsonl").exists()

    def test_ingest_does_not_touch_trading_memory_md(self, tmp_path):
        """ingest_session must not create or modify trading_memory.md."""
        audit = _make_audit(tmp_path)
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])

        mem_dir = tmp_path / "memory"
        store = TierStore(str(mem_dir), "episodic", max_entries=100, max_chars_per_entry=4000)

        ingest_session(
            audit=audit,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )

        # trading_memory.md must not exist anywhere under tmp_path
        md_files = list(tmp_path.rglob("trading_memory.md"))
        assert len(md_files) == 0, \
            f"trading_memory.md was created at: {md_files}"

    def test_trading_memory_log_not_modified(self, tmp_path):
        """Importing tiers/ingest does not modify the TradingMemoryLog class."""
        from tradingagents.agents.utils.memory import TradingMemoryLog
        # Create a TradingMemoryLog and verify it still works after importing agent_os
        import agent_os.memory.tiers
        import agent_os.memory.ingest
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "trading_memory.md")})
        log.store_decision("RELIANCE", "2026-01-15", "Rating: Buy. Long-term outlook positive.")
        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["ticker"] == "RELIANCE"
        # File exists and is the .md grammar
        assert (tmp_path / "trading_memory.md").exists()
        # No .jsonl files created by TradingMemoryLog
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 0


# ===========================================================================
# 10. No arbitrary free-text write path (no LLM-authored memory)
# ===========================================================================

class TestNoArbitraryWritePath:
    def test_ingest_module_has_no_free_text_write(self):
        """ingest module must not expose a function that writes arbitrary model text."""
        import agent_os.memory.ingest as ingest_module
        public_funcs = [
            name for name in dir(ingest_module)
            if not name.startswith("_") and callable(getattr(ingest_module, name))
        ]
        # Suspicious names that would suggest an LLM write path
        suspicious = {
            "write_text", "write_memory", "store_text", "save_text",
            "add_entry", "save_freetext", "write_freetext",
        }
        for name in public_funcs:
            assert name.lower() not in suspicious, \
                f"ingest module has suspicious function '{name}' suggesting LLM write path"

    def test_only_ingest_session_is_public_write_entrypoint(self):
        """ingest_session is the ONLY public write entrypoint."""
        import agent_os.memory.ingest as ingest_module
        write_functions = [
            name for name in dir(ingest_module)
            if (not name.startswith("_")
                and callable(getattr(ingest_module, name))
                and "ingest" in name.lower())
        ]
        # Should contain ingest_session and nothing else that "writes" by name
        assert "ingest_session" in write_functions


# ===========================================================================
# 11. L2-F4 — TierStore min-chars validation (infinite-loop guard)
# ===========================================================================

class TestTierStoreMinCharsValidation:
    def test_below_min_entry_chars_raises_value_error(self, tmp_path):
        """TierStore(..., max_chars_per_entry=37) must raise ValueError (not hang)."""
        with pytest.raises(ValueError, match="min"):
            TierStore(str(tmp_path), "episodic", max_entries=10, max_chars_per_entry=37)

    def test_exactly_min_entry_chars_accepted(self, tmp_path):
        """TierStore(..., max_chars_per_entry=_MIN_ENTRY_CHARS) is valid."""
        store = TierStore(
            str(tmp_path), "episodic",
            max_entries=10,
            max_chars_per_entry=_MIN_ENTRY_CHARS,
        )
        assert store is not None

    def test_production_tiers_value_is_valid(self):
        """The production TIERS config (4096) is >= _MIN_ENTRY_CHARS."""
        assert TIERS["episodic"]["max_chars_per_entry"] >= _MIN_ENTRY_CHARS, (
            f"Production episodic max_chars_per_entry "
            f"({TIERS['episodic']['max_chars_per_entry']}) "
            f"is below _MIN_ENTRY_CHARS ({_MIN_ENTRY_CHARS})"
        )

    def test_max_chars_per_entry_one_below_min_raises(self, tmp_path):
        """max_chars_per_entry = _MIN_ENTRY_CHARS - 1 must raise ValueError."""
        with pytest.raises(ValueError):
            TierStore(
                str(tmp_path), "episodic",
                max_entries=10,
                max_chars_per_entry=_MIN_ENTRY_CHARS - 1,
            )


# ===========================================================================
# 12. L2-F5 — stage == 'block' only; contract test against router source
# ===========================================================================

class TestBlockStageContractWithRouter:
    def test_router_writes_block_not_blocked(self):
        """Contract: execution/router.py writes stage='block', never 'blocked'.

        If the router is ever renamed to write 'blocked', this test catches it
        before the misalignment silently discards block records in ingest.
        """
        import re
        router_path = _REPO_ROOT / "execution" / "router.py"
        source = router_path.read_text(encoding="utf-8")

        # Must contain at least one audit.append("block", ...) call
        assert re.search(r'audit\.append\s*\(\s*["\']block["\']', source), (
            "execution/router.py must contain audit.append('block', ...) calls"
        )

        # Must NOT contain audit.append("blocked", ...) in any form
        assert not re.search(r'audit\.append\s*\(\s*["\']blocked["\']', source), (
            "execution/router.py must NOT write stage='blocked'; "
            "ingest.py matches only stage='block'"
        )

    def test_build_symbol_outcomes_ignores_blocked_stage(self, tmp_path):
        """_build_symbol_outcomes must NOT treat stage='blocked' as a block record."""
        from agent_os.memory.ingest import _build_symbol_outcomes

        # A record with the legacy/wrong stage name
        records = [
            {"run_id": "r1", "symbol": "RELIANCE", "stage": "blocked",
             "payload": {"gate": "daily_loss_limit", "reason": "limit_hit"}},
        ]
        outcomes = _build_symbol_outcomes(records, max_symbols=50)
        # 'blocked' stage must not trigger the block outcome
        assert "RELIANCE" in outcomes
        assert outcomes["RELIANCE"]["blocked"] is False, (
            "_build_symbol_outcomes must not mark stage='blocked' as blocked; "
            "only stage='block' (matching router source) should count"
        )

    def test_build_symbol_outcomes_recognises_block_stage(self, tmp_path):
        """_build_symbol_outcomes correctly processes stage='block' records."""
        from agent_os.memory.ingest import _build_symbol_outcomes

        records = [
            {"run_id": "r1", "symbol": "RELIANCE", "stage": "block",
             "payload": {"gate": "daily_loss_limit", "reason": "limit_hit"}},
        ]
        outcomes = _build_symbol_outcomes(records, max_symbols=50)
        assert outcomes["RELIANCE"]["blocked"] is True
        assert outcomes["RELIANCE"]["block_reason"] == "daily_loss_limit"


# ===========================================================================
# 13. L2-F1 — block_reason extraction from a real block audit record
# ===========================================================================

class TestBlockReasonExtraction:
    def test_block_reason_from_real_block_record(self, tmp_path):
        """block_reason is extracted from a real gate='daily_loss_limit' block record."""
        # Build audit log with a real block record
        path = str(tmp_path / "audit_block.jsonl")
        log = AuditLog(path, run_id="2026-01-15-signal")
        log.append("signal", "SYM", {"rating": "Buy"})
        log.append("block", "SYM", {"gate": "daily_loss_limit", "reason": "limit_hit"})

        br = _make_book_report(book="signal", session_date="2026-01-15", blocked=1)
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])
        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4096)

        episode = ingest_session(
            audit=log,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )

        assert episode is not None
        sym_outcome = episode["symbol_outcomes"]["SYM"]
        assert sym_outcome["blocked"] is True
        # block_reason must be the gate key, matching what the spine router writes
        assert sym_outcome["block_reason"] == "daily_loss_limit", (
            f"Expected block_reason='daily_loss_limit', got {sym_outcome['block_reason']!r}"
        )

    def test_block_reason_is_gate_not_reason_field(self, tmp_path):
        """block_reason uses the 'gate' key first, not 'reason'."""
        from agent_os.memory.ingest import _build_symbol_outcomes

        records = [
            {"run_id": "r1", "symbol": "INFY", "stage": "block",
             "payload": {"gate": "position_limit", "reason": "max_pos_exceeded"}},
        ]
        outcomes = _build_symbol_outcomes(records, max_symbols=50)
        assert outcomes["INFY"]["block_reason"] == "position_limit"

    def test_block_reason_falls_back_to_reason_when_no_gate(self, tmp_path):
        """block_reason falls back to 'reason' when 'gate' is absent."""
        from agent_os.memory.ingest import _build_symbol_outcomes

        records = [
            {"run_id": "r1", "symbol": "TCS", "stage": "block",
             "payload": {"reason": "unknown_instrument"}},
        ]
        outcomes = _build_symbol_outcomes(records, max_symbols=50)
        assert outcomes["TCS"]["block_reason"] == "unknown_instrument"


# ===========================================================================
# 14. L2-F6 — empty-audit run_id fallback
# ===========================================================================

class TestEmptyAuditRunIdFallback:
    def test_empty_audit_uses_audit_run_id(self, tmp_path):
        """An AuditLog with no records uses audit.run_id for source.run_id."""
        # Create an AuditLog that has no records appended
        path = str(tmp_path / "audit_empty.jsonl")
        log = AuditLog(path, run_id="2026-01-15-signal")
        # Do NOT append any records — simulate a no-trade session

        br = _make_book_report(book="signal", session_date="2026-01-15",
                               filled=0, blocked=0)
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])
        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4096)

        episode = ingest_session(
            audit=log,
            report_jsonl_path=str(report_path),
            session_date="2026-01-15",
            book="signal",
            tier_store=store,
        )

        assert episode is not None
        assert episode["source"]["run_id"] == "2026-01-15-signal", (
            f"Expected run_id='2026-01-15-signal', got {episode['source']['run_id']!r}"
        )
        assert episode["symbol_outcomes"] == {}, (
            f"Expected empty symbol_outcomes, got {episode['symbol_outcomes']!r}"
        )


# ===========================================================================
# 15. T05 — positive complete-public-API test
# ===========================================================================

class TestPublicAPICompleteness:
    def test_public_callable_surface_is_exactly_ingest_session(self):
        """The public callable surface of agent_os.memory.ingest is exactly
        {'ingest_session'} (plus optional exception classes IdentityMismatch and
        IngestRejected).  No additional public write function may exist under any
        name.

        This test catches a future write function added under ANY name.
        """
        import inspect
        import agent_os.memory.ingest as ingest_module

        # Collect all public names in the module (defined in this module, not re-exported)
        module_file = ingest_module.__file__
        public_callables = set()
        for name in dir(ingest_module):
            if name.startswith("_"):
                continue
            obj = getattr(ingest_module, name)
            if not callable(obj):
                continue
            # Only include names actually defined in this module (not imported callables)
            obj_module = getattr(obj, "__module__", None)
            if obj_module != ingest_module.__name__:
                continue
            public_callables.add(name)

        # Allowed public callables: the write function + exception classes
        allowed = {"ingest_session", "IdentityMismatch", "IngestRejected"}
        unexpected = public_callables - allowed
        assert unexpected == set(), (
            f"Unexpected public callables in agent_os.memory.ingest: {unexpected}. "
            "The only permitted public write entry point is 'ingest_session'. "
            "Exception classes IdentityMismatch and IngestRejected are also permitted."
        )


# ===========================================================================
# 16. Reviewer finding 1 — audit run identity must match (session_date, book)
# ===========================================================================

class TestAuditSessionIdentity:
    def test_audit_from_different_run_rejected_even_with_matching_report(self, tmp_path):
        """A VALID audit from a DIFFERENT run/book must not be ingested into another
        session's report — even when the report contains a matching (session_date, book).
        The spine names run_ids f'{date}-{book}', so audit '2026-01-14-shadow' must not
        feed a 2026-01-15/signal episode (reviewer's probe)."""
        path = str(tmp_path / "audit_other.jsonl")
        log = AuditLog(path, run_id="2026-01-14-shadow")  # a different, otherwise-valid run
        log.append("signal", "SYM", {"rating": "Buy"})
        log.append("fill", "SYM", {"fill": {"qty": 10, "price": 100.0}, "charges": {"total": 5.0}})

        # The report legitimately contains 2026-01-15 / signal.
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])
        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4096)

        with pytest.raises(IdentityMismatch):
            ingest_session(
                audit=log,
                report_jsonl_path=str(report_path),
                session_date="2026-01-15",
                book="signal",
                tier_store=store,
            )
        assert len(store.read_all()) == 0, "no episode on audit/session identity mismatch"

    def test_matching_run_id_ingests(self, tmp_path):
        """An audit whose run_id == f'{date}-{book}' ingests normally."""
        audit = _make_audit(tmp_path)  # default run_id == "2026-01-15-signal"
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])
        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4096)
        episode = ingest_session(
            audit=audit, report_jsonl_path=str(report_path),
            session_date="2026-01-15", book="signal", tier_store=store,
        )
        assert episode is not None

    def test_explicit_expected_run_id_override(self, tmp_path):
        """An explicit expected_run_id is honored for non-default run naming."""
        path = str(tmp_path / "audit_custom.jsonl")
        log = AuditLog(path, run_id="custom-run-42")
        log.append("signal", "SYM", {"rating": "Buy"})
        br = _make_book_report(book="signal", session_date="2026-01-15")
        report_path = tmp_path / "session.jsonl"
        _write_session_jsonl(report_path, [br])
        store = TierStore(str(tmp_path / "memory"), "episodic", max_entries=100, max_chars_per_entry=4096)

        # Default expected (2026-01-15-signal) rejects custom-run-42.
        with pytest.raises(IdentityMismatch):
            ingest_session(
                audit=log, report_jsonl_path=str(report_path),
                session_date="2026-01-15", book="signal", tier_store=store,
            )
        # Explicit override accepts it.
        episode = ingest_session(
            audit=log, report_jsonl_path=str(report_path),
            session_date="2026-01-15", book="signal", tier_store=store,
            expected_run_id="custom-run-42",
        )
        assert episode is not None
        assert episode["source"]["run_id"] == "custom-run-42"


# ===========================================================================
# 17. Reviewer finding 2 — provenance survives truncation
# ===========================================================================

class TestProvenancePreservedOnTruncation:
    def test_truncated_entry_keeps_provenance(self, tmp_path):
        """An oversized episode is truncated but RETAINS source, session_date, book —
        every stored entry still cites its source."""
        store = TierStore(str(tmp_path), "episodic", max_entries=10, max_chars_per_entry=512)
        entry = {
            "session_date": "2026-01-15",
            "book": "signal",
            "source": {"run_id": "2026-01-15-signal",
                       "report_path": "/runs/session-2026-01-15.jsonl"},
            "symbol_outcomes": {
                f"SYM{i:03d}": {"filled": True, "blocked": False, "block_reason": None}
                for i in range(200)
            },
        }
        store.append(entry)
        stored = store.read_all()
        assert len(stored) == 1
        e = stored[0]
        assert e.get("__truncated__") is True, "a too-large entry must become a truncation envelope"
        # Provenance MUST survive.
        assert e["session_date"] == "2026-01-15"
        assert e["book"] == "signal"
        assert e["source"]["run_id"] == "2026-01-15-signal"
        # And the stored line still respects the cap.
        assert len(json.dumps(e, ensure_ascii=True, sort_keys=True)) <= 512

    def test_cap_too_small_for_provenance_raises(self, tmp_path):
        """If provenance alone cannot fit the cap, append RAISES (never silently drops
        the source citation)."""
        store = TierStore(str(tmp_path), "episodic", max_entries=10,
                          max_chars_per_entry=_MIN_ENTRY_CHARS)
        entry = {
            "session_date": "2026-01-15",
            "book": "signal",
            "source": {"run_id": "2026-01-15-signal",
                       "report_path": "/very/long/path/" + ("x" * 300) + ".jsonl"},
            "symbol_outcomes": {f"SYM{i}": 1 for i in range(50)},
        }
        with pytest.raises(ValueError):
            store.append(entry)
        assert len(store.read_all()) == 0
