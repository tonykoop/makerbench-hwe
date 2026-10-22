"""#1102: CADAM route selection (openrouter | cli-bridge) in the nightly runner and its preflight (fakes only)."""

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import pytest
import trimesh

from makerbench import cadam_routes
from makerbench import code_cad_arena_runner as arena_runner
from makerbench import nightly_cad
from makerbench.cadam_adapter import CadamClient
from makerbench.code_cad_objective import RenderArtifacts
from makerbench.nightly_cad import NightlyEntrant, NightlyExecutor
from makerbench.nightly_preflight import audit_routes, build_report, render_report_lines
from makerbench.run_log_io import atomic_write_json

from test_nightly_cad import _image, _registry  # noqa: E402

NOW = datetime(2026, 7, 19, 7, 30, tzinfo=timezone.utc)
KEY = "bridge-shared-key-0123456789"
SECRET = "or-key-SECRET-VALUE-12345"


# ---- entrant config -----------------------------------------------------------------------


def test_defaults_labels_and_roundtrip():
    default = NightlyEntrant.from_dict({"entrant_id": "c", "kind": "cadam", "model_id": "anthropic/claude-fable-5.1"})
    assert default.route == "openrouter" and default.runtime == ""
    assert default.route_label == "CADAM · OpenRouter · anthropic/claude-fable-5.1"
    bridge = NightlyEntrant.from_dict({"entrant_id": "b", "kind": "cadam", "model_id": "openai/gpt-6.1-sol", "route": "cli-bridge", "runtime": "codex"})
    assert bridge.route_label == "CADAM via CLI · codex · openai/gpt-6.1-sol"
    assert NightlyEntrant.from_dict(bridge.__dict__) == bridge
    assert NightlyEntrant(entrant_id="a", kind="arena", model_id="m").route_label is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"kind": "cadam", "route": "carrier-pigeon"}, "unsupported CADAM route"),
        ({"kind": "cadam", "route": "cli-bridge"}, "runtime claude or codex"),
        ({"kind": "cadam", "route": "cli-bridge", "runtime": "gemini"}, "runtime claude or codex"),
        ({"kind": "cadam", "route": "openrouter", "runtime": "claude"}, "cli-bridge route only"),
        ({"kind": "arena", "route": "cli-bridge", "runtime": "claude"}, "cadam entrants only"),
        ({"kind": "arena", "runtime": "claude"}, "cadam entrants only"),
    ],
)
def test_invalid_route_combinations_are_rejected(payload, message):
    with pytest.raises(ValueError, match=message):
        NightlyEntrant.from_dict({"entrant_id": "e", "model_id": "m", **payload}).validate()


# ---- running both routes ------------------------------------------------------------------

