"""Sandbox parity for studio-tier entrant tools, one backend at a time (#858, epic #853).

#824 was a build123d entrant whose inline tool calls were silently compiled in
the OpenSCAD sandbox. These tests apply the same regression check to every
backend the tool server accepts (OpenSCAD, CadQuery, build123d). Each studio-tier
``measure`` / ``render_view`` call must reach that backend's own sandboxed
compiler, the one the arena grades with. It must run under Bubblewrap with the
same isolation flags and the right interpreter, and it must never fall back to
another backend's sandbox.

Stub entrants and fake sandboxes only. No model, CLI or paid call, and no real
CadQuery or build123d install is needed. The single real-sandbox smoke test
skips unless the sandbox and its runtime are present.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import trimesh

from makerbench import build123d_backend, cadquery_backend
from makerbench import code_cad_arena_runner as runner
from makerbench import code_cad_generator as gen
from makerbench import code_cad_providers as providers
from makerbench import entrant_tools, entrant_tools_mcp, scad_sandbox

SOURCES = {
    "openscad": "cube([10, 20, 5]);\n",
    "cadquery": "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\n",
    "build123d": "from build123d import Box\nresult = Box(10, 20, 5)\n",
}
FENCES = {"openscad": "scad", "cadquery": "python", "build123d": "python"}
BACKENDS = tuple(entrant_tools.BACKENDS)
TOOLS = ("measure", "render_view")
#: Bubblewrap flags every entrant-code sandbox must pass, whatever the backend.
CONFINEMENT_FLAGS = ("--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid",
                     "--unshare-ipc", "--unshare-uts", "--unshare-net")
FAKE_BIN = "/fake-sandbox-bin"


def test_parity_covers_every_sandboxed_arena_backend():
    """A backend the arena can grade in a sandbox must also be one the tool server routes."""
    assert set(BACKENDS) == set(runner.SANDBOXED_BACKEND_COMPILERS) == set(SOURCES)


@pytest.fixture
def layout(tmp_path):
    workspace = tmp_path / "trial" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "draft.scad").write_text(SOURCES["openscad"], encoding="utf-8")
    (workspace / "draft.py").write_text(SOURCES["cadquery"], encoding="utf-8")
    harness = tmp_path / "harness"
    harness.mkdir()
    return workspace, harness


def _session(layout, backend):
    workspace, harness = layout
    return entrant_tools.ToolSession(workspace=workspace, ledger=harness / "ledger.jsonl",
                                     backend=backend)


def _stub_render(monkeypatch):
    monkeypatch.setattr(entrant_tools.render_view, "render_mesh_views",
                        lambda *a, **kw: SimpleNamespace(images=[], to_dict=lambda: {"ok": True}))


# --- layer 1: dispatch reaches the backend's own arena sandbox compiler --------------------


@pytest.fixture
def lane_spy(monkeypatch):
    """Replace each sandboxed arena compiler with a spy that records which lane ran."""
    calls: list[tuple[str, str]] = []

    def spy(lane):
        def compile_box(src, out):
            calls.append((lane, Path(src).read_text(encoding="utf-8")))
            Path(out).mkdir(parents=True, exist_ok=True)
            stl = Path(out) / "box.stl"
            trimesh.creation.box(extents=[10, 20, 5]).export(stl)
            return SimpleNamespace(stl_path=stl)
        return compile_box

    monkeypatch.setattr(scad_sandbox, "compile_scad_sandboxed", spy("openscad"))
    monkeypatch.setattr(cadquery_backend, "compile_cadquery_to_artifacts", spy("cadquery"))
    monkeypatch.setattr(build123d_backend, "compile_build123d_to_artifacts", spy("build123d"))
    return calls


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("backend", BACKENDS)
def test_inline_source_uses_the_session_backends_own_lane(layout, monkeypatch, lane_spy,
                                                          backend, tool):
    _stub_render(monkeypatch)
    workspace, _ = layout
    before = entrant_tools.copy_tree_digest(workspace)
    session = _session(layout, backend)
    result = session.call(tool, {"source": SOURCES[backend]})
    assert result["ok"] is True, result
    if tool == "measure":
        volume = next(m for m in result["result"]["measurements"] if m["metric"] == "volume")
        assert volume["value"] == pytest.approx(1000.0)
    assert lane_spy == [(backend, SOURCES[backend])]
    assert entrant_tools.read_ledger(session.ledger)[0]["backend"] == backend
    assert entrant_tools.copy_tree_digest(workspace) == before


@pytest.mark.parametrize("session_backend", BACKENDS)
@pytest.mark.parametrize("backend", BACKENDS)
def test_explicit_backend_argument_wins_over_the_session_default(layout, monkeypatch, lane_spy,
                                                                 backend, session_backend):
    session = _session(layout, session_backend)
    result = session.call("measure", {"source": SOURCES[backend], "backend": backend})
    assert result["ok"] is True, result
    assert lane_spy == [(backend, SOURCES[backend])]
    assert entrant_tools.read_ledger(session.ledger)[0]["backend"] == backend


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(("path", "lane"), [("draft.scad", "openscad"), ("draft.py", "cadquery")])
def test_workspace_path_routes_by_suffix_in_every_session(layout, lane_spy, backend, path, lane):
    """A workspace file is routed by its suffix, never by a mismatched session backend.

    ``.py`` keeps the generic CadQuery worker routing that #824 kept on purpose.
    The build123d lane is that same Bubblewrap worker plus an import check (see
    the confinement tests below), so a ``.py`` file never reaches OpenSCAD.
    """
    session = _session(layout, backend)
    assert session.call("measure", {"path": path})["ok"] is True
    workspace, _ = layout
    assert lane_spy == [(lane, (workspace / path).read_text(encoding="utf-8"))]
    ledger = entrant_tools.read_ledger(session.ledger)[0]
    assert (ledger["backend"], ledger["path"]) == (lane, path)


# --- layer 2: the lane really runs Bubblewrap with the right interpreter -------------------


@pytest.fixture
def fake_sandbox(monkeypatch):
    """Fake bwrap/openscad/xvfb so argv is built for real, then record each sandbox launch.

    Availability probes (``... /usr/bin/true``) succeed. Every real launch is
    recorded together with the entrant source it bind-mounts, then exits as a
    candidate failure, so no compile runs and nothing is executed on the host.
    """
    launches: list[dict] = []
    real_which = shutil.which

    def which(name, *a, **kw):
        if name in ("bwrap", "openscad", "xvfb-run"):
            return f"{FAKE_BIN}/{name}"
        return real_which(name, *a, **kw)

    def run(cmd, *a, **kw):
        cmd = [str(c) for c in cmd]
        if cmd[-1] == "/usr/bin/true":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        binds = {cmd[i + 2]: cmd[i + 1] for i, a_ in enumerate(cmd[:-2]) if a_ == "--ro-bind"}
        if "/work/entrant.py" in binds:
            source = Path(binds["/work/entrant.py"]).read_text(encoding="utf-8")
        else:
            source = (Path(binds["/work"]) / scad_sandbox.SOURCE_NAME).read_text(encoding="utf-8")
        launches.append({"argv": cmd, "source": source})
        return subprocess.CompletedProcess(cmd, 1, "", "fake sandbox: candidate failed\n")

    def no_popen(*a, **kw):
        pytest.fail("entrant tools must not spawn an unrecorded process")

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", no_popen)
    return launches


def _assert_confined(argv):
    assert argv[0] == f"{FAKE_BIN}/bwrap", argv
    for flag in CONFINEMENT_FLAGS:
        assert flag in argv, (flag, argv)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("backend", BACKENDS)
def test_each_backend_launches_its_own_confined_interpreter(layout, fake_sandbox, backend, tool):
    session = _session(layout, backend)
    result = session.call(tool, {"source": SOURCES[backend]})
    assert result["ok"] is False  # the fake sandbox fails every candidate
    assert "sandbox unavailable" not in result["error"], result["error"]
    assert len(fake_sandbox) == 1, [launch["argv"] for launch in fake_sandbox]
    argv, source = fake_sandbox[0]["argv"], fake_sandbox[0]["source"]
    _assert_confined(argv)
    assert source == SOURCES[backend]
    if backend == "openscad":
        assert f"{FAKE_BIN}/openscad" in argv
        assert sys.executable not in argv and "/work/entrant.py" not in argv
    else:
        tail = argv[argv.index("--chdir"):]
        assert tail[:4] == ["--chdir", "/work", sys.executable, "/work/driver.py"]
        assert tail[4] == "/work/entrant.py"
        assert not any(arg.endswith("/openscad") for arg in argv)
    assert entrant_tools.read_ledger(session.ledger)[0]["backend"] == backend


@pytest.mark.parametrize(("backend", "foreign"), [
    ("build123d", "cadquery"), ("build123d", "openscad"),
])
def test_build123d_lane_refuses_foreign_source_before_any_sandbox(layout, fake_sandbox,
                                                                  backend, foreign):
    result = _session(layout, backend).call("measure", {"source": SOURCES[foreign]})
    assert result["ok"] is False
    assert "requires a script that imports build123d" in result["error"]
    assert fake_sandbox == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_unavailable_sandbox_never_falls_back(layout, monkeypatch, backend):
    """With no bwrap, every backend reports an environment error and starts no process."""
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: None)

    def tripwire(*a, **kw):
        pytest.fail("no process may start without a sandbox")

    monkeypatch.setattr(subprocess, "run", tripwire)
    monkeypatch.setattr(subprocess, "Popen", tripwire)
    session = _session(layout, backend)
    result = session.call("measure", {"source": SOURCES[backend]})
    assert result["ok"] is False and "sandbox unavailable" in result["error"]
    assert entrant_tools.read_ledger(session.ledger)[0]["backend"] == backend


# --- layer 3: a studio trial wires its backend through MCP config and provenance -----------


def _studio_trial(monkeypatch, layout, backend, *, tier="studio",
                  tools=entrant_tools.TOOL_NAMES):
    workspace, harness = layout
    seen: dict = {}

    def fake_run_cli(cmd, *, timeout_s, cwd, **kw):
        seen["cmd"] = list(cmd)
        config = next((a.split("=", 1)[1] for a in cmd if a.startswith("--mcp-config=")), None)
        if config:
            seen["config"] = json.loads(Path(config).read_text())
            server = seen["config"]["mcpServers"][entrant_tools_mcp.SERVER_NAME]["args"]
            # Stand up the session exactly as `python -m makerbench.entrant_tools_mcp` would.
            sessions: list = []
            monkeypatch.setattr(entrant_tools_mcp, "serve",
                                lambda session, *a: sessions.append(session))
            assert entrant_tools_mcp.main(server[server.index("--workspace"):]) == 0
            sessions[0].call("measure", {"source": SOURCES[backend]})
        payload = {"result": f"```{FENCES[backend]}\n{SOURCES[backend]}```", "is_error": False}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(providers, "_run_cli", fake_run_cli)
    generator = providers.make_claude_generator(retry_sleep_s=0, backend=backend)
    results = gen.run_generation_batch(
        registry={"instruments": [{"id": "lyre"}]}, instrument_id="lyre", seed=0,
        model_ids=["claude-code-sonnet"], generator=generator, out_dir=harness / "gen",
        context_tier=tier, workspace_dir=workspace, entrant_tools=tools,
    )
    return seen, json.loads(results[0].provenance_path.read_text())


@pytest.mark.parametrize("backend", BACKENDS)
def test_studio_trial_routes_tool_calls_to_its_backend(monkeypatch, layout, lane_spy, backend):
    seen, provenance = _studio_trial(monkeypatch, layout, backend)
    args = seen["config"]["mcpServers"][entrant_tools_mcp.SERVER_NAME]["args"]
    assert args[args.index("--backend") + 1] == backend
    assert lane_spy == [(backend, SOURCES[backend])]
    calls = provenance["tools"]["calls"]
    assert [(c["backend"], c["ok"]) for c in calls] == [(backend, True)]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("tier", ["blind", "packet", "repo", "image"])
def test_non_studio_tiers_get_no_tool_server_for_any_backend(monkeypatch, layout, backend, tier):
    seen, provenance = _studio_trial(monkeypatch, layout, backend, tier=tier, tools=())
    assert not any(a.startswith("--mcp-config") for a in seen["cmd"])
    assert provenance["tools"]["enabled"] == []


@pytest.mark.parametrize("backend", sorted(set(runner.BACKEND_COMPILERS) - set(BACKENDS)))
def test_backends_without_a_tool_sandbox_are_refused_not_coerced(monkeypatch, layout, backend):
    """Blender/SolidWorks/Fusion have no tool sandbox. They must not borrow OpenSCAD's (#824)."""
    workspace, harness = layout
    monkeypatch.setattr(providers, "_run_cli",
                        lambda *a, **kw: pytest.fail("must not launch the entrant"))
    request = gen.GenerationRequest(model_id="claude-code-sonnet", instrument_id="lyre", seed=0,
                                    spec={}, prompt="p", prompt_sha256="x", context_tier="studio",
                                    workspace_dir=str(workspace),
                                    entrant_tools=entrant_tools.TOOL_NAMES)
    with pytest.raises(RuntimeError, match="entrant tool backend must be one of"):
        providers.claude_tools_args(request, harness, backend=backend)
    assert not (harness / "mcp.json").exists()
    with pytest.raises(RuntimeError, match="entrant tool backend must be one of"):
        providers.make_claude_generator(backend=backend, retry_sleep_s=0)(request)


