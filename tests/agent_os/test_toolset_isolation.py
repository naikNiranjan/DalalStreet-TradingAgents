"""THE PROOF TEST — Task C: Toolset isolation + safety invariants.

In PAPER mode, assert ALL of:
  1. active_tools('paper') contains NO live-order tool (assert by name — 'place_live_order' absent).
  2. get_definitions(<paper allowlist>, ctx) never includes the live schema (check_fn is False in
     paper even if its name were somehow present).
  3. the leak assertion fires (assert_no_live_in_paper raises) if a live-tool name is injected.
  4. a DIRECT dispatch('place_live_order', ...) in paper mode is REFUSED — UnknownTool (genuinely
     UNREGISTERED), AND the handler/wrapper raises rather than runs.
  5. each check_fn fails closed on exception (a check_fn that raises => tool unavailable).
  6. (positive control) in mode=='live' with armed=True, the live tool IS registered, proving
     paper isolation is real, not a wiring bug.

Plus: paper wrappers reject disallowed kwargs; audit_toolset is read-only.
"""

import inspect

import pytest

import agent_os.toolsets as ts
from agent_os.toolsets import (
    Tool,
    UnknownTool,
    ToolUnavailable,
    LiveToolLeak,
    LiveToolRefused,
    DisallowedKwarg,
    build_registry,
    active_tools,
    get_definitions,
    assert_no_live_in_paper,
)

LIVE_TOOL_NAME = "place_live_order"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Context:
    """Minimal context object passed to check_fn / get_definitions."""
    def __init__(self, mode: str = "paper", armed: bool = False):
        self.mode = mode
        self.armed = armed


# ---------------------------------------------------------------------------
# 1. active_tools('paper') contains NO live-order tool
# ---------------------------------------------------------------------------

class TestPaperModeNoLiveTool:
    def test_live_tool_absent_from_paper_active_tools(self):
        tools = active_tools("paper")
        names = {t.name for t in tools}
        assert LIVE_TOOL_NAME not in names, (
            f"SAFETY VIOLATION: '{LIVE_TOOL_NAME}' found in paper active_tools"
        )

    def test_paper_active_tools_non_empty(self):
        """Sanity: paper mode should have at least some tools (data, analysis, etc.)"""
        tools = active_tools("paper")
        assert len(tools) >= 1

    def test_expected_paper_tools_present(self):
        """Expected read-only + analysis tools should be present in paper mode."""
        tools = active_tools("paper")
        names = {t.name for t in tools}
        # These must be present
        assert "get_prices" in names
        assert "run_analysis" in names
        assert "paper_run_session" in names
        assert "audit_read_all" in names


# ---------------------------------------------------------------------------
# 2. get_definitions never includes live schema in paper
# ---------------------------------------------------------------------------

class TestGetDefinitionsNeverIncludesLive:
    def test_live_tool_excluded_from_definitions_in_paper(self):
        """Even if place_live_order is in the requested names list, paper mode excludes it."""
        paper_tools = active_tools("paper")
        paper_names = {t.name for t in paper_tools}
        # Inject the live tool name to simulate a worst-case caller error
        injected_names = paper_names | {LIVE_TOOL_NAME}

        defs = get_definitions(injected_names, mode="paper")
        def_names = {d["function"]["name"] for d in defs}
        assert LIVE_TOOL_NAME not in def_names, (
            "SAFETY VIOLATION: live schema appeared in paper get_definitions"
        )

    def test_get_definitions_returns_schemas_for_paper_tools(self):
        """Verify get_definitions returns non-empty list for valid paper tools."""
        paper_tools = active_tools("paper")
        paper_names = {t.name for t in paper_tools}
        defs = get_definitions(paper_names, mode="paper")
        assert len(defs) >= 1
        # Every returned definition must have the expected OpenAI function call structure
        for d in defs:
            assert d.get("type") == "function"
            assert "function" in d
            assert "name" in d["function"]

    def test_unknown_tool_name_silently_skipped_in_definitions(self):
        """get_definitions silently skips unknown names (not registered)."""
        defs = get_definitions({"nonexistent_tool_xyz"}, mode="paper")
        assert defs == []


# ---------------------------------------------------------------------------
# 3. Leak assertion fires on injection of live-tool name into paper set
# ---------------------------------------------------------------------------

