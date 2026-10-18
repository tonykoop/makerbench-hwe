"""Tests for the redacted nightly CAD arena preflight doctor (#658).

All secrets in this file are fakes written to temp files — the real external
secrets file is never read by any test.
"""

import pytest
import json
import subprocess
import sys

from typer.testing import CliRunner

from makerbench.cli import app
from makerbench.nightly_preflight import (
    LOCK_ABSENT,
    LOCK_ACTIVE,
    LOCK_STALE,
    SECRET_MISSING,
    SECRET_PLACEHOLDER,
    SECRET_PRESENT,
    audit_lock,
    audit_queue,
    audit_secrets,
    build_report,
    classify_secret_value,
    parse_secrets_file,
    render_report_lines,
)

runner = CliRunner()

FAKE_VALUE = "fake-user-8c1d2e3f-not-a-real-credential"
FAKE_TOKEN = "sk-FAKE-1234567890abcdef-DO-NOT-USE"


def _secrets_file(tmp_path, text):
    path = tmp_path / "secrets.env"
    path.write_text(text)
    return path


def _queue_file(tmp_path, jobs):
    path = tmp_path / "queue.json"
    path.write_text(
        json.dumps({"schema": "makerbench-nightly-cad-queue-v1", "jobs": jobs})
    )
    return path


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_classify_secret_value_states():
    assert classify_secret_value(None) == SECRET_MISSING
    assert classify_secret_value("") == SECRET_PLACEHOLDER
    assert classify_secret_value("   ") == SECRET_PLACEHOLDER
    assert classify_secret_value("changeme") == SECRET_PLACEHOLDER
    assert classify_secret_value("CHANGE_ME") == SECRET_PLACEHOLDER
    assert classify_secret_value("<your token here>") == SECRET_PLACEHOLDER
    assert classify_secret_value("your_token") == SECRET_PLACEHOLDER
    assert classify_secret_value("xxxxx") == SECRET_PLACEHOLDER
    assert classify_secret_value(FAKE_TOKEN) == SECRET_PRESENT


def test_parse_secrets_file_handles_export_quotes_comments(tmp_path):
    path = _secrets_file(
        tmp_path,
        "# comment\n"
        f"export CADAM_USER_ID='{FAKE_VALUE}'\n"
        f'CADAM_ACCESS_TOKEN="{FAKE_TOKEN}"\n'
        "SUPABASE_SERVICE_ROLE_KEY=changeme\n"
        "not a kv line\n",
    )
    entries = parse_secrets_file(path)
    assert entries["CADAM_USER_ID"] == FAKE_VALUE
    assert entries["CADAM_ACCESS_TOKEN"] == FAKE_TOKEN
    assert entries["SUPABASE_SERVICE_ROLE_KEY"] == "changeme"


def test_audit_secrets_reports_missing_placeholder_present(tmp_path):
    path = _secrets_file(
        tmp_path,
        f"CADAM_USER_ID={FAKE_VALUE}\nCADAM_ACCESS_TOKEN=changeme\n",
    )
    statuses = {item.key: item.status for item in audit_secrets(path)}
    assert statuses == {
        "CADAM_USER_ID": SECRET_PRESENT,
        "CADAM_ACCESS_TOKEN": SECRET_PLACEHOLDER,
        "SUPABASE_SERVICE_ROLE_KEY": SECRET_MISSING,
    }


def test_audit_secrets_missing_file_reports_all_missing(tmp_path):
    statuses = audit_secrets(tmp_path / "nope.env")
    assert all(item.status == SECRET_MISSING for item in statuses)


def test_lock_absent_live_and_dead_pid(tmp_path):
    lock = tmp_path / ".nightly-cad.lock"
    assert audit_lock(lock).status == LOCK_ABSENT

    lock.write_text(json.dumps({"pid": _dead_pid(), "host": "test"}))
    stale = audit_lock(lock)
    assert stale.status == LOCK_STALE
    assert not stale.ok

    import os

    lock.write_text(json.dumps({"pid": os.getpid(), "host": "test"}))
    active = audit_lock(lock)
    assert active.status == LOCK_ACTIVE
    assert active.pid == os.getpid()
    assert not active.ok
    # No mutation: the lock file is untouched by the audit.
    assert lock.exists()


