"""agent_os/toolsets.py — Toolset isolation layer (Task C).

THE CORE SAFETY SURFACE.

Three hard rules:
  1. Conditional registration: live_toolset is ONLY registered when mode=='live' AND armed==True.
  2. Defense-in-depth: check_fn AND handler both gate on mode+armed (belt AND braces).
  3. Request-assembly leak assertion: assert_no_live_in_paper raises LiveToolLeak if live
     tool names appear in a paper active set.

UNLIKE Hermes dispatch (which returns a soft error string for unknown tools), our dispatch
RAISES UnknownTool. This is a stricter, fail-closed design.

UNLIKE Hermes dispatch (which never consults check_fn), our dispatch enforces check_fn as a
fail-closed gate. This is an intentional, documented divergence for defense-in-depth.

The paper wrappers (paper_run_session, paper_run_execution) hard-code mode=paper and
validate kwargs against a strict allowlist, raising on any injection attempt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UnknownTool(Exception):
    """Raised when dispatch is called for a tool that is not registered."""


class ToolUnavailable(Exception):
    """Raised when dispatch is called for a registered tool whose check_fn returns False.

    This is distinct from UnknownTool (unregistered tool) and is a defense-in-depth
    gate enforced at dispatch time. Unlike Hermes (which never consults check_fn at
    dispatch), agent_os enforces check_fn as a fail-closed gate — intentional divergence.
    """


class LiveToolLeak(Exception):
    """Raised when a live-tool name is detected in a paper-mode active set."""


class DisallowedKwarg(ValueError):
    """Raised when a paper wrapper receives a disallowed / injection kwarg."""


class LiveToolRefused(PermissionError):
    """Raised by live tool handler when called in wrong mode or unarmed context."""


# ---------------------------------------------------------------------------
# Tool dataclass
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    """A single tool definition.

    Attributes:
        name        : unique string identifier
        description : human-readable description (passed to LLM)
        schema      : the function-call schema dict the LLM would see
        check_fn    : Callable(context) -> bool. ANY exception -> False (fail-closed).
                      If None, the tool is always available (when registered).
        handler     : Callable(args: dict, context) -> Any.  The implementation.
    """
    name: str
    description: str
    schema: Dict[str, Any]
    handler: Callable
    check_fn: Optional[Callable] = field(default=None)

    def is_available(self, context) -> bool:
        """Return True iff the tool is available for the given context.

        Fail-closed: any exception from check_fn is treated as False.
        """
        if self.check_fn is None:
            return True
        try:
            return bool(self.check_fn(context))
        except Exception:
            return False


# ---------------------------------------------------------------------------
# YAML loader (stdlib only — no PyYAML)
# ---------------------------------------------------------------------------

def _load_yaml(path: str) -> Dict[str, Any]:
    """Minimal, safe YAML loader for the specific toolsets.yaml format.

    Supports only the subset we need:
      - top-level keys ending with ':'
      - second-level keys ending with ':'
      - list items starting with '  - '

    Raises ValueError on malformed content. Fail-closed.
    """
    result: Dict[str, Any] = {}
    current_top: Optional[str] = None
    current_sub: Optional[str] = None

    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    for lineno, raw in enumerate(lines, 1):
        # Strip inline comments and trailing whitespace
        line = raw.split("#")[0].rstrip()
        if not line.strip():
            continue

        # Measure indentation
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if indent == 0:
            # Top-level key
            if not stripped.endswith(":"):
                raise ValueError(
                    f"toolsets.yaml line {lineno}: expected top-level key ending ':', got: {raw!r}"
                )
            current_top = stripped[:-1].strip()
            current_sub = None
            result[current_top] = {}

        elif indent == 2:
            if current_top is None:
                raise ValueError(
                    f"toolsets.yaml line {lineno}: indented content before any top-level key"
                )
            if stripped.startswith("- "):
                # List item under current_top directly (modes: paper: - toolset)
                # This shouldn't happen with 2-space indent; modes values are at 4-space
                raise ValueError(
                    f"toolsets.yaml line {lineno}: unexpected list item at indent 2"
                )
            elif stripped.endswith(":"):
                current_sub = stripped[:-1].strip()
                result[current_top][current_sub] = []
            else:
                raise ValueError(
                    f"toolsets.yaml line {lineno}: expected sub-key or list item, got: {raw!r}"
                )

        elif indent == 4:
            if current_top is None or current_sub is None:
                raise ValueError(
                    f"toolsets.yaml line {lineno}: list item with no parent key"
                )
            if not stripped.startswith("- "):
                raise ValueError(
                    f"toolsets.yaml line {lineno}: expected '- item', got: {raw!r}"
                )
            item = stripped[2:].strip()
            if not isinstance(result[current_top].get(current_sub), list):
                result[current_top][current_sub] = []
            result[current_top][current_sub].append(item)

        else:
            raise ValueError(
                f"toolsets.yaml line {lineno}: unexpected indentation ({indent} spaces): {raw!r}"
            )

    return result


def _parse_toolsets_yaml(path: str) -> tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Parse toolsets.yaml and return (toolset_tools, mode_toolsets).

    toolset_tools  : {toolset_name: [tool_name, ...]}
    mode_toolsets  : {mode_name: [toolset_name, ...]}

    Raises ValueError on missing sections.
    """
    data = _load_yaml(path)
    if "toolsets" not in data:
        raise ValueError("toolsets.yaml: missing top-level 'toolsets' key")
    if "modes" not in data:
        raise ValueError("toolsets.yaml: missing top-level 'modes' key")

    toolset_tools: Dict[str, List[str]] = {}
    for ts_name, tools in data["toolsets"].items():
        if not isinstance(tools, list):
            raise ValueError(f"toolsets.yaml: toolset '{ts_name}' value is not a list")
        toolset_tools[ts_name] = list(tools)

    mode_toolsets: Dict[str, List[str]] = {}
    for mode_name, ts_list in data["modes"].items():
        if not isinstance(ts_list, list):
            raise ValueError(f"toolsets.yaml: mode '{mode_name}' value is not a list")
        mode_toolsets[mode_name] = list(ts_list)

    return toolset_tools, mode_toolsets