class TestLeakAssertionFiresOnInjection:
    def test_assert_no_live_in_paper_raises_on_injection(self):
        """assert_no_live_in_paper raises LiveToolLeak if live-tool name is present."""
        paper_names = {t.name for t in active_tools("paper")}
        injected = paper_names | {LIVE_TOOL_NAME}
        with pytest.raises(LiveToolLeak):
            assert_no_live_in_paper(injected, "paper")

    def test_assert_no_live_in_paper_clean_passes(self):
        """assert_no_live_in_paper does not raise for clean paper set."""
        paper_names = {t.name for t in active_tools("paper")}
        # Should not raise
        assert_no_live_in_paper(paper_names, "paper")

    def test_assert_no_live_in_paper_live_mode_not_triggered(self):
        """assert_no_live_in_paper does NOT raise for live mode (it only guards paper)."""
        # In live mode the live tool is expected; the assertion is paper-specific
        live_names = {LIVE_TOOL_NAME, "get_prices"}
        # Should not raise when mode is 'live'
        assert_no_live_in_paper(live_names, "live")


# ---------------------------------------------------------------------------
# 4. Direct dispatch in paper mode is REFUSED — UnknownTool
# ---------------------------------------------------------------------------

class TestDispatchRefusalInPaperMode:
    def test_dispatch_live_tool_raises_unknown_tool_in_paper(self):
        """Dispatch of place_live_order in paper mode raises UnknownTool (not registered)."""
        registry = build_registry("paper", armed=False)
        ctx = _Context(mode="paper", armed=False)
        with pytest.raises(UnknownTool):
            registry.dispatch(LIVE_TOOL_NAME, {}, ctx)

    def test_dispatch_live_tool_does_not_return_soft_error_string(self):
        """
        UNLIKE Hermes, our dispatch does NOT return a soft error string.
        It RAISES UnknownTool. This test proves the stricter design.
        """
        registry = build_registry("paper", armed=False)
        ctx = _Context(mode="paper", armed=False)
        result = None
        raised = False
        try:
            result = registry.dispatch(LIVE_TOOL_NAME, {}, ctx)
        except UnknownTool:
            raised = True

        assert raised, "Expected UnknownTool to be raised, not a soft error"
        assert result is None, "dispatch must not return a string for unknown tools"

    def test_dispatch_known_paper_tool_succeeds(self):
        """Sanity: dispatching a known paper tool does not raise UnknownTool."""
        registry = build_registry("paper", armed=False)
        ctx = _Context(mode="paper", armed=False)
        # dispatch audit_read_all — it's a read-only tool, should work
        # (may raise for other reasons but not UnknownTool)
        try:
            registry.dispatch("audit_read_all", {}, ctx)
        except UnknownTool:
            pytest.fail("audit_read_all should be registered in paper mode")
        except Exception:
            pass  # Other exceptions (e.g. missing audit path) are OK


# ---------------------------------------------------------------------------
# 5. check_fn fails closed on exception
# ---------------------------------------------------------------------------

class TestCheckFnFailsClosed:
    """check_fn is fail-closed at BOTH the schema layer (Tool.is_available) and the
    dispatch layer (T01 — dispatch refuses a registered tool whose check_fn is False)."""

    def test_raising_check_fn_is_unavailable(self):
        """A check_fn that raises => Tool.is_available returns False (fail-closed)."""
        def boom_check(ctx):
            raise RuntimeError("boom — external probe failed")

        t = Tool(
            name="unstable_probe_tool",
            description="A tool whose check_fn raises",
            schema={"name": "unstable_probe_tool", "description": "...", "parameters": {}},
            check_fn=boom_check,
            handler=lambda args, ctx: {"ok": True},
        )
        assert t.is_available(_Context(mode="paper")) is False

    def test_returning_false_check_fn_is_unavailable(self):
        """A check_fn returning False => Tool.is_available returns False."""
        t = Tool(
            name="gated_false_tool",
            description="Always unavailable",
            schema={"name": "gated_false_tool", "description": "...", "parameters": {}},
            check_fn=lambda ctx: False,
            handler=lambda args, ctx: {},
        )
        assert t.is_available(_Context(mode="paper")) is False

    def test_dispatch_refuses_tool_with_false_check_fn(self):
        """T01: dispatch of a registered tool whose check_fn is False raises ToolUnavailable
        AND the handler does NOT run (check_fn is a real dispatch-time gate)."""
        ran = {"v": False}

        def handler(args, ctx):
            ran["v"] = True
            return {}

        gated = Tool(
            name="gated_dispatch_tool",
            description="check_fn always False",
            schema={"name": "gated_dispatch_tool", "description": "...", "parameters": {}},
            check_fn=lambda ctx: False,
            handler=handler,
        )
        reg = ts._Registry({"gated_dispatch_tool": gated})
        with pytest.raises(ToolUnavailable):
            reg.dispatch("gated_dispatch_tool", {}, _Context(mode="paper"))
        assert ran["v"] is False, "handler must NOT run when check_fn returns False"

    def test_dispatch_refuses_tool_with_raising_check_fn(self):
        """T01: a raising check_fn is fail-closed at dispatch too (ToolUnavailable, no handler run)."""
        ran = {"v": False}

        def boom_check(ctx):
            raise RuntimeError("boom")

        def handler(args, ctx):
            ran["v"] = True
            return {}

        boomtool = Tool(
            name="boom_dispatch_tool",
            description="check_fn raises",
            schema={"name": "boom_dispatch_tool", "description": "...", "parameters": {}},
            check_fn=boom_check,
            handler=handler,
        )
        reg = ts._Registry({"boom_dispatch_tool": boomtool})
        with pytest.raises(ToolUnavailable):
            reg.dispatch("boom_dispatch_tool", {}, _Context(mode="paper"))
        assert ran["v"] is False