def test_audit_queue_summarizes_jobs_and_flags_blocked(tmp_path):
    queue = _queue_file(
        tmp_path,
        [
            {"job_id": "sambuca-night", "status": "blocked"},
            {"job_id": "cittern-night", "status": "queued"},
        ],
    )
    report = audit_queue(queue)
    assert report.jobs == (("sambuca-night", "blocked"), ("cittern-night", "queued"))
    assert report.blocked_job_ids == ("sambuca-night",)
    assert not report.ok


def test_report_go_when_everything_green(tmp_path):
    secrets = _secrets_file(
        tmp_path,
        f"CADAM_USER_ID={FAKE_VALUE}\n"
        f"CADAM_ACCESS_TOKEN={FAKE_TOKEN}\n"
        f"SUPABASE_SERVICE_ROLE_KEY={FAKE_TOKEN}\n",
    )
    queue = _queue_file(tmp_path, [{"job_id": "sambuca-night", "status": "queued"}])
    report = build_report(
        secrets_path=secrets,
        lock_path=tmp_path / ".nightly-cad.lock",
        queue_path=queue,
        named_paths={"repo_root": tmp_path},
    )
    assert report.ok
    assert "preflight: GO" in render_report_lines(report)[-1]


@pytest.fixture(autouse=True)
def _healthy_cadam_server(monkeypatch):
    """CLI/report tests must not touch the network: the doctor's probe transport is stubbed."""

    from makerbench import nightly_preflight

    monkeypatch.setattr(nightly_preflight, "_probe_transport", lambda *_a: b"ok")


def _cli_args(tmp_path, secrets, queue):
    return [
        "arena",
        "preflight",
        "--secrets",
        str(secrets),
        "--queue",
        str(queue),
        "--output-root",
        str(tmp_path),
        "--repo-root",
        str(tmp_path),
        "--runner-script",
        str(queue),  # any existing file stands in for the runner script
        "--no-entrants",  # these tests cover the other gates; entrant checks are tested below
    ]


def test_cli_preflight_exit_nonzero_and_redacted_with_no_credentials(tmp_path):
    secrets = _secrets_file(tmp_path, "CADAM_USER_ID=changeme\n")
    queue = _queue_file(tmp_path, [{"job_id": "sambuca-night", "status": "blocked"}])
    result = runner.invoke(app, _cli_args(tmp_path, secrets, queue))
    assert result.exit_code == 1
    assert "CADAM_USER_ID: PLACEHOLDER" in result.output
    assert "CADAM_ACCESS_TOKEN: MISSING" in result.output
    assert "SUPABASE_SERVICE_ROLE_KEY: MISSING" in result.output
    assert "sambuca-night: blocked" in result.output
    assert "NO-GO" in result.output


def test_cli_preflight_never_prints_secret_values_even_when_present(tmp_path):
    """Redaction RED control: populated fake secrets must never reach stdout."""

    secrets = _secrets_file(
        tmp_path,
        f"CADAM_USER_ID={FAKE_VALUE}\n"
        f"CADAM_ACCESS_TOKEN={FAKE_TOKEN}\n"
        f"SUPABASE_SERVICE_ROLE_KEY={FAKE_TOKEN}\n",
    )
    queue = _queue_file(tmp_path, [{"job_id": "sambuca-night", "status": "queued"}])
    result = runner.invoke(app, _cli_args(tmp_path, secrets, queue))
    assert result.exit_code == 0, result.output
    assert FAKE_VALUE not in result.output
    assert FAKE_TOKEN not in result.output
    assert "CADAM_USER_ID: PRESENT" in result.output
    assert "GO" in result.output


def test_cli_preflight_fails_on_missing_paths(tmp_path):
    secrets = _secrets_file(
        tmp_path,
        f"CADAM_USER_ID={FAKE_VALUE}\n"
        f"CADAM_ACCESS_TOKEN={FAKE_TOKEN}\n"
        f"SUPABASE_SERVICE_ROLE_KEY={FAKE_TOKEN}\n",
    )
    queue = _queue_file(tmp_path, [{"job_id": "sambuca-night", "status": "queued"}])
    result = runner.invoke(
        app,
        [
            "arena",
            "preflight",
            "--secrets",
            str(secrets),
            "--queue",
            str(queue),
            "--output-root",
            str(tmp_path / "missing-runs"),
            "--repo-root",
            str(tmp_path),
            "--runner-script",
            str(tmp_path / "missing-runner.ps1"),
        ],
    )
    assert result.exit_code == 1
    assert "MISSING" in result.output
    assert "NO-GO" in result.output


