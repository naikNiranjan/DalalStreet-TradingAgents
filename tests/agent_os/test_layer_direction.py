"""Layer-direction + cross-cutting safety tests for agent_os (doc 15 exit criterion #5).

These assert the architectural invariant that makes the Agent Operating Layer safe to
add at all:

  * ``agent_os`` may import from the spine (``execution`` / ``tradingagents``);
  * the spine must NEVER import from ``agent_os`` — so the trading core stays
    standalone and the layer is strictly optional.

Plus a cross-module tie-in to capability #4: in paper mode the active tool set exposes
no order-placement tool and no LLM memory-write tool (the only memory write path is the
deterministic ``ingest_session``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPINE_DIRS = ("execution", "tradingagents")
AGENT_OS_DIR = REPO_ROOT / "agent_os"

_IMPORT_AGENT_OS = re.compile(r"^\s*(?:from|import)\s+agent_os(?:\b|\.)", re.MULTILINE)


def _py_files(root: Path):
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _imports_module(py_file: Path, top_level: str) -> bool:
    """True if py_file imports `top_level` (or a submodule of it), via AST."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == top_level or alias.name.startswith(top_level + "."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == top_level or mod.startswith(top_level + "."):
                return True
    return False


# ---------------------------------------------------------------------------
# Direction: the spine must never import agent_os
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spine_dir", SPINE_DIRS)
def test_spine_never_imports_agent_os(spine_dir):
    root = REPO_ROOT / spine_dir
    offenders = []
    for py_file in _py_files(root):
        text = py_file.read_text(encoding="utf-8")
        if _IMPORT_AGENT_OS.search(text) or _imports_module(py_file, "agent_os"):
            offenders.append(str(py_file.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"Spine modules import agent_os (forbidden — the spine must stay standalone): {offenders}"
    )


def test_agent_os_modules_import_cleanly():
    import agent_os  # noqa: F401
    import agent_os.rules.loader  # noqa: F401
    import agent_os.playbooks.schema  # noqa: F401
    import agent_os.playbooks.catalog  # noqa: F401
    import agent_os.toolsets  # noqa: F401
    import agent_os.memory.ingest  # noqa: F401
    import agent_os.memory.tiers  # noqa: F401


def test_agent_os_depends_on_spine():
    """The layering is real: at least one agent_os module imports from the spine."""
    depends = False
    for py_file in _py_files(AGENT_OS_DIR):
        if _imports_module(py_file, "execution") or _imports_module(py_file, "tradingagents"):
            depends = True
            break
    assert depends, "Expected agent_os to import from the spine (execution/tradingagents)."


# ---------------------------------------------------------------------------
# Cross-module safety: paper mode exposes no order / memory-write tool
# ---------------------------------------------------------------------------

# Denylist of order/execution/memory-write verb stems (secondary guard). Extended
# beyond the obvious to cover execute_trade / submit_trade / trade_execution etc.
_FORBIDDEN_TOOL_PATTERNS = re.compile(
    r"(live|place_order|order|execute|submit|trade|transact|write_memory|memory_write|remember|broker)",
    re.IGNORECASE,
)

# Allowlist: the EXACT set of tools the paper-mode LLM may see. An allowlist is far
# stronger than a denylist — any unexpected tool (order, memory-write, or otherwise)
# fails this test and forces a deliberate review before it can ever reach the model.
_EXPECTED_PAPER_TOOLS = {
    "get_prices", "get_news", "get_fundamentals", "get_ohlcv", "security_master_lookup",
    "run_analysis",
    "paper_run_session", "paper_run_execution",
    "audit_read_all", "audit_verify",
}


def test_paper_mode_tool_set_matches_allowlist():
    """Strong guard: the paper-mode tool set is EXACTLY the known-good allowlist."""
    from agent_os.toolsets import active_tools

    names = {t.name for t in active_tools("paper")}
    assert names == _EXPECTED_PAPER_TOOLS, (
        f"paper-mode tool set drifted from the allowlist. "
        f"unexpected={sorted(names - _EXPECTED_PAPER_TOOLS)} "
        f"missing={sorted(_EXPECTED_PAPER_TOOLS - names)}"
    )


def test_paper_mode_has_no_order_or_memory_write_tool():
    from agent_os.toolsets import active_tools, assert_no_live_in_paper

    names = [t.name for t in active_tools("paper")]
    # No live-order tool leaked into the paper active set.
    assert_no_live_in_paper(names, "paper")  # must not raise
    # No tool whose name implies order placement or an LLM memory-write path.
    offenders = [n for n in names if _FORBIDDEN_TOOL_PATTERNS.search(n)]
    assert not offenders, f"paper mode exposes forbidden tool(s): {offenders}"


def test_memory_has_no_freetext_write_entrypoint():
    """The only public write path into memory is the deterministic ingest_session."""
    import agent_os.memory.ingest as ingest

    # ingest_session is the sole documented write entrypoint; it takes an audit log +
    # report path, never free-text model output.
    assert hasattr(ingest, "ingest_session")
    public = [n for n in dir(ingest) if not n.startswith("_")]
    # No function suggesting arbitrary model-authored memory writes.
    bad = [n for n in public if re.search(r"(write_note|add_memory|remember|llm_write)", n, re.I)]
    assert not bad, f"memory exposes a free-text write entrypoint: {bad}"