_YAML_PATH = str(Path(__file__).parent / "toolsets.yaml")

# Lazy-loaded module-level cache
_toolset_tools: Optional[Dict[str, List[str]]] = None
_mode_toolsets: Optional[Dict[str, List[str]]] = None

def _ensure_loaded() -> tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    global _toolset_tools, _mode_toolsets
    if _toolset_tools is None:
        _toolset_tools, _mode_toolsets = _parse_toolsets_yaml(_YAML_PATH)
    return _toolset_tools, _mode_toolsets


# ---------------------------------------------------------------------------
# The set of live-tool names — derived from YAML live_toolset (single source of truth).
# F3: derive at import so it cannot drift from the YAML definition.
# ---------------------------------------------------------------------------

def _derive_live_tool_names() -> frozenset:
    """Derive the live tool names from the YAML live_toolset at import time.

    Raises AssertionError if 'place_live_order' is not present (guards against
    accidental removal from the YAML).
    """
    toolset_tools, _ = _parse_toolsets_yaml(_YAML_PATH)
    live_tools = frozenset(toolset_tools.get("live_toolset", []))
    assert "place_live_order" in live_tools, (
        "SAFETY CONFIG ERROR: 'place_live_order' must be in the YAML live_toolset. "
        "Its accidental removal would silently break the live-tool isolation guard."
    )
    return live_tools


_LIVE_TOOL_NAMES: frozenset = _derive_live_tool_names()


# ---------------------------------------------------------------------------
# Paper wrapper argument validation
# ---------------------------------------------------------------------------

_PAPER_SESSION_ALLOWED_KWARGS: frozenset = frozenset({"session_date", "universe"})
_PAPER_EXECUTION_ALLOWED_KWARGS: frozenset = frozenset({"session_date"})

# All kwarg keys that represent injection vectors (rejected unconditionally)
_INJECTION_KWARGS: frozenset = frozenset({
    "out_dir", "signals_path", "path", "graph", "books", "security_master",
    "quote_source", "broker", "mode", "armed", "portfolio_factory",
})


def _validate_paper_kwargs(kwargs: dict, allowed: frozenset) -> None:
    """Raise DisallowedKwarg if any kwarg is not in the allowed set."""
    for key in kwargs:
        if key in _INJECTION_KWARGS or key not in allowed:
            raise DisallowedKwarg(
                f"Disallowed kwarg '{key}' in paper wrapper call. "
                f"Allowed: {sorted(allowed)}"
            )


