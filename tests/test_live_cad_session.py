"""Interactive live CAD session launcher (#663): argv wiring, secrets, bridge-down refusal."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from makerbench import live_cad_session as session
from makerbench.cli import app

FAKE_TOKEN = "tok-live-session-0123456789abcdef"


def _secrets(tmp_path, text=None):
    path = tmp_path / "nightly.env"
    path.write_text(text if text is not None else
                    f"HWE_SW_HOST=172.20.0.1\nHWE_SW_TOKEN={FAKE_TOKEN}\nHWE_FUSION_TOKEN={FAKE_TOKEN}\n"
                    "OPENROUTER_API_KEY=sk-not-for-cad\n", encoding="utf-8")
    return path


def _plan(tmp_path, *, cad="solidworks", runtime="codex", ping=True, probe=(True, "ok"), **kwargs):
    workdir = tmp_path / "run"
    workdir.mkdir(exist_ok=True)
    calls = []
    plan = session.plan_session(
        cad=cad, runtime=runtime, workdir=workdir, secrets=kwargs.pop("secrets", _secrets(tmp_path)),
        environ=kwargs.pop("environ", {"PATH": "/usr/bin"}), session_dir=tmp_path,
        ping=lambda config: calls.append(("ping", config)) or ping,
        mcp_probe=lambda config: calls.append(("probe", config)) or probe, **kwargs)
    return plan, calls, workdir


def test_codex_session_is_interactive_and_approves_only_its_connector(tmp_path):
    plan, _, workdir = _plan(tmp_path)
    argv = plan.argv
    assert argv[:3] == ["codex", "-m", "gpt-6.1-sol"] and "exec" not in argv
    assert argv[argv.index("-C") + 1] == workdir.resolve().as_posix()
    assert argv[argv.index("--add-dir") + 1] == workdir.resolve().as_posix()
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    overrides = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-c"]
    assert all(item.startswith("mcp_servers.hwe-solidworks.") for item in overrides)
    assert [o for o in overrides if "approval" in o] == ['mcp_servers.hwe-solidworks.default_tools_approval_mode="approve"']
    assert 'mcp_servers.hwe-solidworks.env_vars=["HWE_SW_HOST", "HWE_SW_PORT", "HWE_SW_TOKEN"]' in overrides
    joined = " ".join(argv)
    assert FAKE_TOKEN not in joined and "bypass" not in joined and "never" not in joined
    assert plan.cwd == workdir.resolve()


def test_claude_session_preallows_read_and_the_connector_and_nothing_else(tmp_path):
    plan, _, workdir = _plan(tmp_path, cad="fusion", runtime="claude")
    argv = plan.argv
    assert argv[:3] == ["claude", "--model", "claude-opus-5-5"] and "-p" not in argv
    assert "--allowedTools=Read,mcp__hwe-fusion" in argv and "--strict-mcp-config" in argv
    assert not any(tok.startswith("--permission-mode") or "dontAsk" in tok or "dangerously" in tok for tok in argv)
    assert argv[argv.index("--add-dir") + 1] == workdir.resolve().as_posix()
    config_path = next(tok.split("=", 1)[1] for tok in argv if tok.startswith("--mcp-config="))
    config_text = open(config_path, encoding="utf-8").read()
    server = json.loads(config_text)["mcpServers"]
    assert list(server) == ["hwe-fusion"]
    assert server["hwe-fusion"]["env"]["HWE_FUSION_TOKEN"].startswith("${HWE_FUSION_TOKEN")
    assert FAKE_TOKEN not in config_text and FAKE_TOKEN not in " ".join(argv)


def test_bridge_env_reaches_the_process_env_not_argv(tmp_path):
    plan, calls, _ = _plan(tmp_path, environ={"PATH": "/usr/bin", "HWE_SW_PORT": "8767", "HWE_SW_TOKEN": "stale"})
    assert plan.env["HWE_SW_TOKEN"] == FAKE_TOKEN  # the secrets file wins
    assert plan.env["HWE_SW_PORT"] == "8767" and plan.env["HWE_SW_HOST"] == "172.20.0.1"
    assert "OPENROUTER_API_KEY" not in plan.env  # only bridge settings come from the file
    assert calls[0][1].env["HWE_SW_TOKEN"] == FAKE_TOKEN  # the ping is authenticated
    assert FAKE_TOKEN not in " ".join(plan.argv)


def test_a_down_bridge_refuses_to_launch_before_probing(tmp_path):
    with pytest.raises(session.SessionError, match="did not answer an authenticated ping.*not launching") as err:
        _plan(tmp_path, ping=False)
    assert "start_solidworks_bridge.ps1" in str(err.value) and FAKE_TOKEN not in str(err.value)


def test_a_failing_mcp_launch_path_refuses_to_launch(tmp_path):
    with pytest.raises(session.SessionError, match="shim gave no ping reply; not launching"):
        _plan(tmp_path, cad="fusion", probe=(False, "hwe-fusion MCP shim gave no ping reply"))


def test_workdir_prompt_and_model_handling(tmp_path):
    with pytest.raises(session.SessionError, match="is not a folder"):
        session.plan_session(cad="fusion", runtime="codex", workdir=tmp_path / "missing", secrets=_secrets(tmp_path),
                             environ={}, ping=lambda c: True, mcp_probe=lambda c: (True, ""))
    prompt = tmp_path / "step.md"
    prompt.write_text("Measure the gap between the neck heel and the hull.\n", encoding="utf-8")
    plan, _, _ = _plan(tmp_path, runtime="claude", model="opus-5.5", prompt_file=prompt)
    assert plan.argv[2] == "claude-opus-5-5"
    assert plan.argv[-1] == "Measure the gap between the neck heel and the hull."
    with pytest.raises(session.SessionError, match="not a Claude Code model name"):
        _plan(tmp_path, runtime="claude", model="opus5.5")
    with pytest.raises(session.SessionError, match="--cad must be one of"):
        _plan(tmp_path, cad="onshape")


def test_cli_bridge_down_exits_nonzero_without_echoing_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "connector_available", lambda config: False)
    workdir = tmp_path / "run"
    workdir.mkdir()
    result = CliRunner().invoke(app, ["arena", "live-cad-session", "--cad", "solidworks", "--workdir", str(workdir),
                                      "--secrets", str(_secrets(tmp_path))])
    assert result.exit_code == 1
    assert "did not answer an authenticated ping" in result.output and FAKE_TOKEN not in result.output


def test_cli_dry_run_prints_the_argv(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "connector_available", lambda config: True)
    monkeypatch.setattr(session, "connector_mcp_probe", lambda config: (True, "ok"))
    monkeypatch.setattr(session, "launch", lambda plan: pytest.fail("dry run must not launch"))
    workdir = tmp_path / "run"
    workdir.mkdir()
    result = CliRunner().invoke(app, ["arena", "live-cad-session", "--cad", "fusion", "--runtime", "claude",
                                      "--workdir", str(workdir), "--secrets", str(_secrets(tmp_path)), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "--allowedTools=Read,mcp__hwe-fusion" in result.output and FAKE_TOKEN not in result.output