def test_cli_preflight_reports_stale_lock(tmp_path):
    secrets = _secrets_file(
        tmp_path,
        f"CADAM_USER_ID={FAKE_VALUE}\n"
        f"CADAM_ACCESS_TOKEN={FAKE_TOKEN}\n"
        f"SUPABASE_SERVICE_ROLE_KEY={FAKE_TOKEN}\n",
    )
    queue = _queue_file(tmp_path, [{"job_id": "sambuca-night", "status": "queued"}])
    lock = tmp_path / ".nightly-cad.lock"
    dead = _dead_pid()
    lock.write_text(json.dumps({"pid": dead, "host": "test"}))
    result = runner.invoke(app, _cli_args(tmp_path, secrets, queue))
    assert result.exit_code == 1
    assert "STALE" in result.output
    assert str(dead) in result.output
    # Read-only guarantee: the stale lock is reported, never removed.
    assert lock.exists()


# ---- #660: CADAM server + bot sign-in ------------------------------------------------------

import json as _json  # noqa: E402
import urllib.error as _urlerror  # noqa: E402

BOT_ENV = (
    "CADAM_USER_ID=user-uuid-123\n"
    "SUPABASE_SERVICE_ROLE_KEY=service-role-secret-value\n"
    "CADAM_BOT_EMAIL=bot-secret@example.test\n"
    "CADAM_BOT_PASSWORD=bot-password-secret-value\n"
    "VITE_SUPABASE_ANON_KEY=anon-secret-value\n"
)
BOT_SECRETS = ("service-role-secret-value", "bot-password-secret-value", "anon-secret-value", "bot-secret@example.test")


def _bot_report(tmp_path, transport, extra=""):
    secrets = _secrets_file(tmp_path, BOT_ENV + extra)
    queue = _queue_file(tmp_path, [{"job_id": "job-1", "status": "queued"}])
    return build_report(
        secrets_path=secrets,
        lock_path=tmp_path / ".nightly-cad.lock",
        queue_path=queue,
        named_paths={"repo_root": tmp_path},
        cadam_transport=transport,
        environ={},
    )


def _healthy(method, url, headers, body):
    if "/auth/v1/token" in url:
        return _json.dumps({"access_token": "jwt-secret-value"}).encode()
    return b"<html></html>"


def test_bot_credentials_replace_the_static_token_requirement_and_go(tmp_path):
    report = _bot_report(tmp_path, _healthy)
    assert report.ok
    assert "CADAM_ACCESS_TOKEN" not in {s.key for s in report.secrets}
    assert {"CADAM_BOT_EMAIL", "CADAM_BOT_PASSWORD", "VITE_SUPABASE_ANON_KEY"} <= {s.key for s in report.secrets}
    lines = render_report_lines(report)
    assert "[PASS] cadam" in lines
    assert any("bot sign-in succeeded" in line for line in lines)
    assert lines[-1] == "preflight: GO"


def test_server_down_names_the_start_command(tmp_path):
    def down(*_args):
        raise _urlerror.URLError("connection refused")

    report = _bot_report(tmp_path, down)
    assert not report.ok and not report.cadam.server_ok
    text = "\n".join(render_report_lines(report))
    assert "[FAIL] cadam" in text
    assert "not reachable at http://127.0.0.1:3000" in text
    assert "npm run dev" in text
    assert text.splitlines()[-1] == "preflight: NO-GO"


def test_a_404_still_counts_as_the_server_being_up(tmp_path):
    def not_found_root(method, url, headers, body):
        if "/auth/v1/token" in url:
            return _healthy(method, url, headers, body)
        raise _urlerror.HTTPError(url, 404, "nope", {}, None)

    assert _bot_report(tmp_path, not_found_root).ok


def test_sign_in_failure_is_a_clear_redacted_no_go(tmp_path):
    def rejects(method, url, headers, body):
        if "/auth/v1/token" in url:
            raise _urlerror.HTTPError(url, 400, "invalid login bot-password-secret-value", {}, None)
        return b"ok"

    report = _bot_report(tmp_path, rejects)
    assert report.cadam.server_ok and not report.cadam.signin_ok
    text = "\n".join(render_report_lines(report))
    assert "sign-in rejected (HTTP 400)" in text
    assert "NO-GO" in text
    for secret in BOT_SECRETS:
        assert secret not in text