def paper_run_session(*, session_date: str, **kwargs) -> Any:
    """Narrow wrapper around run_session — hard-codes mode=paper.

    Only accepts: session_date, universe (optional list of plain strings).
    Rejects all injection kwargs (out_dir, signals_path, broker, graph, etc.).

    Heavy deps (graph, security_master, quote_source, broker) come from a
    server-side context; they are NEVER accepted from LLM arguments.

    NOTE: In unit tests this will raise a runtime error (no live infrastructure)
    but kwarg hygiene is validated before any infrastructure is touched.
    """
    _validate_paper_kwargs(kwargs, _PAPER_SESSION_ALLOWED_KWARGS)
    # Reject universe items that are not plain strings
    universe = kwargs.get("universe", None)
    if universe is not None:
        if not isinstance(universe, (list, tuple)):
            raise ValueError("'universe' must be a list of plain strings")
        for item in universe:
            if not isinstance(item, str):
                raise ValueError(f"'universe' items must be plain strings, got: {type(item)}")

    # We do NOT import or call the spine here in tests; the wrapper validates
    # hygiene only.  In production the scheduler would inject live deps from a
    # server-side context object, not from LLM kwargs.
    raise NotImplementedError(
        "paper_run_session requires a live server context; "
        "call via the agent_os scheduler, not directly."
    )


def paper_run_execution(*, session_date: str, **kwargs) -> Any:
    """Narrow wrapper around run_execution_phase — hard-codes mode=paper.

    Only accepts: session_date.
    Rejects all injection kwargs.
    """
    _validate_paper_kwargs(kwargs, _PAPER_EXECUTION_ALLOWED_KWARGS)
    raise NotImplementedError(
        "paper_run_execution requires a live server context; "
        "call via the agent_os scheduler, not directly."
    )


# ---------------------------------------------------------------------------
# Tool catalog (all tools, with their schemas + handlers + check_fns)
# ---------------------------------------------------------------------------

def _make_schema(name: str, description: str, params: Optional[dict] = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": params or {"type": "object", "properties": {}, "required": []},
    }


def _data_check(context) -> bool:
    """Data tools are always available (read-only market data)."""
    return True


def _paper_check(context) -> bool:
    """Paper tools are available in paper mode only."""
    return getattr(context, "mode", "paper") == "paper"


def _audit_check(context) -> bool:
    """Audit read tools are always available (read-only)."""
    return True


def _live_check(context) -> bool:
    """Live tool check: mode must be 'live' AND armed must be True. Fail-closed."""
    mode = getattr(context, "mode", "paper")
    armed = getattr(context, "armed", False)
    return mode == "live" and bool(armed)


# --- Handlers ---

def _handle_get_prices(args: dict, context) -> dict:
    raise NotImplementedError("get_prices requires live data infrastructure")

def _handle_get_news(args: dict, context) -> dict:
    raise NotImplementedError("get_news requires live data infrastructure")

def _handle_get_fundamentals(args: dict, context) -> dict:
    raise NotImplementedError("get_fundamentals requires live data infrastructure")

def _handle_get_ohlcv(args: dict, context) -> dict:
    raise NotImplementedError("get_ohlcv requires live data infrastructure")

def _handle_security_master_lookup(args: dict, context) -> dict:
    raise NotImplementedError("security_master_lookup requires live data infrastructure")

def _handle_run_analysis(args: dict, context) -> dict:
    raise NotImplementedError("run_analysis requires live analysis infrastructure")

def _handle_paper_run_session(args: dict, context) -> dict:
    kwargs = {k: v for k, v in args.items() if k != "session_date"}
    session_date = args.get("session_date", "")
    return paper_run_session(session_date=session_date, **kwargs)

def _handle_paper_run_execution(args: dict, context) -> dict:
    kwargs = {k: v for k, v in args.items() if k != "session_date"}
    session_date = args.get("session_date", "")
    return paper_run_execution(session_date=session_date, **kwargs)

def _handle_audit_read_all(args: dict, context) -> dict:
    raise NotImplementedError("audit_read_all requires live audit infrastructure")

def _handle_audit_verify(args: dict, context) -> dict:
    raise NotImplementedError("audit_verify requires live audit infrastructure")

