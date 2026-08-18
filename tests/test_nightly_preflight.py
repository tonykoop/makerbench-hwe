"""Tests for the redacted nightly CAD arena preflight doctor (#658).

All secrets in this file are fakes written to temp files — the real external
secrets file is never read by any test.
"""

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
