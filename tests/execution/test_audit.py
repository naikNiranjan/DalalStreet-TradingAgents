"""Task 6 — audit log: hash chain, tamper/gap detection, writeability, serialization."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from execution.audit import GENESIS_HASH, AuditLog
from execution.contracts import Action, GateResult, Order, Side

TS = datetime(2026, 6, 9, 11, 0)


def _log(tmp_path, name="audit.jsonl"):
    return AuditLog(str(tmp_path / name), run_id="run-1", clock=lambda: TS)


# --- chain integrity -----------------------------------------------------------

def test_records_link_into_a_chain(tmp_path):
    log = _log(tmp_path)
    r1 = log.append("signal", "RELIANCE.NS", {"action": "strong_buy"})
    r2 = log.append("order", "RELIANCE.NS", {"qty": 10})
    assert r1.prev_hash == GENESIS_HASH
    assert r2.prev_hash == r1.hash        # chained
    assert log.verify()
    assert len(log) == 2


def test_verify_detects_tampered_payload(tmp_path):
    log = _log(tmp_path)
    log.append("signal", "RELIANCE.NS", {"action": "strong_buy"})
    log.append("order", "RELIANCE.NS", {"qty": 10})
    # tamper: rewrite the first record's payload on disk
    lines = [json.loads(l) for l in open(log.path)]
    lines[0]["payload"]["action"] = "exit"
    with open(log.path, "w") as fh:
        fh.write("\n".join(json.dumps(l) for l in lines) + "\n")
    assert not log.verify()               # hash no longer matches the body


def test_verify_detects_a_dropped_record(tmp_path):
    log = _log(tmp_path)
    log.append("signal", "X", {"a": 1})
    log.append("gate", "X", {"b": 2})
    log.append("order", "X", {"c": 3})
    lines = open(log.path).read().splitlines()
    with open(log.path, "w") as fh:                 # drop the middle record
        fh.write(lines[0] + "\n" + lines[2] + "\n")
    assert not log.verify()                         # prev_hash link is broken


def test_verify_detects_reordering(tmp_path):
    log = _log(tmp_path)
    log.append("signal", "X", {"a": 1})
    log.append("order", "X", {"b": 2})
    lines = open(log.path).read().splitlines()
    with open(log.path, "w") as fh:
        fh.write(lines[1] + "\n" + lines[0] + "\n")  # swap
    assert not log.verify()


def test_reopen_resumes_chain(tmp_path):
    log = _log(tmp_path)
    log.append("signal", "X", {"a": 1})
    reopened = _log(tmp_path)                        # same path
    r = reopened.append("order", "X", {"b": 2})
    assert reopened.verify()
    assert r.prev_hash != GENESIS_HASH              # continued, not restarted


# --- writeability (feeds gate #10) ---------------------------------------------

def test_is_writable_true_for_normal_path(tmp_path):
    assert _log(tmp_path).is_writable()


def test_is_writable_false_for_nonexistent_dir():
    log = AuditLog("/nonexistent-dir-xyz/audit.jsonl", run_id="r")
    assert not log.is_writable()


# --- serialization of real contract objects ------------------------------------

def test_appends_contract_objects_jsonably(tmp_path):
    log = _log(tmp_path)
    order = Order("RELIANCE.NS", Side.BUY, 10)
    gate = GateResult(False, "kill_switch", "set", "block")
    log.append("order", "RELIANCE.NS", order)
    log.append("block", "RELIANCE.NS", gate)
    assert log.verify()
    recs = log.read_all()
    assert recs[0]["payload"]["side"] == "buy"          # enum -> value
    assert recs[0]["payload"]["order_type"] == "market"
    assert recs[1]["payload"]["gate"] == "kill_switch"
    # round-trips through JSON without error
    assert recs[1]["payload"]["allowed"] is False


def test_one_record_per_stage(tmp_path):
    log = _log(tmp_path)
    for stage in ("signal", "gate", "order", "fill"):
        log.append(stage, "X", {"s": stage})
    assert [r["stage"] for r in log.read_all()] == ["signal", "gate", "order", "fill"]