def test_preflight_output_never_contains_a_secret_or_token(tmp_path):
    text = "\n".join(render_report_lines(_bot_report(tmp_path, _healthy)))
    for secret in BOT_SECRETS + ("jwt-secret-value",):
        assert secret not in text


def test_urls_come_from_the_secrets_file_and_reach_the_probes(tmp_path):
    seen = []

    def spy(method, url, headers, body):
        seen.append(url)
        return _healthy(method, url, headers, body)

    _bot_report(tmp_path, spy, extra="CADAM_BASE_URL=http://cadam.local:4000\nVITE_SUPABASE_URL=http://sb.local:9\n")
    assert seen[0] == "http://cadam.local:4000"
    assert seen[1].startswith("http://sb.local:9/auth/v1/token")


def test_static_token_mode_still_probes_the_server_but_never_signs_in(tmp_path):
    secrets = _secrets_file(
        tmp_path,
        f"CADAM_USER_ID={FAKE_VALUE}\nCADAM_ACCESS_TOKEN={FAKE_TOKEN}\nSUPABASE_SERVICE_ROLE_KEY={FAKE_TOKEN}\n",
    )
    queue = _queue_file(tmp_path, [{"job_id": "job-1", "status": "queued"}])

    seen = []

    def server_only(method, url, headers, body):
        seen.append(url)
        if "/auth/v1/token" in url:
            raise AssertionError("static-token mode must not sign in")
        return b"ok"

    report = build_report(
        secrets_path=secrets,
        lock_path=tmp_path / ".nightly-cad.lock",
        queue_path=queue,
        named_paths={"repo_root": tmp_path},
        cadam_transport=server_only,
        environ={},
    )
    assert seen == ["http://127.0.0.1:3000"]
    assert report.ok and report.cadam.signin_ok


def test_stopped_app_is_no_go_in_static_token_mode_too(tmp_path):
    secrets = _secrets_file(
        tmp_path,
        f"CADAM_USER_ID={FAKE_VALUE}\nCADAM_ACCESS_TOKEN={FAKE_TOKEN}\nSUPABASE_SERVICE_ROLE_KEY={FAKE_TOKEN}\n",
    )
    queue = _queue_file(tmp_path, [{"job_id": "job-1", "status": "queued"}])

    def down(*_args):
        raise _urlerror.URLError("refused")

    report = build_report(
        secrets_path=secrets,
        lock_path=tmp_path / ".nightly-cad.lock",
        queue_path=queue,
        named_paths={"repo_root": tmp_path},
        cadam_transport=down,
        environ={},
    )
    assert not report.ok
    assert "npm run dev" in "\n".join(render_report_lines(report))


def test_a_5xx_from_the_app_is_no_go_even_when_sign_in_would_succeed(tmp_path):
    def broken_app(method, url, headers, body):
        if "/auth/v1/token" in url:
            return _healthy(method, url, headers, body)
        raise _urlerror.HTTPError(url, 503, "unavailable", {}, None)

    report = _bot_report(tmp_path, broken_app)
    assert not report.ok and not report.cadam.server_ok
    assert "HTTP 503" in "\n".join(render_report_lines(report))


# --- per-entrant GO/NO-GO (#663) ------------------------------------------------

from makerbench.nightly_preflight import audit_entrants  # noqa: E402
from makerbench.nightly_preflight import CadamStatus  # noqa: E402

_CADAM_OK = CadamStatus("http://127.0.0.1:3000", True, True, "CADAM server up; bot sign-in succeeded")


def _matchup_job(reference, **extra):
    return {
        "job_id": "sambuca-matchup", "instrument_id": "sambuca", "reference_image": str(reference), "budget_usd": 2.0,
        "entrants": [
            {"entrant_id": "cadam-opus", "kind": "cadam", "model_id": "anthropic/claude-opus-5.5", "max_cost_usd": 2.0},
            {"entrant_id": "freecad-opus", "kind": "arena", "model_id": "claude-code-opus-5.5", "backend": "freecad",
             "context_tier": "image"},
            {"entrant_id": "solidworks-sol", "kind": "live", "model_id": "codex-gpt-6.1-sol", "backend": "solidworks-live",
             "context_tier": "image"},
            {"entrant_id": "fusion-sonnet", "kind": "live", "model_id": "claude-code-sonnet-5.5", "backend": "fusion-live",
             "context_tier": "image"},
        ],
        **extra,
    }


