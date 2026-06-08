"""DalalStreet execution spine (Phase 3) — paper-trading, fail-closed.

This package turns a ``SignalDecision`` (produced by the analysis graph) into a
*simulated* trade, through a deterministic, unit-tested pipeline:

    signal -> intent -> sizing -> risk gates -> quote -> paper fill -> portfolio -> audit

Design posture everywhere: **fail-closed**. The LLM never touches money or
bypasses a gate; everything between the brain and the (simulated) broker is pure,
deterministic code. Cash equity only, once-daily cadence, CNC delivery, paper mode
is the hard default. See niranjan_docs/12-phase3-execution-spine.md (contracts) and
niranjan_docs/13-phase3-build-plan.md (build order).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
