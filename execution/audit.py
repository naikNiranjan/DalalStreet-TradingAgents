"""Audit log (spine Contract 7) — append-only, hash-chained decision trail.

Append-only JSONL where each record carries ``prev_hash`` + ``hash(record)`` so any
tampering or gap is detectable. One record per stage of a decision's lifecycle
(``signal`` -> ``gate`` -> ``order`` -> ``fill``/``reject``/``block``...). The invariant
enforced by gate ``audit_writable``: **no order is placed without its preceding audit
records written.** The daily report reads coverage from this log.

The hash covers the full record body *including* ``prev_hash``, so the chain is the
hash list: editing any field of any record, or removing/reordering a record, breaks
:meth:`AuditLog.verify`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

__all__ = ["AuditRecord", "AuditLog", "GENESIS_HASH"]

GENESIS_HASH = "0" * 64
_BODY_KEYS = ("ts", "run_id", "symbol", "stage", "payload", "prev_hash")


def _jsonable(x: Any) -> Any:
    """Recursively convert contracts (dataclasses / enums / datetimes) to JSON-safe values."""
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, datetime):
        return x.isoformat()
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {f.name: _jsonable(getattr(x, f.name)) for f in dataclasses.fields(x)}
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return str(x)


def _canonical(body: dict) -> str:
    """Deterministic JSON for hashing: sorted keys, no whitespace."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    ts: str
    run_id: str
    symbol: str
    stage: str
    payload: dict
    prev_hash: str
    hash: str

    @staticmethod
    def compute_hash(body: dict) -> str:
        return _sha256(_canonical(body))


class AuditLog:
    """Append-only, hash-chained JSONL log."""

    def __init__(self, path: str, *, run_id: str, clock: Optional[Callable[[], datetime]] = None):
        self.path = path
        self.run_id = run_id
        self._clock = clock or (lambda: datetime.now())
        self._prev_hash = self._tail_hash()

    # --- internal ----------------------------------------------------------

    def _tail_hash(self) -> str:
        """Resume the chain from an existing file (so re-opening keeps it intact)."""
        records = self.read_all()
        return records[-1]["hash"] if records else GENESIS_HASH

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    # --- write -------------------------------------------------------------

    def append(self, stage: str, symbol: str, payload: Any) -> AuditRecord:
        """Append one lifecycle record and return it (raises if not writable)."""
        body = {
            "ts": self._clock().isoformat(),
            "run_id": self.run_id,
            "symbol": symbol,
            "stage": stage,
            "payload": _jsonable(payload),
            "prev_hash": self._prev_hash,
        }
        h = AuditRecord.compute_hash(body)
        line = json.dumps({**body, "hash": h}, ensure_ascii=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._prev_hash = h
        return AuditRecord(**body, hash=h)

    # --- integrity ---------------------------------------------------------

    def verify(self) -> bool:
        """Re-read from disk and confirm the hash chain is intact (no tamper, no gaps)."""
        prev = GENESIS_HASH
        for rec in self.read_all():
            if any(k not in rec for k in (*_BODY_KEYS, "hash")):
                return False
            if rec["prev_hash"] != prev:
                return False  # broken link == gap / reorder
            body = {k: rec[k] for k in _BODY_KEYS}
            if AuditRecord.compute_hash(body) != rec["hash"]:
                return False  # tampered record
            prev = rec["hash"]
        return True

    def is_writable(self) -> bool:
        """True iff a record could be appended now (feeds the audit_writable gate)."""
        if os.path.exists(self.path):
            return os.access(self.path, os.W_OK)
        parent = os.path.dirname(os.path.abspath(self.path))
        return os.path.isdir(parent) and os.access(parent, os.W_OK)

    def __len__(self) -> int:
        return len(self.read_all())