def _appdir(tmp_path):
    from makerbench.freecad_backend import APPIMAGE_SHA256

    appdir = tmp_path / "squashfs-root"
    appdir.mkdir()
    (appdir / "AppRun").write_text("")
    (appdir / ".makerbench-appimage-sha256").write_text(APPIMAGE_SHA256 + "\n")
    return appdir


def _shims(tmp_path):
    """A synthetic StudioPipeline-hwe checkout holding both MCP shims (CI has no real one)."""
    root = tmp_path / "StudioPipeline-hwe"
    (root / "driver").mkdir(parents=True)
    for name in ("solidworks_mcp_server.py", "fusion_mcp_server.py"):
        (root / "driver" / name).write_text("")
    return root


def test_every_entrant_is_go_when_its_tools_answer(tmp_path, monkeypatch):
    root = _shims(tmp_path)
    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    queue = _queue_file(tmp_path, [{"job_id": "done", "status": "votable"}, _matchup_job(reference)])
    seen = []
    status = audit_entrants(queue, {"MAKERBENCH_FREECAD_APPDIR": str(_appdir(tmp_path)), "HWE_SW_TOKEN": FAKE_TOKEN},
                            cadam=_CADAM_OK, environ={}, which=lambda name: f"/usr/bin/{name}",
                            ping=lambda config: seen.append(config.env) or True, connector_root=root)
    assert status.ok and status.job_id == "sambuca-matchup"
    assert {item.entrant_id: item.route for item in status.entrants} == {
        "cadam-opus": "CADAM · OpenRouter · Opus 5.5", "freecad-opus": "Claude Code · Opus 5.5 · FreeCAD",
        "solidworks-sol": "Codex · GPT-6.1 Sol · SolidWorks live bridge",
        "fusion-sonnet": "Claude Code · Sonnet 5.5 · Fusion live bridge"}
    assert "live CAD runs one at a time on one seat: SolidWorks then Fusion" in status.job_notes
    assert "worst-case paid spend $2.00 of a $2.00 budget" in status.job_notes
    notes = {item.entrant_id: " ".join(item.notes) for item in status.entrants}
    assert "driver codex --model gpt-6.1-sol" in notes["solidworks-sol"]
    assert "driver claude --model claude-sonnet-5-5" in notes["fusion-sonnet"]
    assert "checksum marker matches" in notes["freecad-opus"]
    assert "claude CLI, model claude-opus-5-5," in notes["freecad-opus"]
    assert seen[0]["HWE_SW_TOKEN"] == FAKE_TOKEN  # the ping gets the bridge token...
    report = _report_lines(status)
    assert FAKE_TOKEN not in report  # ...and it never reaches the report


def _report_lines(status):
    from makerbench.nightly_preflight import LockStatus, PreflightReport, QueueStatus

    report = PreflightReport(secrets=(), lock=LockStatus(LOCK_ABSENT), queue=QueueStatus(ok=True), entrants=status)
    return "\n".join(render_report_lines(report))


def test_each_missing_piece_is_its_own_no_go(tmp_path, monkeypatch):
    root = _shims(tmp_path)
    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    job = _matchup_job(reference)
    job["entrants"][0]["max_cost_usd"] = 0
    queue = _queue_file(tmp_path, [job])
    cadam_down = CadamStatus("http://127.0.0.1:3000", False, False, "CADAM server not reachable")
    status = audit_entrants(queue, {}, cadam=cadam_down, environ={},
                            which=lambda name: None if name == "claude" else f"/usr/bin/{name}",
                            ping=lambda config: config.connector == "hwe-fusion", connector_root=root)
    verdict = {item.entrant_id: (item.ok, " ".join(item.notes)) for item in status.entrants}
    assert not status.ok
    assert verdict["cadam-opus"][0] is False and "not reachable" in verdict["cadam-opus"][1] and "ceiling" in verdict["cadam-opus"][1]
    assert verdict["freecad-opus"][0] is False and "MAKERBENCH_FREECAD_APPDIR is not set" in verdict["freecad-opus"][1]
    assert verdict["solidworks-sol"][0] is False and "start_solidworks_bridge.ps1" in verdict["solidworks-sol"][1]
    assert "HWE_SW_TOKEN is not set" in verdict["solidworks-sol"][1] and "mirrored networking" in verdict["solidworks-sol"][1]
    assert verdict["fusion-sonnet"][0] is False and "claude is not on PATH" in verdict["fusion-sonnet"][1]
    lines = _report_lines(status)
    assert "NO-GO solidworks-sol · Codex · GPT-6.1 Sol · SolidWorks live bridge" in lines and lines.endswith("preflight: NO-GO")