def _handle_place_live_order(args: dict, context) -> dict:
    """Live order handler — defense-in-depth check BEFORE any action.

    This handler is defense-in-depth layer 2 (layer 1 is conditional registration).
    It refuses unless mode=='live' AND armed==True.

    Even when allowed, it does NOT place an order directly — it only hands off
    to the spine path (the spine's 15 risk gates → paper broker → audit log).
    The LLM never gets a direct order placement.
    """
    mode = getattr(context, "mode", "paper")
    armed = getattr(context, "armed", False)
    if mode != "live" or not armed:
        raise LiveToolRefused(
            f"place_live_order REFUSED: requires mode='live' and armed=True, "
            f"got mode={mode!r} armed={armed!r}. "
            "This handler never places orders directly — it hands off to the spine."
        )
    # Even in live+armed mode, the handler does NOT place orders directly.
    # It must hand off to the spine (execution/session.py run_session),
    # which runs all 15 risk gates → paper/live broker → audit log.
    raise NotImplementedError(
        "place_live_order is a spine-handoff wrapper only; "
        "live trading is not implemented in agent_os directly."
    )


# ---------------------------------------------------------------------------
# Catalog of all tools
# ---------------------------------------------------------------------------

def _build_catalog() -> Dict[str, Tool]:
    """Return the complete tool catalog as {name: Tool}."""
    catalog = {}

    def _add(name, description, handler, check_fn=None, params=None):
        catalog[name] = Tool(
            name=name,
            description=description,
            schema=_make_schema(name, description, params),
            handler=handler,
            check_fn=check_fn,
        )

    # data_toolset
    _add("get_prices",
         "Fetch current market prices for one or more symbols (read-only).",
         _handle_get_prices, _data_check,
         {"type": "object", "properties": {
             "symbols": {"type": "array", "items": {"type": "string"}}
         }, "required": ["symbols"]})

    _add("get_news",
         "Fetch recent news headlines and sentiment for a symbol (read-only).",
         _handle_get_news, _data_check,
         {"type": "object", "properties": {
             "symbol": {"type": "string"}
         }, "required": ["symbol"]})

    _add("get_fundamentals",
         "Fetch fundamental financial data for a symbol (read-only).",
         _handle_get_fundamentals, _data_check,
         {"type": "object", "properties": {
             "symbol": {"type": "string"}
         }, "required": ["symbol"]})

    _add("get_ohlcv",
         "Fetch OHLCV (Open/High/Low/Close/Volume) historical data (read-only).",
         _handle_get_ohlcv, _data_check,
         {"type": "object", "properties": {
             "symbol": {"type": "string"},
             "days": {"type": "integer", "default": 30}
         }, "required": ["symbol"]})

    _add("security_master_lookup",
         "Look up instrument metadata from the security master (read-only).",
         _handle_security_master_lookup, _data_check,
         {"type": "object", "properties": {
             "symbol": {"type": "string"}
         }, "required": ["symbol"]})

    # analysis_toolset
    _add("run_analysis",
         "Run the analysis graph for a symbol to produce a signal via the spine (no orders).",
         _handle_run_analysis, _data_check,
         {"type": "object", "properties": {
             "symbol": {"type": "string"},
             "session_date": {"type": "string", "description": "ISO date YYYY-MM-DD"}
         }, "required": ["symbol"]})

    # paper_toolset
    # F5: use _paper_check (not _data_check) so these wrappers are only surfaced in
    # paper-mode get_definitions, making _paper_check live code and preventing these
    # paper-only tools from appearing in the live schema.
    _add("paper_run_session",
         "Run a full paper-trading session (analysis+execution). "
         "Accepts only: session_date, universe. "
         "Heavy deps are injected server-side, never from LLM args.",
         _handle_paper_run_session, _paper_check,
         {"type": "object", "properties": {
             "session_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
             "universe": {"type": "array", "items": {"type": "string"}, "description": "Optional symbol list"}
         }, "required": ["session_date"]})

    _add("paper_run_execution",
         "Run the execution phase of a paper-trading session. "
         "Accepts only: session_date.",
         _handle_paper_run_execution, _paper_check,
         {"type": "object", "properties": {
             "session_date": {"type": "string", "description": "ISO date YYYY-MM-DD"}
         }, "required": ["session_date"]})

    # audit_toolset
    _add("audit_read_all",
         "Read all audit log entries for a session (read-only).",
         _handle_audit_read_all, _audit_check,
         {"type": "object", "properties": {
             "session_date": {"type": "string"},
             "book": {"type": "string"}
         }, "required": ["session_date"]})

    _add("audit_verify",
         "Verify audit log integrity for a session (read-only, no writes).",
         _handle_audit_verify, _audit_check,
         {"type": "object", "properties": {
             "session_date": {"type": "string"}
         }, "required": ["session_date"]})

    # live_toolset — CONDITIONALLY registered (mode==live AND armed==True only)
    _add("place_live_order",
         "Hand off a trade recommendation to the live spine path. "
         "Requires mode='live' AND armed=True. "
         "Does NOT place orders directly — routes through 15 risk gates → broker → audit.",
         _handle_place_live_order, _live_check,
         {"type": "object", "properties": {
             "symbol": {"type": "string"},
             "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
             "session_date": {"type": "string"}
         }, "required": ["symbol", "action", "session_date"]})

    return catalog