class TestAuditToolsetReadOnly:
    def test_audit_toolset_contains_only_read_tools(self):
        """audit_toolset must only contain read-only tools (no write/order tools)."""
        tools = active_tools("paper")
        audit_tools = {t.name for t in tools if t.name.startswith("audit_")}
        # Must have at least audit_read_all
        assert "audit_read_all" in audit_tools, "audit_read_all must be in paper mode"
        # No write or order tools in audit toolset
        forbidden_patterns = ["write", "order", "place", "execute", "trade"]
        for name in audit_tools:
            for pattern in forbidden_patterns:
                assert pattern not in name.lower(), (
                    f"audit toolset contains potentially dangerous tool: {name}"
                )

    def test_audit_verify_present_and_read_only(self):
        """audit_verify should be present; it's read/verify only."""
        tools = active_tools("paper")
        names = {t.name for t in tools}
        assert "audit_verify" in names


# ---------------------------------------------------------------------------
# 6. Positive control — live mode with armed=True registers the live tool
# ---------------------------------------------------------------------------

class TestPositiveControlLiveModeRegistersLiveTool:
    def test_live_tool_present_in_live_armed_mode(self):
        """In live+armed mode, place_live_order IS registered — proving paper isolation is real."""
        tools = active_tools("live", armed=True)
        names = {t.name for t in tools}
        assert LIVE_TOOL_NAME in names, (
            "WIRING BUG: place_live_order must be present in live+armed mode "
            "(if absent, the paper isolation may be a wiring bug rather than real isolation)"
        )

    def test_live_tool_absent_in_live_unarmed_mode(self):
        """In live mode WITHOUT armed=True, the live tool must still NOT be registered."""
        tools = active_tools("live", armed=False)
        names = {t.name for t in tools}
        assert LIVE_TOOL_NAME not in names, (
            "place_live_order must be absent in live mode when armed=False"
        )

    def test_live_tool_dispatch_raises_in_live_unarmed_mode(self):
        """Even in live mode, dispatch raises unless armed=True."""
        registry = build_registry("live", armed=False)
        ctx = _Context(mode="live", armed=False)
        with pytest.raises(UnknownTool):
            registry.dispatch(LIVE_TOOL_NAME, {}, ctx)

    def test_live_tool_registered_in_live_armed_dispatch(self):
        """In live+armed, dispatch of place_live_order does not raise UnknownTool.
        (It may raise for other reasons — no broker context — but not UnknownTool.)
        """
        registry = build_registry("live", armed=True)
        ctx = _Context(mode="live", armed=True)
        try:
            registry.dispatch(LIVE_TOOL_NAME, {}, ctx)
        except UnknownTool:
            pytest.fail("place_live_order must be registered in live+armed mode")
        except Exception:
            pass  # other exceptions (no broker) are expected and OK


# ---------------------------------------------------------------------------
# Paper wrapper argument hygiene
# ---------------------------------------------------------------------------