def test_a_claude_model_the_cli_rejects_is_no_go(tmp_path, monkeypatch):
    """2026-10-18 dry night (#663): ``claude -p --model opus-5.5`` exited rc=1 unrecognized_model.

    Preflight checks the CLI model the runner itself resolves, so a mapping that hands the CLI
    ``opus-5.5`` again is NO-GO before the night starts.
    """
    from makerbench import code_cad_providers as providers

    root = _shims(tmp_path)
    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    env = {"MAKERBENCH_FREECAD_APPDIR": str(_appdir(tmp_path)), "HWE_SW_TOKEN": FAKE_TOKEN}

    def audit():
        status = audit_entrants(_queue_file(tmp_path, [_matchup_job(reference)]), env, cadam=_CADAM_OK, environ={},
                                which=lambda name: f"/usr/bin/{name}", ping=lambda config: True, connector_root=root)
        return {item.entrant_id: (item.ok, " ".join(item.notes)) for item in status.entrants}, status

    verdict, status = audit()
    assert status.ok and verdict["freecad-opus"][0]
    monkeypatch.setattr(providers, "cli_model_for_model_id", lambda model_id, **kwargs: ("claude", "opus-5.5"))
    monkeypatch.setattr(providers, "claude_cli_model", lambda name: name)  # the live driver's route, too
    verdict, status = audit()
    assert not status.ok
    for entrant_id in ("freecad-opus", "fusion-sonnet"):
        assert verdict[entrant_id][0] is False
        assert "not a Claude Code model name" in verdict[entrant_id][1]
    assert verdict["solidworks-sol"][0] is True  # a Codex driver is not judged by Claude's names
    assert "NO-GO freecad-opus" in _report_lines(status)


def test_job_level_problems_are_no_go(tmp_path):
    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    over = _matchup_job(reference, budget_usd=1.0)
    status = audit_entrants(_queue_file(tmp_path, [over]), {}, cadam=_CADAM_OK, environ={},
                            which=lambda name: "/bin/x", ping=lambda config: True)
    assert not status.job_ok and any("exceed the night's $1.00 budget" in note for note in status.job_notes)
    assembly = _matchup_job(reference, deliverable="assembly")
    status = audit_entrants(_queue_file(tmp_path, [assembly]), {}, cadam=_CADAM_OK, environ={},
                            which=lambda name: "/bin/x", ping=lambda config: True)
    assert not status.job_ok and any("assembly nights are not supported" in note for note in status.job_notes)
    empty = audit_entrants(_queue_file(tmp_path, [{"job_id": "x", "status": "votable"}]), {}, cadam=_CADAM_OK, environ={})
    assert empty.ok and empty.job_notes == ("no queued job",)