FAKE_BRIDGE = '''#!{py}
import http.server, os, sys
from pathlib import Path
here = Path(__file__).parent
(here / "pid").write_text(str(os.getpid()))
(here / "key_seen").write_text(os.environ.get("CADAM_CLI_BRIDGE_KEY", ""))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def _free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def world(tmp_path, monkeypatch):
    port = _free_port()
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    fake = bridge_dir / "fake_bridge.py"
    fake.write_text(FAKE_BRIDGE.format(py=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "bridge.json"
    config.write_text(json.dumps({"host": "127.0.0.1", "port": port, "claude_bin": "claude", "codex_bin": "codex",
                                  "models": {"anthropic/claude-sonnet-5.5": {"runtime": "claude", "cli_model": "claude-sonnet-5-5"},
                                             "openai/gpt-6.1-sol": {"runtime": "codex", "cli_model": "gpt-6.1-sol"}}}))
    for k, v in {"CADAM_CLI_BRIDGE_CONFIG": str(config), "CADAM_CLI_BRIDGE_KEY": KEY, "CADAM_CLI_BRIDGE_CMD": f"{fake} {port}",
                 "CADAM_USER_ID": "u", "SUPABASE_SERVICE_ROLE_KEY": "service-role-key-0000", "CADAM_ACCESS_TOKEN": "tok-000000000000",
                 "CADAM_BASE_URL": "http://or.cadam.test", "CADAM_BRIDGE_BASE_URL": "http://bridge.cadam.test"}.items():
        monkeypatch.setenv(k, v)
    seen = []

    def factory(config_):
        record = {"base_url": config_.base_url, "bridge_up": None}
        seen.append(record)

        def transport(method, url, headers, body):
            if record["bridge_up"] is None:  # the first call of the turn: was the bridge listening?
                record["bridge_up"] = cadam_routes.default_http_get(f"http://127.0.0.1:{port}/health") == 200
            if method == "GET" and "/rest/v1/messages?" in url:
                started = any("/api/parametric-chat" in u for u in urls)
                return json.dumps([{"id": "a", "metadata": {"billingTokens": 3}, "parts": [
                    {"type": "tool-build_parametric_model", "input": {"code": "cube([40,20,10]);"}}]}] if started else []).encode()
            urls.append(url)
            return b"{}"

        urls = []
        return CadamClient(config_, transport=transport, sleep_fn=lambda _s: None)

    monkeypatch.setattr(nightly_cad, "CadamClient", factory)

    def compiler(_scad, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        trimesh.creation.box(extents=[30, 20, 10]).export(out_dir / "output.stl")
        (out_dir / "preview.png").write_bytes(b"\x89PNG\r\n")
        return RenderArtifacts(stl_path=out_dir / "output.stl", png_path=out_dir / "preview.png")

    monkeypatch.setattr(arena_runner, "ingest_candidate", partial(arena_runner.ingest_candidate, compiler=compiler))
    monkeypatch.setattr(cadam_routes, "cli_version", lambda binary, run=None: f"{binary} 9.9.9 (fake)")

    class World:
        pass

    w = World()
    w.tmp, w.port, w.seen, w.bridge_dir = tmp_path, port, seen, bridge_dir

    def run(entrants):
        queue = tmp_path / "queue.json"
        atomic_write_json(queue, {"schema": "makerbench-nightly-cad-queue-v1", "jobs": [{
            "job_id": "night", "instrument_id": "sambuca", "reference_image": _image(tmp_path / "ref.png").as_posix(),
            "budget_usd": 5.0, "entrants": entrants}]})
        executor = NightlyExecutor(queue_path=queue, registry_path=_registry(tmp_path / "registry.json"),
                                   output_root=tmp_path / "runs", now=lambda: NOW)
        return executor.run()

    w.run = run
    return w


def _row(result):
    log = json.loads((Path(result["run_dir"]) / "run_log.json").read_text())
    (row,) = log["trials"]
    return row, json.loads(Path(row["result"]["gen"]["provenance_path"]).read_text())


def test_openrouter_route_uses_the_stock_cadam_and_never_starts_the_bridge(world):
    result = world.run([{"entrant_id": "cadam-or", "kind": "cadam", "model_id": "anthropic/claude-fable-5.1", "max_cost_usd": 1.0},
                        {"entrant_id": "cadam-or2", "kind": "cadam", "model_id": "anthropic/claude-fable-5.1", "max_cost_usd": 1.0}])
    assert {s["base_url"] for s in world.seen} == {"http://or.cadam.test"}
    assert not (world.bridge_dir / "pid").exists()
    row, prov = _row_for(result, "cadam-or")
    assert prov["route"] == "openrouter" and prov["entrant_label"] == "CADAM · OpenRouter · anthropic/claude-fable-5.1"
    assert "runtime" not in prov and "cli_version" not in prov
    # entrant_label keeps the configured id; meta.route is #1131's recorded route label (pretty model name)
    assert row["meta"]["entrant_label"] == "CADAM · OpenRouter · anthropic/claude-fable-5.1"
    assert row["meta"]["route"] == "CADAM · OpenRouter · Fable 5.1"


def _row_for(result, entrant):
    log = json.loads((Path(result["run_dir"]) / "run_log.json").read_text())
    row = next(r for r in log["trials"] if r["model_id"] == entrant)
    return row, json.loads(Path(row["result"]["gen"]["provenance_path"]).read_text())


def test_cli_bridge_route_starts_the_bridge_for_the_turn_and_stops_it_after(world):
    result = world.run([
        {"entrant_id": "cadam-cli-claude", "kind": "cadam", "model_id": "anthropic/claude-sonnet-5.5", "route": "cli-bridge", "runtime": "claude"},
        {"entrant_id": "cadam-or", "kind": "cadam", "model_id": "anthropic/claude-fable-5.1"},
    ])
    bridge_call = next(s for s in world.seen if s["base_url"] == "http://bridge.cadam.test")
    assert bridge_call["bridge_up"] is True  # the bridge was listening when the CADAM turn began
    assert next(s for s in world.seen if s["base_url"] == "http://or.cadam.test")["bridge_up"] is False  # and is gone for the next entrant
    assert (world.bridge_dir / "key_seen").read_text() == KEY
    pid = int((world.bridge_dir / "pid").read_text())
    with pytest.raises(OSError):
        os.kill(pid, 0)  # stopped
    row, prov = _row_for(result, "cadam-cli-claude")
    assert prov["route"] == "cli-bridge" and prov["runtime"] == "claude"
    assert prov["cli_version"] == "claude 9.9.9 (fake)" and prov["cli_model"] == "claude-sonnet-5-5"
    assert prov["entrant_label"] == "CADAM via CLI · claude · anthropic/claude-sonnet-5.5"
    assert row["meta"]["route"] == "CADAM via CLI · claude · Sonnet 5.5"  # recorded by #1131's _record_route, route-aware
    labels = {_row_for(result, e)[0]["meta"]["entrant_label"] for e in ("cadam-cli-claude", "cadam-or")}
    assert len(labels) == 2  # two routes, two entrants, never merged
    assert KEY not in json.dumps(prov)


def test_an_already_listening_bridge_is_reused_and_left_running(world):
    proc = subprocess.Popen([str(world.bridge_dir / "fake_bridge.py"), str(world.port)])
    try:
        for _ in range(50):
            if cadam_routes.default_http_get(f"http://127.0.0.1:{world.port}/health") == 200:
                break
            import time

            time.sleep(0.1)
        os.environ["CADAM_CLI_BRIDGE_CMD"] = "/bin/false"  # would fail if the runner tried to spawn its own
        world.run([{"entrant_id": "c", "kind": "cadam", "model_id": "openai/gpt-6.1-sol", "route": "cli-bridge", "runtime": "codex"},
                   {"entrant_id": "d", "kind": "cadam", "model_id": "openai/gpt-6.1-sol", "route": "cli-bridge", "runtime": "codex"}])
        assert proc.poll() is None and cadam_routes.default_http_get(f"http://127.0.0.1:{world.port}/health") == 200
    finally:
        proc.terminate()
        proc.wait()


def test_a_bridge_that_cannot_start_fails_the_entrant_without_calling_cadam(world, monkeypatch):
    monkeypatch.setenv("CADAM_CLI_BRIDGE_CMD", "/bin/false")
    result = world.run([{"entrant_id": "c", "kind": "cadam", "model_id": "anthropic/claude-sonnet-5.5", "route": "cli-bridge", "runtime": "claude"},
                        {"entrant_id": "d", "kind": "cadam", "model_id": "anthropic/claude-sonnet-5.5", "route": "cli-bridge", "runtime": "claude"}])
    assert [o["status"] for o in result["outcomes"]] == ["error", "error"]
    assert "bridge" in result["outcomes"][0]["error"]
    assert all(call["bridge_up"] is None for call in world.seen)  # CADAM never received a request: the failure came first


def test_missing_bridge_configuration_is_a_clear_entrant_error(world, monkeypatch):
    monkeypatch.delenv("CADAM_CLI_BRIDGE_CONFIG")
    result = world.run([{"entrant_id": "c", "kind": "cadam", "model_id": "anthropic/claude-sonnet-5.5", "route": "cli-bridge", "runtime": "claude"},
                        {"entrant_id": "d", "kind": "cadam", "model_id": "anthropic/claude-sonnet-5.5"}])
    assert result["outcomes"][0]["status"] == "error" and "CADAM_CLI_BRIDGE_CONFIG" in result["outcomes"][0]["error"]
    assert result["outcomes"][1]["status"] == "complete"  # the other route is unaffected


# ---- preflight ----------------------------------------------------------------------------


@pytest.fixture
def pre(tmp_path):
    cadam = tmp_path / "CADAM" / "src" / "lib"
    cadam.mkdir(parents=True)
    (cadam / "utils.ts").write_text("export const models = [\n  {\n    id: 'anthropic/claude-fable-5.1',\n  },\n  {\n    id: 'anthropic/claude-sonnet-5.5',\n  },\n];\n")
    server = cadam.parent / "server"
    server.mkdir()
    (server / "aiChat.ts").write_text("""function buildChatModel() {
  if (providerFor(modelId) === 'openrouter') {
    return {
      model: providers.openrouter().chat(modelId, {
        // Prefer Anthropic; allow other hosts with auto tool choice.
        ...(modelId.startsWith('anthropic/')
          ? { provider: { order: ['Anthropic'], allow_fallbacks: true } }
          : {}),
        ...(thinking ? { reasoning: { max_tokens: thinkingBudget } } : {}),
        usage: { include: true },
      }),
    };
  }
}
""")
    config = tmp_path / "bridge.json"
    config.write_text(json.dumps({"port": 8787, "models": {"anthropic/claude-sonnet-5.5": {"runtime": "claude", "cli_model": "claude-sonnet-5-5"}}}))
    repo = tmp_path / "repo"
    (repo / "tools" / "cadam_cli_bridge").mkdir(parents=True)
    (repo / "tools" / "cadam_cli_bridge" / "__init__.py").write_text("")

    def queue(*entrants):
        path = tmp_path / "queue.json"
        atomic_write_json(path, {"jobs": [{"job_id": "j", "status": "queued", "entrants": list(entrants)}]})
        return path

    class P:
        pass

    p = P()
    p.tmp, p.queue, p.repo, p.config = tmp_path, queue, repo, config
    p.entries = {"OPENROUTER_API_KEY": SECRET, "CADAM_DIR": str(tmp_path / "CADAM"), "CADAM_CLI_BRIDGE_CONFIG": str(config),
                 "CADAM_CLI_BRIDGE_KEY": KEY}

    def audit(*entrants, entries=None, run=None, http=None, which=None):
        return audit_routes(queue(*entrants), entries if entries is not None else p.entries, environ={},
                            run=run or (lambda argv: (0, "tool 1.0\n")), http_get=http or (lambda url: 200 if "8787" in url or "3001" in url else 0),
                            which=which or (lambda name: "/usr/bin/" + name), repo_root=repo)

    p.audit = audit
    return p


OR = {"entrant_id": "a", "kind": "cadam", "model_id": "anthropic/claude-fable-5.1"}
CLI = {"entrant_id": "b", "kind": "cadam", "model_id": "anthropic/claude-sonnet-5.5", "route": "cli-bridge", "runtime": "claude"}


def _failed(statuses):
    return [text for status in statuses for ok, text in status.checks if not ok]


def test_both_routes_pass_when_everything_is_in_place(pre):
    statuses = pre.audit(OR, CLI)
    assert [s.label for s in statuses] == ["CADAM · OpenRouter · anthropic/claude-fable-5.1", "CADAM via CLI · claude · anthropic/claude-sonnet-5.5"]
    assert all(s.ok for s in statuses), _failed(statuses)


def test_non_cadam_and_finished_jobs_are_not_audited(pre):
    assert pre.audit({"entrant_id": "x", "kind": "arena", "model_id": "m"}) == ()


def test_openrouter_route_failures(pre):
    assert any("OPENROUTER_API_KEY: MISSING" in t for t in _failed(pre.audit(OR, entries={**pre.entries, "OPENROUTER_API_KEY": ""})))
    assert any("OPENROUTER_API_KEY: PLACEHOLDER" in t for t in _failed(pre.audit(OR, entries={**pre.entries, "OPENROUTER_API_KEY": "changeme"})))
    unlisted = {**OR, "model_id": "anthropic/claude-nope-9"}
    assert any("NOT in CADAM's picker" in t for t in _failed(pre.audit(unlisted)))
    assert any("clone or model catalog not found" in t for t in _failed(pre.audit(OR, entries={**pre.entries, "CADAM_DIR": str(pre.tmp / "none")})))


@pytest.mark.parametrize(
    ("kwargs", "entrant", "expected"),
    [
        ({"entries": "no-key"}, CLI, "CADAM_CLI_BRIDGE_KEY: MISSING"),
        ({"entries": "no-config"}, CLI, "CADAM_CLI_BRIDGE_CONFIG does not point"),
        ({}, {**CLI, "model_id": "anthropic/unmapped"}, "is not mapped in the bridge config"),
        ({}, {**CLI, "runtime": "codex"}, "mapped to runtime claude, not codex"),
        ({"which": lambda name: None}, CLI, "CLI (claude) not found"),
        ({"run": lambda argv: (1, "")}, CLI, "NOT logged in"),
        ({"http": lambda url: 0}, CLI, "bridge-mode CADAM is not reachable"),
    ],
)
def test_cli_bridge_route_failures_are_specific(pre, kwargs, entrant, expected):
    entries = pre.entries
    if kwargs.get("entries") == "no-key":
        entries = {k: v for k, v in entries.items() if k != "CADAM_CLI_BRIDGE_KEY"}
    elif kwargs.get("entries") == "no-config":
        entries = {k: v for k, v in entries.items() if k != "CADAM_CLI_BRIDGE_CONFIG"}
    args = {k: v for k, v in kwargs.items() if k != "entries"}
    failed = _failed(pre.audit(entrant, entries=entries, **args))
    assert any(expected in text for text in failed), failed


def test_bridge_missing_and_not_listening_is_reported(pre):
    (pre.repo / "tools" / "cadam_cli_bridge" / "__init__.py").unlink()
    failed = _failed(pre.audit(CLI, http=lambda url: 200 if "3001" in url else 0))
    assert any("tools/cadam_cli_bridge is missing" in t for t in failed)


def test_report_goes_no_go_on_a_failed_route_and_prints_no_secret(pre):
    secrets = pre.tmp / "secrets.env"
    secrets.write_text(
        f"CADAM_USER_ID=u\nCADAM_ACCESS_TOKEN={SECRET}\nSUPABASE_SERVICE_ROLE_KEY={SECRET}\nOPENROUTER_API_KEY={SECRET}\n"
        f"CADAM_CLI_BRIDGE_KEY={KEY}\nCADAM_CLI_BRIDGE_CONFIG={pre.config}\nCADAM_DIR={pre.tmp / 'CADAM'}\n")
    report = build_report(
        secrets_path=secrets, lock_path=pre.tmp / ".lock", queue_path=pre.queue(OR, {**CLI, "model_id": "anthropic/unmapped"}),
        named_paths={"repo_root": pre.tmp}, cadam_transport=lambda *a: b"ok", environ={},
        route_run=lambda argv: (0, "v1\n"), route_http_get=lambda url: 200, route_which=lambda n: "/bin/" + n)
    text = "\n".join(render_report_lines(report))
    assert not report.ok and "[FAIL] route: CADAM via CLI · claude · anthropic/unmapped" in text
    assert "[PASS] route: CADAM · OpenRouter · anthropic/claude-fable-5.1" in text
    for secret in (SECRET, KEY):
        assert secret not in text


# ---- review: settings that cannot run must not be GO --------------------------------------------


def _write_config(pre, **changes):
    raw = json.loads(pre.config.read_text())
    raw.update(changes)
    pre.config.write_text(json.dumps(raw))


def test_a_short_shared_key_is_not_go_and_is_never_printed(pre):
    short = "short-key"
    statuses = pre.audit(CLI, entries={**pre.entries, "CADAM_CLI_BRIDGE_KEY": short})
    failed = _failed(statuses)
    assert any("CADAM_CLI_BRIDGE_KEY: TOO SHORT" in t for t in failed)
    assert short not in " ".join(text for s in statuses for _, text in s.checks)
    # the runner rejects the identical environment before the turn
    with pytest.raises(cadam_routes.BridgeError, match="at least 16"):
        cadam_routes.bridge_settings_for_run({"CADAM_CLI_BRIDGE_CONFIG": str(pre.config), "CADAM_CLI_BRIDGE_KEY": short})


def test_a_model_without_cli_model_is_not_go(pre):
    _write_config(pre, models={"anthropic/claude-sonnet-5.5": {"runtime": "claude"}})
    failed = _failed(pre.audit(CLI))
    assert any("has no cli_model" in t for t in failed) and any("cli_model is required" in t for t in failed)


def test_a_non_loopback_bridge_host_is_not_go_and_not_startable_or_reusable(pre):
    _write_config(pre, host="0.0.0.0")
    failed = _failed(pre.audit(CLI))
    assert any("is not loopback" in t for t in failed)
    assert any("bridge cannot run with this config" in t for t in failed)  # a listener on that host is not accepted either
    with pytest.raises(cadam_routes.BridgeError, match="loopback"):
        cadam_routes.CliBridge(pre.config, KEY)


@pytest.mark.parametrize("patch", [{"port": 0}, {"port": 70000}, {"host": "localhost"}, {"models": {}}])
def test_other_unrunnable_bridge_configs_are_not_go(pre, patch):
    _write_config(pre, **patch)
    assert _failed(pre.audit(CLI))
    with pytest.raises(cadam_routes.BridgeError):
        cadam_routes.CliBridge(pre.config, KEY)


def test_recorded_route_label_is_route_aware_and_stays_total():
    """#1131's route_label never raises; the CADAM routes layer on top of it."""
    bridge = NightlyEntrant(entrant_id="b", kind="cadam", model_id="anthropic/claude-sonnet-5.5", route="cli-bridge", runtime="claude")
    assert nightly_cad.route_label(bridge) == "CADAM via CLI · claude · Sonnet 5.5"
    default = NightlyEntrant(entrant_id="o", kind="cadam", model_id="anthropic/claude-fable-5.1")
    assert nightly_cad.route_label(default) == "CADAM · OpenRouter · Fable 5.1"
    odd = NightlyEntrant(entrant_id="x", kind="cadam", model_id="", route="cli-bridge", runtime="codex")
    assert isinstance(nightly_cad.route_label(odd), str)  # no exception even for a malformed entrant


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace("allow_fallbacks: true", "allow_fallbacks: false"),
    lambda s: s.replace("['Anthropic']", "['Google']"),
    lambda s: s.replace("startsWith('anthropic/')", "startsWith('google/')"),
    lambda s: s.replace("providers.openrouter().chat", "unused.chat"),
    lambda s: "// provider: { order: ['Anthropic'], allow_fallbacks: true }\n",
])
def test_preflight_rejects_missing_or_drifted_provider_pin(pre, mutation):
    source = pre.tmp / "CADAM/src/server/aiChat.ts"
    source.write_text(mutation(source.read_text()))
    assert any("provider pin MISSING or drifted" in t for t in _failed(pre.audit(OR)))


def test_preflight_checks_fallback_preference_and_non_anthropic_is_unaffected(pre):
    clone = pre.tmp / "CADAM"
    source = clone / "src/server/aiChat.ts"
    original = source.read_text()
    assert cadam_routes.anthropic_provider_pinned(clone)
    source.write_text(original.replace("allow_fallbacks: true", "allow_fallbacks: false"))
    assert not cadam_routes.anthropic_provider_pinned(clone)
    assert any("provider pin MISSING" in t for t in _failed(pre.audit(OR)))
    other = {**OR, "model_id": "google/example"}
    assert not any("provider pin" in t for status in pre.audit(other) for _, t in status.checks)
    source.write_text(original)
    assert all(status.ok for status in pre.audit(OR))


def test_preflight_reports_fallbacks_enabled(pre):
    messages = [text for status in pre.audit(OR) for ok, text in status.checks if ok]
    assert any("Anthropic preferred, fallbacks enabled" in text for text in messages)
    assert not any("fallbacks disabled" in text for text in messages)


@pytest.fixture(autouse=True)
def offline_credit_probe(monkeypatch):
    monkeypatch.setattr("makerbench.nightly_preflight.openrouter_credit_check",
                        lambda *args: (True, "OpenRouter limit_remaining=10, usage=0"))