# --- optional real smoke test ---------------------------------------------------------------


@pytest.mark.skipif(not scad_sandbox.sandbox_available(),
                    reason="OpenSCAD sandbox unavailable here")
def test_real_openscad_tool_call_measures_inside_bubblewrap(layout):
    session = _session(layout, "openscad")
    result = session.call("measure", {"source": SOURCES["openscad"]})
    assert result["ok"] is True, result
    volume = next(m for m in result["result"]["measurements"] if m["metric"] == "volume")
    assert volume["value"] == pytest.approx(1000.0, rel=1e-3)


@pytest.mark.skipif(not (cadquery_backend.cadquery_available()
                         and cadquery_backend.build123d_available()
                         and cadquery_backend._bubblewrap_available(
                             cadquery_backend._scrub_environment(os.environ))),
                    reason="CadQuery/build123d runtime or Bubblewrap unavailable here")
@pytest.mark.parametrize("backend", ["cadquery", "build123d"])
def test_real_python_lane_tool_call_measures_inside_bubblewrap(layout, backend):
    session = _session(layout, backend)
    result = session.call("measure", {"source": SOURCES[backend]})
    assert result["ok"] is True, result
    volume = next(m for m in result["result"]["measurements"] if m["metric"] == "volume")
    assert volume["value"] == pytest.approx(1000.0, rel=1e-3)
