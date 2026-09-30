"""agy (Gemini) adapter: model selection, tool-denial retry, prompt note (#926).

Everything here runs against a stubbed ``agy`` executable or a patched ``subprocess.run``:
no live agy call, no login, no quota.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import pytest

from makerbench import code_cad_providers as providers
from makerbench.code_cad_generator import GenerationRequest

DENIAL = ('jetski: no output produced — a tool required the "command" permission that headless mode '
          "cannot prompt for, so it was auto-denied.")
GOOD = "```scad\ncube([10, 10, 10]);\n```"

STUB = """#!{python}
import json, os, sys
state = os.environ["AGY_STUB_STATE"]
mode = os.environ.get("AGY_STUB_MODE", "ok")
n = 0
if os.path.exists(state):
    n = len(open(state).read().splitlines())
open(state, "a").write(json.dumps(sys.argv[1:]) + "\\n")
call = n + 1
if mode == "deny_always" or (mode == "deny_once" and call == 1):
    sys.stderr.write({denial!r})
    sys.exit(0)            # exit 0, empty stdout: the silent headless denial
if mode == "fail_rc":
    sys.stderr.write("boom")
    sys.exit(3)
sys.stdout.write({good!r})
"""


@pytest.fixture
def stub_agy(tmp_path, monkeypatch):
    bin_ = tmp_path / "agy"
    bin_.write_text(STUB.format(python=sys.executable, denial=DENIAL, good=GOOD))
    bin_.chmod(bin_.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AGY_STUB_STATE", str(state))

    def calls():
        return [json.loads(line) for line in state.read_text().splitlines()] if state.exists() else []

    return bin_, calls


def _request(context_tier="blind", workspace_dir=None) -> GenerationRequest:
    return GenerationRequest(
        model_id="antigravity-gemini-3.8-flash-high", instrument_id="ocarina", seed=0,
        spec={"id": "ocarina", "task_brief": "an ocarina"}, prompt="Instrument id: ocarina\n",
        prompt_sha256="0" * 64, context_tier=context_tier,
        workspace_dir=str(workspace_dir) if workspace_dir is not None else None,
    )


def test_command_shape_model_flag_and_no_permission_flags(stub_agy, monkeypatch):
    bin_, calls = stub_agy
    monkeypatch.setenv("AGY_STUB_MODE", "ok")
    gen = providers.make_agy_generator("gemini-3.8-flash-high", bin_=str(bin_), retry_sleep_s=0)
    assert gen(_request()) == "cube([10, 10, 10]);"
    (argv,) = calls()
    assert argv[0] == "--print" and argv[2] == "--print-timeout" and argv[3] == "15m"
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"
    # subscription only, and no permission broadening
    for forbidden in ("--dangerously-skip-permissions", "--sandbox", "--mode", "--agent"):
        assert forbidden not in argv
    assert argv[1].endswith(providers.AGY_NO_TOOLS_NOTE)


def test_no_model_flag_unless_a_model_is_given(stub_agy, monkeypatch):
    bin_, calls = stub_agy
    monkeypatch.setenv("AGY_STUB_MODE", "ok")
    providers.make_agy_generator(bin_=str(bin_), retry_sleep_s=0)(_request())
    assert "--model" not in calls()[0]


def test_a_silent_tool_denial_is_retried_and_then_succeeds(stub_agy, monkeypatch):
    bin_, calls = stub_agy
    monkeypatch.setenv("AGY_STUB_MODE", "deny_once")
    gen = providers.make_agy_generator("gemini-3.8-flash-high", bin_=str(bin_), retry_sleep_s=0)
    assert gen(_request()) == "cube([10, 10, 10]);"
    assert len(calls()) == 2


def test_repeated_denial_reports_the_reason_and_the_attempt_count(stub_agy, monkeypatch):
    bin_, calls = stub_agy
    monkeypatch.setenv("AGY_STUB_MODE", "deny_always")
    gen = providers.make_agy_generator("gemini-3.8-flash-high", bin_=str(bin_), retry_sleep_s=0)
    with pytest.raises(RuntimeError, match=r"agy produced no output \(rc=0, 3 attempts\).*auto-denied"):
        gen(_request())
    assert len(calls()) == 3


def test_empty_retries_is_configurable(stub_agy, monkeypatch):
    bin_, calls = stub_agy
    monkeypatch.setenv("AGY_STUB_MODE", "deny_always")
    gen = providers.make_agy_generator(bin_=str(bin_), retry_sleep_s=0, empty_retries=0)
    with pytest.raises(RuntimeError, match="1 attempts"):
        gen(_request())
    assert len(calls()) == 1


def test_nonzero_exit_keeps_the_existing_single_retry(stub_agy, monkeypatch):
    bin_, calls = stub_agy
    monkeypatch.setenv("AGY_STUB_MODE", "fail_rc")
    gen = providers.make_agy_generator(bin_=str(bin_), retry_sleep_s=0)
    with pytest.raises(RuntimeError, match=r"agy failed \(rc=3, 2 attempts\)"):
        gen(_request())
    assert len(calls()) == 2


@pytest.fixture
def hermetic_sandbox(monkeypatch):
    from makerbench import entrant_sandbox

    monkeypatch.setattr(entrant_sandbox, "sandbox_available", lambda: True)
    monkeypatch.setattr(entrant_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(entrant_sandbox, "profile_for_provider",
                        lambda provider, bin_: entrant_sandbox.generic_profile(provider))


def test_non_blind_prompt_is_not_given_the_no_tools_note(tmp_path, monkeypatch, hermetic_sandbox):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=GOOD, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ws = tmp_path / "ws"
    ws.mkdir()
    providers.make_agy_generator(retry_sleep_s=0)(_request("repo", ws))
    prompt = seen["cmd"][seen["cmd"].index("--print") + 1]
    assert providers.AGY_NO_TOOLS_NOTE not in prompt


def _capture_cmd(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=GOOD, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_model_map_model_reaches_agy_but_a_derived_name_does_not(monkeypatch):
    seen = _capture_cmd(monkeypatch)
    model_map = {"antigravity-gemini-3.8-flash-high": {"provider": "agy", "model": "gemini-3.8-flash-high"}}
    gen = providers.resolve_generator("antigravity-gemini-3.8-flash-high", model_map=model_map)
    gen(_request())
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "gemini-3.8-flash-high"

    seen.clear()
    # No explicit model in the map: the name derived from the id is NOT passed (it may not be a
    # model `agy models` lists), so existing agy entrants keep their behaviour.
    providers.resolve_generator("antigravity-gemini-default")(_request("blind"))
    assert "--model" not in seen["cmd"]


def test_prompt_note_forbids_tools_without_asking_for_permissions():
    note = providers.AGY_NO_TOOLS_NOTE.lower()
    assert "do not run any commands" in note and "do not use any tools" in note
    assert "permission" not in note and "allow" not in note


def test_attempt_count_spans_mixed_failures(stub_agy, monkeypatch, tmp_path):
    # rc!=0 once, then a silent denial, then success: 3 calls in total, and when everything
    # keeps failing the message counts every call, not just the last retry sequence.
    bin_, calls = stub_agy
    script = bin_.read_text().replace(
        'if mode == "fail_rc":',
        'if mode == "mixed" and call == 1:\n    sys.stderr.write("boom")\n    sys.exit(3)\nif mode == "mixed" and call in (2, 3):\n'
        '    sys.stderr.write(' + repr(DENIAL) + ')\n    sys.exit(0)\nif mode == "fail_rc":')
    bin_.write_text(script)
    monkeypatch.setenv("AGY_STUB_MODE", "mixed")
    gen = providers.make_agy_generator(bin_=str(bin_), retry_sleep_s=0)  # defaults: 1 exit + 2 denial retries
    assert gen(_request()) == "cube([10, 10, 10]);"
    assert len(calls()) == 4  # rc!=0, denied, denied, ok