class TestPaperWrapperArgumentHygiene:
    def test_paper_run_session_rejects_disallowed_kwargs(self):
        """paper_run_session must raise on disallowed injection kwargs."""
        from agent_os.toolsets import paper_run_session
        with pytest.raises(DisallowedKwarg):
            paper_run_session(
                session_date="2026-06-08",
                out_dir="/tmp/injected_path",  # disallowed
            )

    def test_paper_run_session_rejects_broker_kwarg(self):
        from agent_os.toolsets import paper_run_session
        with pytest.raises(DisallowedKwarg):
            paper_run_session(
                session_date="2026-06-08",
                broker=object(),  # disallowed
            )

    def test_paper_run_session_rejects_graph_kwarg(self):
        from agent_os.toolsets import paper_run_session
        with pytest.raises(DisallowedKwarg):
            paper_run_session(
                session_date="2026-06-08",
                graph=object(),  # disallowed
            )

    def test_paper_run_session_rejects_signals_path_kwarg(self):
        from agent_os.toolsets import paper_run_session
        with pytest.raises(DisallowedKwarg):
            paper_run_session(
                session_date="2026-06-08",
                signals_path="/tmp/injected.jsonl",  # disallowed
            )

    def test_paper_run_session_rejects_mode_override_kwarg(self):
        from agent_os.toolsets import paper_run_session
        with pytest.raises(DisallowedKwarg):
            paper_run_session(
                session_date="2026-06-08",
                mode="live",  # disallowed — wrapper must hard-code mode=paper
            )

    def test_paper_run_session_accepts_session_date(self):
        """T07: a valid call (only session_date) passes kwarg hygiene, then raises
        NotImplementedError specifically (needs a server context). It must NOT raise
        DisallowedKwarg/TypeError/ValueError for the allowed arg. Asserting the precise
        exception means a bug in argument processing can no longer hide behind a broad catch."""
        from agent_os.toolsets import paper_run_session
        with pytest.raises(NotImplementedError):
            paper_run_session(session_date="2026-06-08")

    def test_paper_run_session_valid_call_does_not_raise_disallowed_kwarg(self):
        """The allowed arg must never trip the kwarg-hygiene guard."""
        from agent_os.toolsets import paper_run_session
        try:
            paper_run_session(session_date="2026-06-08")
        except DisallowedKwarg:
            pytest.fail("session_date is allowed and must not raise DisallowedKwarg")
        except NotImplementedError:
            pass

    def test_paper_run_execution_rejects_disallowed_kwargs(self):
        from agent_os.toolsets import paper_run_execution
        with pytest.raises(DisallowedKwarg):
            paper_run_execution(
                session_date="2026-06-08",
                quote_source=object(),  # disallowed
            )

    def test_paper_run_execution_rejects_security_master_kwarg(self):
        from agent_os.toolsets import paper_run_execution
        with pytest.raises(DisallowedKwarg):
            paper_run_execution(
                session_date="2026-06-08",
                security_master=object(),  # disallowed
            )


# ---------------------------------------------------------------------------
# Defense-in-depth: handler refuses even when called directly (live mode armed)
# ---------------------------------------------------------------------------

class TestHandlerDefenseInDepth:
    def test_live_handler_refuses_in_paper_context(self):
        """Even if live tool handler is somehow called with paper context, it must refuse."""
        # Get the live tool's handler directly from live registry
        live_registry = build_registry("live", armed=True)
        live_tool_entry = live_registry._get_tool(LIVE_TOOL_NAME)
        assert live_tool_entry is not None, "live tool must be registered in live+armed"

        paper_ctx = _Context(mode="paper", armed=False)
        # F4: assert the SPECIFIC refusal exception, not any Exception — so the test
        # fails loudly if the refusal gate were removed and it fell through to
        # NotImplementedError.
        with pytest.raises(LiveToolRefused):
            live_tool_entry.handler({}, paper_ctx)

    def test_live_handler_refuses_without_armed_flag(self):
        """Live tool handler refuses when armed=False even in live mode."""
        live_registry = build_registry("live", armed=True)
        live_tool_entry = live_registry._get_tool(LIVE_TOOL_NAME)
        assert live_tool_entry is not None

        unarmed_ctx = _Context(mode="live", armed=False)
        with pytest.raises(LiveToolRefused):
            live_tool_entry.handler({}, unarmed_ctx)


# ---------------------------------------------------------------------------
# Registry structural tests
# ---------------------------------------------------------------------------

