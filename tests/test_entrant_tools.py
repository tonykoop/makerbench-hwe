"""Read-only measure/render tools for studio-tier entrants (#798).

Stub entrants only. No model, CLI or paid calls. Real-sandbox tests skip when
bwrap/openscad/xvfb are missing, unless ``MAKERBENCH_REQUIRE_SANDBOX=1`` (CI).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench import code_cad_generator as gen
from makerbench import code_cad_providers as providers
from makerbench import entrant_tools, entrant_tools_mcp, redaction, scad_sandbox

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_AVAILABLE = scad_sandbox.sandbox_available()
if REQUIRE_SANDBOX and not _AVAILABLE:  # pragma: no cover - CI guard
    pytest.fail("MAKERBENCH_REQUIRE_SANDBOX=1 but the OpenSCAD sandbox cannot start", pytrace=False)
needs_sandbox = pytest.mark.skipif(not _AVAILABLE, reason="OpenSCAD sandbox unavailable here")

CUBE = "cube([10, 20, 5]);\n"


@pytest.fixture
def layout(tmp_path):
    """A staged workspace, a harness dir outside it, and a host secret outside both."""
    workspace = tmp_path / "trial" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "prior.scad").write_text(CUBE, encoding="utf-8")
    trimesh.creation.box(extents=[4, 4, 4]).export(workspace / "prior.stl")
    harness = tmp_path / "harness"
    harness.mkdir()
    secret = tmp_path / "host-secret.scad"
    secret.write_text("// HOST SECRET 7f3a\ncube(99);\n", encoding="utf-8")
    return workspace, harness, secret


def _session(workspace, harness, **kw):
    return entrant_tools.ToolSession(workspace=workspace, ledger=harness / "ledger.jsonl", **kw)


# --- tier policy ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["blind", "packet", "repo", "image"])
def test_only_the_studio_tier_gets_tools(tier):
    assert entrant_tools.tools_for_tier(tier, True) == ()
    assert entrant_tools.tools_for_tier("studio", False) == ()
    assert entrant_tools.tools_for_tier("studio", True) == ("measure", "render_view")


def test_make_execute_trial_refuses_tools_off_the_studio_tier(tmp_path):
    with pytest.raises(ValueError, match="only available on the studio context tier"):
        runner.make_execute_trial(registry={"instruments": []}, run_dir=tmp_path, generators={},
                                  backend="openscad", context_tier="blind", entrant_tools=True)


# --- workspace confinement -------------------------------------------------------------------


@pytest.mark.parametrize("rel", ["../host-secret.scad", "../../host-secret.scad"])
def test_traversal_path_is_refused_before_reading(layout, rel, monkeypatch):
    workspace, harness, _secret = layout
    monkeypatch.setattr(entrant_tools.ToolSession, "_compile",
                        staticmethod(lambda *a: pytest.fail("nothing may compile")))
    out = _session(workspace, harness).call("measure", {"path": rel})
    assert out["ok"] is False and "escapes the workspace" in out["error"]
    assert "HOST SECRET" not in json.dumps(out)


def test_absolute_path_is_refused(layout):
    workspace, harness, secret = layout
    out = _session(workspace, harness).call("measure", {"path": secret.as_posix()})
    assert out["ok"] is False and "relative to the workspace" in out["error"]


def test_symlink_escape_is_refused(layout):
    workspace, harness, secret = layout
    (workspace / "innocent.scad").symlink_to(secret)
    out = _session(workspace, harness).call("render_view", {"path": "innocent.scad"})
    assert out["ok"] is False and "escapes the workspace" in out["error"]
    assert "HOST SECRET" not in json.dumps(out)


def test_ledger_inside_the_workspace_is_refused(layout):
    workspace, _harness, _secret = layout
    with pytest.raises(entrant_tools.ToolError, match="outside the entrant workspace"):
        entrant_tools.ToolSession(workspace=workspace, ledger=workspace / "ledger.jsonl")


def test_workspace_stl_is_measured_without_any_compile(layout, monkeypatch):
    workspace, harness, _ = layout
    monkeypatch.setattr(entrant_tools.ToolSession, "_compile",
                        staticmethod(lambda *a: pytest.fail("a staged mesh needs no compile")))
    out = _session(workspace, harness).call("measure", {"path": "prior.stl"})
    assert out["ok"] is True
    volume = next(m for m in out["result"]["measurements"] if m["metric"] == "volume")
    assert volume["value"] == pytest.approx(64.0)


def test_unavailable_sandbox_is_an_error_and_starts_no_process(layout, monkeypatch):
    workspace, harness, _ = layout

    def tripwire(*a, **k):
        raise AssertionError("no host process may start")

    monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: False)
    monkeypatch.setattr(subprocess, "run", tripwire)
    monkeypatch.setattr(subprocess, "Popen", tripwire)

    out = _session(workspace, harness).call("measure", {"source": CUBE})

    assert out["ok"] is False and out["error"].startswith("sandbox unavailable")


def test_budget_is_enforced_and_over_budget_calls_do_not_compile(layout, monkeypatch):
    workspace, harness, _ = layout
    compiled = []

    def fake_compile(source, backend, tmp):
        compiled.append(source)
        path = tmp / "out.stl"
        trimesh.creation.box(extents=[1, 1, 1]).export(path)
        return path

    monkeypatch.setattr(entrant_tools.ToolSession, "_compile", staticmethod(fake_compile))
    session = _session(workspace, harness, max_calls=2)
    results = [session.call("measure", {"source": CUBE}) for _ in range(3)]

    assert [r["ok"] for r in results] == [True, True, False]
    assert "budget exhausted" in results[2]["error"]
    assert len(compiled) == 2
    # A retried attempt resumes the same ledger, so the budget spans attempts.
    assert _session(workspace, harness, max_calls=2).call("measure", {"source": CUBE})["ok"] is False
    assert len(compiled) == 2


def test_every_call_is_ledgered_with_hashes_not_content(layout, monkeypatch):
    workspace, harness, _ = layout
    session = _session(workspace, harness)
    session.call("measure", {"path": "prior.stl"})
    session.call("measure", {"path": "../host-secret.scad"})
    session.call("rm_rf", {})

    calls = entrant_tools.read_ledger(harness / "ledger.jsonl")

    assert [c["seq"] for c in calls] == [1, 2, 3]
    assert [c["ok"] for c in calls] == [True, False, False]
    assert calls[0]["source_sha256"] and calls[0]["path"] == "prior.stl"
    assert calls[2]["counted"] is False and "unknown tool" in calls[2]["error"]
    raw = (harness / "ledger.jsonl").read_text()
    assert "cube(" not in raw and "HOST SECRET" not in raw


@needs_sandbox
def test_real_sandbox_source_cannot_read_host_files_and_workspace_is_untouched(layout):
    workspace, harness, secret = layout
    before = entrant_tools.copy_tree_digest(workspace)
    session = _session(workspace, harness)

    probe = f'include <{secret.as_posix()}>\ncube([1, 1, 1]);\n'
    measured = session.call("measure", {"source": probe})
    rendered = session.call("render_view", {"path": "prior.scad", "views": ["iso"],
                                            "sections": [{"axis": "z", "offset_mm": 2.0}]})

    # The include is invisible in the sandbox: only the 1 mm cube compiles.
    assert measured["ok"] is True
    bbox = next(m for m in measured["result"]["measurements"] if m["metric"] == "bbox")
    assert bbox["value"] == pytest.approx([1, 1, 1])
    assert "HOST SECRET" not in json.dumps(measured)
    assert rendered["ok"] is True and [i["name"] for i in rendered["images"]] == [
        "view-iso.png", "section-s0.png"]
    section = rendered["result"]["images"][1]
    assert section["cut_area_mm2"] == pytest.approx(200.0, rel=0.03)
    assert entrant_tools.copy_tree_digest(workspace) == before
    assert sorted(p.name for p in harness.iterdir()) == ["ledger.jsonl"]


# --- MCP server -----------------------------------------------------------------------------


def test_mcp_server_lists_exactly_the_two_tools_and_routes_calls(layout):
    workspace, harness, _ = layout
    session = _session(workspace, harness)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "measure", "arguments": {"path": "../host-secret.scad"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
    ]
    stdout = io.StringIO()
    entrant_tools_mcp.serve(session, io.StringIO("\n".join(map(json.dumps, requests)) + "\n"),
                            stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]

    assert [r["id"] for r in responses] == [1, 2, 3, 4]
    assert [t["name"] for t in responses[1]["result"]["tools"]] == ["measure", "render_view"]
    assert responses[2]["result"]["isError"] is True
    assert "escapes the workspace" in responses[2]["result"]["content"][0]["text"]
    assert responses[3]["error"]["code"] == -32601


def _serve(session, requests):
    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    stdout = io.StringIO()
    entrant_tools_mcp.serve(session, io.StringIO(lines), stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


@pytest.mark.parametrize("arguments", [5, "prior.stl", ["prior.stl"], True])
def test_non_object_arguments_are_a_ledgered_tool_error_not_an_exception(layout, arguments):
    workspace, harness, _ = layout
    out = _session(workspace, harness).call("measure", arguments)

    assert out["ok"] is False and out["error"] == "arguments must be an object"
    [call] = entrant_tools.read_ledger(harness / "ledger.jsonl")
    assert call["counted"] is False and call["tool"] == "measure" and call["path"] is None


def test_mcp_server_keeps_answering_after_malformed_params_and_arguments(layout):
    workspace, harness, _ = layout
    responses = _serve(_session(workspace, harness), [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "measure", "arguments": 5}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": "measure"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": ["measure", {}]},
        7,
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
    ])

    assert [r["id"] for r in responses] == [1, 2, 3, None, 4]
    assert responses[0]["result"]["isError"] is True
    assert "arguments must be an object" in responses[0]["result"]["content"][0]["text"]
    assert responses[1]["error"]["code"] == responses[2]["error"]["code"] == -32602
    assert responses[3]["error"]["code"] == -32600
    assert responses[4]["result"] == {}
    assert len(entrant_tools.read_ledger(harness / "ledger.jsonl")) == 1


def test_mcp_server_survives_an_unexpected_handler_exception(layout, monkeypatch):
    workspace, harness, _ = layout
    real_handle = entrant_tools_mcp.handle

    def flaky(session, message):
        if message.get("id") == 1:
            raise KeyError("unexpected")
        return real_handle(session, message)

    monkeypatch.setattr(entrant_tools_mcp, "handle", flaky)
    responses = _serve(_session(workspace, harness), [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ])

    assert responses[0] == {"jsonrpc": "2.0", "id": 1,
                            "error": {"code": -32603, "message": "internal error"}}
    assert responses[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


PROBE = "entrant-probe-5c1d"


def _hostile_paths(secret):
    return [secret.as_posix(), f"/home/{PROBE}/secret.scad", f"../../home/{PROBE}/b.scad",
            f"/tmp/{PROBE}/../etc/passwd", f"C:/Users/{PROBE}/c.scad", "../host-secret.scad"]


def test_refused_paths_and_unknown_tool_names_never_reach_the_ledger(layout):
    workspace, harness, secret = layout
    session = _session(workspace, harness)
    hostile = _hostile_paths(secret)
    for raw in hostile:
        assert session.call("measure", {"path": raw})["ok"] is False
    session.call(f"/home/{PROBE}/tool", {"path": "prior.stl"})
    session.call("measure", {"path": "prior.stl"})

    raw_ledger = (harness / "ledger.jsonl").read_text()
    assert PROBE not in raw_ledger and "host-secret" not in raw_ledger
    assert secret.as_posix() not in raw_ledger
    assert redaction.find_host_paths(raw_ledger) == []
    calls = entrant_tools.read_ledger(harness / "ledger.jsonl")
    assert all(c["path"] is None and c["path_sha256"] for c in calls[:len(hostile)])
    assert calls[-2]["tool"] is None and calls[-2]["tool_sha256"] and calls[-2]["path"] is None
    assert calls[-1]["path"] == "prior.stl" and calls[-1]["ok"] is True


def test_validated_path_is_recorded_and_host_paths_are_redacted_from_errors(layout, monkeypatch):
    workspace, harness, _ = layout

    def failing_compile(source, backend, tmp):
        raise entrant_tools.render.CompileError(f"openscad failed on {tmp}/candidate.scad")

    monkeypatch.setattr(entrant_tools.ToolSession, "_compile", staticmethod(failing_compile))
    out = _session(workspace, harness).call("measure", {"path": "prior.scad"})

    assert out["ok"] is False and redaction.find_host_paths(out["error"]) == []
    [call] = entrant_tools.read_ledger(harness / "ledger.jsonl")
    assert call["path"] == "prior.scad" and call["counted"] is True
    assert "openscad failed" in call["error"] and redaction.find_host_paths(call["error"]) == []


def test_host_paths_in_a_tool_payload_error_are_redacted_in_the_ledger(layout, monkeypatch):
    workspace, harness, _ = layout

    def failing_measure(stl, sections):
        return {"ok": False, "error": f"cannot load mesh {stl}", "measurements": []}

    monkeypatch.setattr(entrant_tools.measure, "measure_candidate", failing_measure)
    out = _session(workspace, harness).call("measure", {"path": "prior.stl"})

    assert out["ok"] is False
    [call] = entrant_tools.read_ledger(harness / "ledger.jsonl")
    assert call["error"].startswith("cannot load mesh")
    assert redaction.find_host_paths(call["error"]) == []


def test_refused_paths_leave_no_host_path_in_trial_provenance(monkeypatch, layout):
    _, _, secret = layout
    calls = [("measure", {"path": raw}) for raw in _hostile_paths(secret)]
    calls += [(f"/home/{PROBE}/tool", {}), ("render_view", 5), ("measure", {"path": "prior.stl"})]

    _, provenance = _capture_claude(monkeypatch, layout, "studio", calls=calls)

    block = json.dumps(provenance["tools"])
    assert PROBE not in block and secret.as_posix() not in block
    assert redaction.find_host_paths(block) == []
    recorded = provenance["tools"]["calls"]
    assert [c["path"] for c in recorded] == [None] * (len(calls) - 1) + ["prior.stl"]
    # A refused path still spends a counted call (probing isn't free); an unknown tool or
    # non-object arguments are refused before the budget is touched.
    assert provenance["tools"]["calls_used"] == len(_hostile_paths(secret)) + 1


# --- Claude wiring --------------------------------------------------------------------------


def _request(tier, workspace, tools=entrant_tools.TOOL_NAMES):
    return gen.GenerationRequest(model_id="claude-code-sonnet", instrument_id="lyre", seed=0,
                                 spec={}, prompt="p", prompt_sha256="x", context_tier=tier,
                                 workspace_dir=str(workspace) if workspace else None,
                                 entrant_tools=tuple(tools))


def _capture_claude(monkeypatch, layout, tier, tools=entrant_tools.TOOL_NAMES, *, tool_calls=0,
                    calls=None):
    workspace, harness, _ = layout
    seen: dict = {}

    def fake_run_cli(cmd, *, timeout_s, cwd, **kw):
        seen["cmd"] = list(cmd)
        config = next((a.split("=", 1)[1] for a in cmd if a.startswith("--mcp-config=")), None)
        if config:
            seen["config"] = json.loads(Path(config).read_text())
            server = seen["config"]["mcpServers"]["makerbench"]["args"]
            ledger = Path(server[server.index("--ledger") + 1])
            session = entrant_tools.ToolSession(workspace=workspace, ledger=ledger)
            for name, arguments in calls or [("measure", {"path": "prior.stl"})] * tool_calls:
                session.call(name, arguments)
        payload = {"result": "```scad\ncube(1);\n```", "is_error": False}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(providers, "_run_cli", fake_run_cli)
    generator = providers.make_claude_generator(retry_sleep_s=0)
    results = gen.run_generation_batch(
        registry={"instruments": [{"id": "lyre"}]}, instrument_id="lyre", seed=0,
        model_ids=["claude-code-sonnet"], generator=generator, out_dir=harness / "gen",
        context_tier=tier, workspace_dir=workspace if tier != "blind" else None,
        entrant_tools=tools,
    )
    provenance = json.loads(results[0].provenance_path.read_text())
    return seen, provenance


def test_blind_claude_gets_no_tools_and_no_mcp_config(monkeypatch, layout):
    seen, provenance = _capture_claude(monkeypatch, layout, "blind", tools=())
    assert "--tools=" in seen["cmd"]
    assert not any(a.startswith("--mcp-config") for a in seen["cmd"])
    assert provenance["tools"]["enabled"] == [] and provenance["tools"]["transport"] == "none"


def test_studio_claude_without_opt_in_keeps_read_only_tools(monkeypatch, layout):
    seen, provenance = _capture_claude(monkeypatch, layout, "studio", tools=())
    assert "--tools=Read,Glob,Grep" in seen["cmd"]
    assert not any(a.startswith("--mcp-config") for a in seen["cmd"])
    assert provenance["tools"]["enabled"] == []


def test_studio_claude_argv_allow_lists_exactly_the_mcp_tools(monkeypatch, layout):
    workspace, harness, _ = layout
    seen, provenance = _capture_claude(monkeypatch, layout, "studio", tool_calls=2)

    cmd = seen["cmd"]
    assert "--tools=Read,Glob,Grep,mcp__makerbench__measure,mcp__makerbench__render_view" in cmd
    assert "--strict-mcp-config" in cmd and "--restricted" in cmd
    assert sum(a.startswith("--mcp-config=") for a in cmd) == 1
    assert list(seen["config"]["mcpServers"]) == ["makerbench"]
    server_args = seen["config"]["mcpServers"]["makerbench"]["args"]
    assert server_args[server_args.index("--workspace") + 1] == workspace.resolve().as_posix()
    ledger = Path(server_args[server_args.index("--ledger") + 1])
    assert not ledger.is_relative_to(workspace.resolve())

    tools = provenance["tools"]
    assert tools["enabled"] == ["measure", "render_view"]
    assert tools["transport"] == "claude-mcp-stdio"
    assert tools["calls_used"] == 2 and [c["tool"] for c in tools["calls"]] == ["measure"] * 2
    assert not ledger.parent.exists(), "harness tool dir is cleaned up after the trial"


def test_codex_request_with_tools_records_not_wired_instead_of_offering_them(tmp_path):
    request = _request("studio", tmp_path, entrant_tools.TOOL_NAMES)

    record = gen._tools_record(request, generator=lambda r: "")

    assert record["enabled"] == [] and record["transport"] == "none"
    assert "not wired" in record["reason"]