_CATALOG: Optional[Dict[str, Tool]] = None

def _get_catalog() -> Dict[str, Tool]:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _build_catalog()
    return _CATALOG


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class _Registry:
    """Minimal tool registry for agent_os.

    This is NOT a clone of Hermes ToolRegistry. It is a stripped-down,
    fail-closed registry that enforces the three hard rules:
      1. Conditional registration.
      2. Defense-in-depth check_fn + handler gating.
      3. Leak assertion on request assembly.
    """

    def __init__(self, tools: Dict[str, Tool]):
        self._tools: Dict[str, Tool] = dict(tools)

    def _get_tool(self, name: str) -> Optional[Tool]:
        """Return the Tool for name, or None if not registered."""
        return self._tools.get(name)

    def _all_tools(self) -> List[Tool]:
        return list(self._tools.values())

    def dispatch(self, name: str, args: dict, context) -> Any:
        """Dispatch a tool by name.

        UNLIKE Hermes, we RAISE UnknownTool for unregistered tools (not a soft error string).
        This is stricter and fail-closed.

        T01 — DEFENSE-IN-DEPTH: Unlike Hermes (which never consults check_fn at dispatch),
        agent_os enforces check_fn as a fail-closed gate AFTER registration lookup.
        A registered tool whose check_fn returns False raises ToolUnavailable — the handler
        does NOT run. This is an intentional, documented divergence for extra safety.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownTool(
                f"Tool '{name}' is not registered in this registry "
                f"(mode={getattr(context, 'mode', '?')!r}, "
                f"armed={getattr(context, 'armed', False)!r}). "
                "This is a hard refusal, not a soft error."
            )
        # T01: enforce check_fn at dispatch (fail-closed defense-in-depth)
        if not tool.is_available(context):
            raise ToolUnavailable(
                f"Tool '{name}' is registered but its check_fn returned False for this context "
                f"(mode={getattr(context, 'mode', '?')!r}, "
                f"armed={getattr(context, 'armed', False)!r}). "
                "Dispatch refused — fail-closed gate."
            )
        return tool.handler(args, context)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _tool_names_for_mode(mode: str, armed: bool) -> Set[str]:
    """Return the set of tool names allowed for mode+armed, from toolsets.yaml."""
    toolset_tools, mode_toolsets = _ensure_loaded()
    allowed_toolsets = mode_toolsets.get(mode, [])

    names: Set[str] = set()
    for ts_name in allowed_toolsets:
        # CONDITIONAL: live_toolset only if armed
        if ts_name == "live_toolset" and not armed:
            continue
        tools = toolset_tools.get(ts_name, [])
        names.update(tools)
    return names


def build_registry(mode: str, *, armed: bool = False) -> _Registry:
    """Build and return a registry for the given mode+armed combination.

    CONDITIONAL REGISTRATION (hard rule 1):
      - In paper mode: live_toolset is NEVER registered.
      - In live mode WITHOUT armed=True: live_toolset is NOT registered.
      - Only in live mode WITH armed=True: live_toolset IS registered.
    """
    if mode not in ("paper", "live"):
        raise ValueError(f"Unknown mode: {mode!r}. Expected 'paper' or 'live'.")

    catalog = _get_catalog()
    allowed_names = _tool_names_for_mode(mode, armed)

    # Extra safety: even if the YAML somehow listed live tools for paper mode,
    # we strip them here (defense-in-depth, layer 0).
    if mode == "paper":
        allowed_names -= _LIVE_TOOL_NAMES

    registered = {}
    for name in allowed_names:
        tool = catalog.get(name)
        if tool is None:
            # A tool listed in the YAML but not in the catalog is a config error.
            # Fail LOUD (reviewer finding 3): a typo or stale name must not silently
            # change the runtime tool surface — raise rather than warn-and-skip.
            raise ValueError(
                f"toolsets.yaml references tool '{name}' which is not defined in the "
                f"catalog (mode={mode!r}, armed={armed!r}). This is a safety-config error: "
                f"the tool surface must be exactly the declared, catalogued set."
            )
        registered[name] = tool

    return _Registry(registered)


def active_tools(mode: str, *, armed: bool = False) -> List[Tool]:
    """Return the list of Tool objects active for mode+armed."""
    registry = build_registry(mode, armed=armed)
    return registry._all_tools()


def get_definitions(
    tool_names: Iterable[str],
    *,
    mode: str,
    armed: bool = False,
) -> List[dict]:
    """Return OpenAI-format tool schemas for the requested names.

    F1 (CRITICAL FIX) — ``mode`` and ``armed`` are EXPLICIT and validated here; they
    are NEVER derived (via ``getattr``) from an arbitrary, possibly LLM-influenced
    context object. The server-trusted caller (the agent_os runtime/scheduler) is the
    sole authority on mode/armed. A spoofed context can therefore not flip the mode
    and surface a live-order tool in paper mode.

    F2 (FIX) — there is NO ``extra_tools`` parameter. The only tools that can be
    surfaced are those registered for the validated mode/armed, which closes the
    alias-injection hole (an arbitrary-named alias of the live handler can no longer
    be passed in and leaked to the LLM).

    A tool is included ONLY if:
      1. it is registered in the registry built for the explicit mode/armed, AND
      2. its check_fn returns True for a trusted context synthesized from mode/armed
         (fail-closed on any exception).

    Defense layers and their (in)dependence — stated honestly:
      * conditional registration (build_registry) — the live tool is not even in the
        registry in paper mode.
      * INDEPENDENT mode/armed gate: ``_live_check`` returns False in paper, so the live
        tool's ``is_available(trusted_ctx)`` is False regardless of any name set. This is
        the guarantee that does NOT depend on ``_LIVE_TOOL_NAMES``.
      * name/identity belt below: skips a tool whose name is a known live name OR whose
        check_fn IS ``_live_check`` (so clearing ``_LIVE_TOOL_NAMES`` alone cannot leak).
      * final ``assert_no_live_in_paper`` over the produced schema (name-based backstop).
      The name-based strip/belt/assert share ``_LIVE_TOOL_NAMES`` and are redundant with
      each other; ``_live_check`` is the independent layer. Defeating all of them at once
      requires privileged in-process code mutation (not reachable by the model).
    """
    if mode not in ("paper", "live"):
        raise ValueError(f"Unknown mode: {mode!r}. Expected 'paper' or 'live'.")

    registry = build_registry(mode, armed=armed)
    # Trusted context for check_fn evaluation — built from the validated mode/armed,
    # never from caller-supplied/LLM-influenced objects.
    trusted_ctx = SimpleNamespace(mode=mode, armed=armed)

    result = []
    for name in sorted(set(tool_names)):
        tool = registry._tools.get(name)  # ONLY registered tools — no injection surface
        if tool is None:
            continue
        # Belt: a live tool never surfaces in paper mode. Identified by BOTH its name
        # (in _LIVE_TOOL_NAMES) AND its check_fn identity (_live_check) — the latter is
        # independent of the name set, so clearing _LIVE_TOOL_NAMES alone cannot leak it.
        if mode == "paper" and (name in _LIVE_TOOL_NAMES or tool.check_fn is _live_check):
            continue
        if tool.is_available(trusted_ctx):
            result.append({"type": "function", "function": dict(tool.schema)})

    # Braces: final request-assembly leak assertion over the produced schema.
    assert_no_live_in_paper([d["function"]["name"] for d in result], mode)
    return result


def assert_no_live_in_paper(active_names: Iterable[str], mode: str) -> None:
    """Assert that no live-tool names appear in a paper-mode active set.

    Raises LiveToolLeak if mode == 'paper' AND any live-tool name is found.
    This is the request-assembly leak assertion (hard rule 3).

    Does NOT raise for mode == 'live' (live tools are expected there).

    F3: names are normalized (strip + casefold) before comparison so cosmetic
    variants ('PLACE_LIVE_ORDER', trailing space, etc.) are caught.
    """
    if mode != "paper":
        return
    # Materialize to a list so we can iterate twice
    names_list = list(active_names)
    # Normalize both sides: strip whitespace and casefold
    normalized_live = frozenset(n.strip().casefold() for n in _LIVE_TOOL_NAMES)
    leaked_originals = [n for n in names_list if n.strip().casefold() in normalized_live]
    if leaked_originals:
        raise LiveToolLeak(
            f"SAFETY VIOLATION: live-tool name(s) {sorted(leaked_originals)!r} detected in "
            f"paper-mode active set. This is a hard safety failure — investigate immediately."
        )