class TestRegistryStructure:
    def test_paper_registry_has_no_live_tool(self):
        """build_registry('paper') must not contain place_live_order at all."""
        registry = build_registry("paper", armed=False)
        assert registry._get_tool(LIVE_TOOL_NAME) is None, (
            "place_live_order must be ABSENT from paper registry, not just gated"
        )

    def test_live_armed_registry_has_live_tool(self):
        """build_registry('live', armed=True) must contain place_live_order."""
        registry = build_registry("live", armed=True)
        assert registry._get_tool(LIVE_TOOL_NAME) is not None

    def test_all_tools_have_schemas(self):
        """All registered tools must have a schema dict."""
        registry = build_registry("paper", armed=False)
        for tool in registry._all_tools():
            assert isinstance(tool.schema, dict), f"Tool {tool.name} missing schema"
            assert "name" in tool.schema, f"Tool {tool.name} schema missing 'name'"

    def test_all_tools_have_handlers(self):
        """All registered tools must have callable handlers."""
        registry = build_registry("paper", armed=False)
        for tool in registry._all_tools():
            assert callable(tool.handler), f"Tool {tool.name} handler not callable"


# ---------------------------------------------------------------------------
# F1 (CRITICAL) + F2 — get_definitions takes EXPLICIT validated mode/armed and has
# NO context/extra_tools injection surface. A spoofed context cannot flip the mode.
# ---------------------------------------------------------------------------