def test_cli_preflight_prints_the_entrant_section_by_default(tmp_path, monkeypatch):
    from makerbench import nightly_preflight

    monkeypatch.setattr(nightly_preflight, "_probe_transport", lambda *a, **k: b"<html></html>")
    monkeypatch.setattr("makerbench.live_cad_runner.connector_available", lambda config: False)
    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    secrets = _secrets_file(tmp_path, f"CADAM_USER_ID={FAKE_VALUE}\nCADAM_ACCESS_TOKEN={FAKE_TOKEN}\n"
                                      f"SUPABASE_SERVICE_ROLE_KEY={FAKE_TOKEN}\n")
    queue = _queue_file(tmp_path, [_matchup_job(reference)])
    args = [a for a in _cli_args(tmp_path, secrets, queue) if a != "--no-entrants"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "entrants: job sambuca-matchup" in result.output and "NO-GO solidworks-sol" in result.output
    assert FAKE_TOKEN not in result.output


def test_a_missing_mcp_shim_is_its_own_no_go(tmp_path):
    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    status = audit_entrants(_queue_file(tmp_path, [_matchup_job(reference)]), {"HWE_SW_TOKEN": FAKE_TOKEN},
                            cadam=_CADAM_OK, environ={}, which=lambda name: "/bin/x", ping=lambda config: True,
                            connector_root=tmp_path / "no-checkout")
    live = {item.entrant_id: " ".join(item.notes) for item in status.entrants if "live" in item.route}
    assert all("MCP shim missing" in notes for notes in live.values()) and len(live) == 2


def test_a_cli_bridge_cadam_entrant_is_judged_by_its_route_checks(tmp_path):
    from makerbench import cadam_routes
    from makerbench.nightly_preflight import RouteStatus

    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    job = _matchup_job(reference)
    job["entrants"][0] = {"entrant_id": "cadam-cli", "kind": "cadam", "model_id": "anthropic/claude-opus-5.5",
                          "route": "cli-bridge", "runtime": "claude", "max_cost_usd": 0}
    queue = _queue_file(tmp_path, [job])
    label = cadam_routes.route_label("cli-bridge", "claude", "anthropic/claude-opus-5.5")
    cadam_down = CadamStatus("http://127.0.0.1:3000", False, False, "CADAM server not reachable")  # the OpenRouter app
    kwargs = dict(cadam=cadam_down, environ={}, which=lambda name: "/bin/x", ping=lambda config: True,
                  connector_root=_shims(tmp_path))
    ready = audit_entrants(queue, {"HWE_SW_TOKEN": FAKE_TOKEN},
                           routes=(RouteStatus(label, ((True, "bridge is already listening"),)),), **kwargs)
    item = next(e for e in ready.entrants if e.entrant_id == "cadam-cli")
    assert item.ok and "CLI bridge (claude subscription)" in " ".join(item.notes)  # no OpenRouter app or ceiling needed
    broken = audit_entrants(queue, {"HWE_SW_TOKEN": FAKE_TOKEN},
                            routes=(RouteStatus(label, ((False, "claude CLI is NOT logged in"),)),), **kwargs)
    item = next(e for e in broken.entrants if e.entrant_id == "cadam-cli")
    assert not item.ok and "claude CLI is NOT logged in" in item.notes


@pytest.mark.parametrize("broken", ["missing-approval", "prompt-approval", "sandbox-bypass"])
def test_solidworks_config_regression_is_no_go(tmp_path, monkeypatch, broken):
    from makerbench import live_cad_runner as live

    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    job = _matchup_job(reference)
    job["entrants"] = [job["entrants"][2]]
    queue = _queue_file(tmp_path, [job])
    original = live.driver_command

    def damaged(config, path):
        argv = original(config, path)
        key = 'mcp_servers.hwe-solidworks.default_tools_approval_mode="approve"'
        if broken == "missing-approval":
            index = argv.index(key)
            del argv[index - 1:index + 1]
        elif broken == "prompt-approval":
            argv[argv.index(key)] = key.replace('"approve"', '"prompt"')
        else:
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        return argv

    monkeypatch.setattr(live, "driver_command", damaged)
    status = audit_entrants(queue, {"HWE_SW_TOKEN": FAKE_TOKEN}, cadam=_CADAM_OK,
                            environ={}, which=lambda name: f"/usr/bin/{name}",
                            ping=lambda config: True, connector_root=_shims(tmp_path))
    assert not status.ok
    assert not status.entrants[0].ok
    assert "must pre-approve hwe-solidworks" in " ".join(status.entrants[0].notes)


def test_a_live_entrant_whose_mcp_launch_fails_is_no_go(tmp_path):
    # #663: the raw ping passed while the driver's own MCP launch could not reach the bridge.
    reference = tmp_path / "photo.jpg"
    reference.write_bytes(b"x")
    seen = []

    def probe(config, environ):
        seen.append((config.driver_runtime, environ.get("HWE_FUSION_PORT")))
        return (config.connector != "hwe-fusion", f"{config.connector} MCP ping failed through the driver's launch path: bridge error")

    status = audit_entrants(_queue_file(tmp_path, [_matchup_job(reference)]), {"HWE_SW_TOKEN": FAKE_TOKEN, "HWE_FUSION_PORT": "8766"},
                            cadam=_CADAM_OK, environ={}, which=lambda name: "/bin/x", ping=lambda config: True,
                            connector_root=_shims(tmp_path), mcp_probe=probe)
    verdict = {item.entrant_id: (item.ok, " ".join(item.notes)) for item in status.entrants}
    assert verdict["fusion-sonnet"][0] is False and "driver's launch path" in verdict["fusion-sonnet"][1]
    assert ("claude", "8766") in seen
