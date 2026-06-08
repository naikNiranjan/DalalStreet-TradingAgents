"""Deterministic risk layer — sizing + fail-closed gates (the safety core)."""

from .guards import GateContext, evaluate, blocking_results, is_allowed
from .sizing import SizingResult, size

__all__ = [
    "GateContext",
    "evaluate",
    "blocking_results",
    "is_allowed",
    "SizingResult",
    "size",
]