class TestGetDefinitionsExplicitModeOnly:
    def test_mode_is_required_keyword_only(self):
        """mode must be a required keyword-only param — it cannot be derived from a
        positional, possibly-LLM-influenced, context object."""
        sig = inspect.signature(get_definitions)
        params = sig.parameters
        assert "mode" in params
        assert params["mode"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["mode"].default is inspect.Parameter.empty, "mode must be required"

    def test_no_extra_tools_injection_surface(self):
        """F2: the extra_tools parameter is gone — there is no way to inject an
        arbitrary-named alias of the live handler into the returned schema."""
        params = inspect.signature(get_definitions).parameters
        assert "extra_tools" not in params
        assert "context" not in params  # no arbitrary-context mode source either

    def test_calling_without_mode_raises_type_error(self):
        with pytest.raises(TypeError):
            get_definitions(["get_prices"])  # missing required mode

    def test_spoofed_context_cannot_leak_live_schema_in_paper(self):
        """The pre-fix critical bug: a context whose __getattr__ returned mode='live',
        armed=True leaked the live schema. mode is now explicit, so paper is paper —
        requesting the live tool by name under mode='paper' yields nothing."""
        defs = get_definitions([LIVE_TOOL_NAME, "get_prices"], mode="paper")
        names = {d["function"]["name"] for d in defs}
        assert LIVE_TOOL_NAME not in names
        assert "get_prices" in names  # sanity: real paper tools still surface

    def test_live_mode_armed_includes_live_via_explicit_mode(self):
        """Positive control for the new signature: explicit live+armed surfaces the live tool."""
        defs = get_definitions([LIVE_TOOL_NAME], mode="live", armed=True)
        names = {d["function"]["name"] for d in defs}
        assert LIVE_TOOL_NAME in names

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            get_definitions(["get_prices"], mode="PAPER")  # case/typo -> fail closed


# ---------------------------------------------------------------------------
# F3 — _LIVE_TOOL_NAMES is the single source of truth, derived from the YAML.
# ---------------------------------------------------------------------------

class TestLiveToolNamesSyncedToYaml:
    def test_live_tool_names_equals_yaml_live_toolset(self):
        toolset_tools, _ = ts._parse_toolsets_yaml(ts._YAML_PATH)
        yaml_live = frozenset(toolset_tools.get("live_toolset", []))
        assert ts._LIVE_TOOL_NAMES == yaml_live, (
            "_LIVE_TOOL_NAMES must stay in sync with the YAML live_toolset "
            "(it is derived from it at import). A second live tool added to the YAML "
            "is therefore automatically covered by the strip guard + leak assertion."
        )

    def test_place_live_order_present(self):
        assert "place_live_order" in ts._LIVE_TOOL_NAMES

    def test_leak_assertion_normalizes_cosmetic_variants(self):
        """assert_no_live_in_paper catches casing/whitespace variants of a live name."""
        for variant in ("PLACE_LIVE_ORDER", " place_live_order ", "Place_Live_Order"):
            with pytest.raises(LiveToolLeak):
                assert_no_live_in_paper(["get_prices", variant], "paper")


# ---------------------------------------------------------------------------
# T02 — the paper-mode layer-0 strip guard works EVEN IF the YAML is misconfigured.
# ---------------------------------------------------------------------------

class TestStripGuardIndependentOfYaml:
    def test_strip_guard_catches_yaml_misconfiguration(self, monkeypatch):
        """Simulate a YAML misconfig that leaks a live tool into the paper allowlist;
        build_registry('paper') must STILL exclude it via the layer-0 strip."""
        real_fn = ts._tool_names_for_mode

        def spoof(mode, armed):
            names = set(real_fn(mode, armed))
            if mode == "paper":
                names |= {LIVE_TOOL_NAME}  # misconfig: live tool leaked into paper
            return names

        monkeypatch.setattr(ts, "_tool_names_for_mode", spoof)
        registry = build_registry("paper", armed=False)
        assert registry._get_tool(LIVE_TOOL_NAME) is None, (
            "layer-0 strip must drop the live tool even when the YAML/allowlist is wrong"
        )


# ---------------------------------------------------------------------------
# F5 / T04 — paper wrappers use _paper_check, so they are paper-only (not dead code).
# ---------------------------------------------------------------------------

class TestPaperWrappersModeGated:
    def test_paper_check_is_wired_to_paper_wrappers(self):
        catalog = ts._get_catalog()
        assert catalog["paper_run_session"].check_fn is ts._paper_check
        assert catalog["paper_run_execution"].check_fn is ts._paper_check

    def test_paper_wrappers_present_in_paper_definitions(self):
        defs = get_definitions(
            ["paper_run_session", "paper_run_execution"], mode="paper"
        )
        names = {d["function"]["name"] for d in defs}
        assert "paper_run_session" in names
        assert "paper_run_execution" in names

    def test_paper_wrappers_absent_from_live_definitions(self):
        """In live mode _paper_check returns False, so the paper wrappers do not surface."""
        defs = get_definitions(
            ["paper_run_session", "paper_run_execution"], mode="live", armed=True
        )
        names = {d["function"]["name"] for d in defs}
        assert "paper_run_session" not in names
        assert "paper_run_execution" not in names


# ---------------------------------------------------------------------------
# Defense-layer independence — the live tool is excluded in paper EVEN IF the
# name set (_LIVE_TOOL_NAMES) is cleared, because _live_check (its check_fn) is an
# independent mode/armed gate. (Hardening for the verify-pass minor finding.)
# ---------------------------------------------------------------------------

class TestBeltIndependentOfNameSet:
    def test_clearing_live_tool_names_alone_does_not_leak(self, monkeypatch):
        """Force the live tool into the paper registry AND clear _LIVE_TOOL_NAMES (defeating
        every name-based guard). The live tool must STILL be excluded from paper
        get_definitions — proving _live_check / check_fn-identity is an independent layer."""
        catalog = ts._get_catalog()
        live_tool = catalog[LIVE_TOOL_NAME]
        get_prices = catalog["get_prices"]

        def fake_build_registry(mode, *, armed=False):
            # Simulate a registration bug that puts the live tool into the paper registry.
            return ts._Registry({LIVE_TOOL_NAME: live_tool, "get_prices": get_prices})

        monkeypatch.setattr(ts, "build_registry", fake_build_registry)
        monkeypatch.setattr(ts, "_LIVE_TOOL_NAMES", frozenset())  # defeat name-based guards

        defs = get_definitions([LIVE_TOOL_NAME, "get_prices"], mode="paper")
        names = {d["function"]["name"] for d in defs}
        assert LIVE_TOOL_NAME not in names, (
            "live tool must be excluded by the check_fn-identity belt / _live_check even "
            "when _LIVE_TOOL_NAMES is cleared and the tool is force-registered"
        )
        assert "get_prices" in names  # sanity: legitimate tool still surfaces


# ---------------------------------------------------------------------------
# Reviewer finding 3 — an unknown YAML/allowlist tool name fails LOUD.
# ---------------------------------------------------------------------------

class TestUnknownYamlToolFailsLoud:
    def test_unknown_tool_in_allowlist_raises(self, monkeypatch):
        """A tool name in the allowlist but absent from the catalog (typo / stale name)
        is a safety-config error — build_registry must RAISE, not warn-and-skip, so the
        runtime tool surface can never silently drift."""
        real_fn = ts._tool_names_for_mode

        def spoof(mode, armed):
            names = set(real_fn(mode, armed))
            names.add("totally_unknown_tool")  # not in the catalog
            return names

        monkeypatch.setattr(ts, "_tool_names_for_mode", spoof)
        with pytest.raises(ValueError):
            ts.build_registry("paper", armed=False)
