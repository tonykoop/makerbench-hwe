"""Tests for the Code-CAD Arena local CLI provider adapters."""

from __future__ import annotations

import json
import subprocess

import pytest

from makerbench import code_cad_providers as providers
from makerbench.code_cad_generator import GenerationRequest


def _request(model_id: str = "claude-code-sonnet") -> GenerationRequest:
    return GenerationRequest(
        model_id=model_id,
        instrument_id="ocarina",
        seed=0,
        spec={"id": "ocarina"},
        prompt="Instrument id: ocarina\nSeed: 0\n",
        prompt_sha256="0" * 64,
    )


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestExtractScad:
    def test_extracts_fenced_block(self):
        text = "notes\n```scad\ncube([1,2,3]);\n```\ntrailer"
        assert providers.extract_scad(text) == "cube([1,2,3]);"

    def test_accepts_openscad_fence_label(self):
        text = "```openscad\nsphere(5);\n```"
        assert providers.extract_scad(text) == "sphere(5);"

    def test_falls_back_to_plain_text(self):
        assert providers.extract_scad("  cube([9,9,9]);  ") == "cube([9,9,9]);"


class TestClaudeGenerator:
    def test_parses_json_envelope_and_extracts_scad(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            payload = {"result": "```scad\ncube([5,5,5]);\n```", "is_error": False}
            return _completed(stdout=json.dumps(payload))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0)
        assert gen(_request()) == "cube([5,5,5]);"
        cmd = calls[0]
        assert cmd[:2] == ["claude", "-p"]
        assert "--output-format" in cmd and "json" in cmd
        assert "--max-turns" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    def test_retries_once_then_raises(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _completed(returncode=1, stderr="boom")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0)
        with pytest.raises(RuntimeError, match="claude -p failed"):
            gen(_request())
        assert len(calls) == 2

    def test_is_error_payload_retries(self, monkeypatch):
        replies = [
            _completed(stdout=json.dumps({"result": "overloaded", "is_error": True})),
            _completed(stdout=json.dumps({"result": "```scad\ncube(1);\n```"})),
        ]
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: replies.pop(0))
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0)
        assert gen(_request()) == "cube(1);"

    def test_timeout_raises_timeout_error(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", timeout_s=1, retry_sleep_s=0)
        with pytest.raises(TimeoutError):
            gen(_request())


class TestCodexGenerator:
    def test_parses_jsonl_agent_message_and_uses_devnull_stdin(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["stdin"] = kwargs.get("stdin")
            events = [
                {"type": "turn.started"},
                {"type": "item.completed", "item": {"type": "reasoning", "text": "hmm"}},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "```scad\ncylinder(h=4, r=2);\n```"},
                },
            ]
            return _completed(stdout="\n".join(json.dumps(e) for e in events))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_codex_generator("gpt-5.3", retry_sleep_s=0)
        assert gen(_request("codex-gpt-5.3")) == "cylinder(h=4, r=2);"
        assert seen["stdin"] is subprocess.DEVNULL
        assert seen["cmd"][:3] == ["codex", "exec", "--json"]
        assert "read-only" in seen["cmd"]
        assert seen["cmd"][seen["cmd"].index("--model") + 1] == "gpt-5.3"

    def test_ignores_non_json_banner_lines(self, monkeypatch):
        stdout = "codex v1.2\n" + json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "```scad\ncube(2);\n```"}}
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(stdout=stdout))
        gen = providers.make_codex_generator(retry_sleep_s=0)
        assert gen(_request("codex-gpt-5.3")) == "cube(2);"


class TestGeminiAndAgyGenerators:
    def test_gemini_command_shape(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _completed(stdout="```scad\ncube(3);\n```")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_gemini_generator("gemini-2.5-pro", retry_sleep_s=0)
        assert gen(_request("gemini-2.5-pro")) == "cube(3);"
        assert seen["cmd"][0] == "gemini"
        assert seen["cmd"][seen["cmd"].index("-m") + 1] == "gemini-2.5-pro"
        assert "-p" in seen["cmd"]

    def test_agy_prompt_immediately_follows_print_flag(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _completed(stdout="```scad\ncube(4);\n```")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_agy_generator(retry_sleep_s=0)
        assert gen(_request("antigravity-gemini-default")) == "cube(4);"
        cmd = seen["cmd"]
        assert cmd[0] == "agy" and cmd[1] == "--print"
        assert "You are a senior mechanical" in cmd[2]
        assert cmd[cmd.index("--print-timeout") + 1] == "15m"


class TestStubGenerator:
    def test_deterministic_and_jittered_per_model(self):
        gen = providers.make_stub_generator()
        a1 = gen(_request("stub-a"))
        a2 = gen(_request("stub-a"))
        b = gen(_request("stub-b"))
        assert a1 == a2
        assert a1 != b
        assert "cube" in a1

    def test_fixed_program_override(self):
        gen = providers.make_stub_generator("sphere(9);\n")
        assert gen(_request()) == "sphere(9);\n"


class TestDispatch:
    @pytest.mark.parametrize(
        ("model_id", "provider"),
        [
            ("claude-code-sonnet", "claude"),
            ("claude-code-opus-4.8-high", "claude"),
            ("codex-gpt-5.3-codex", "codex"),
            ("gemini-2.5-pro", "gemini"),
            ("antigravity-gemini-default", "agy"),
            ("stub-a", "stub"),
        ],
    )
    def test_provider_for_model_id(self, model_id, provider):
        assert providers.provider_for_model_id(model_id) == provider

    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="cannot infer provider"):
            providers.provider_for_model_id("mystery-model")

    def test_model_name_extraction(self):
        assert providers.model_name_for_model_id("claude-code-sonnet", "claude") == "sonnet"
        assert providers.model_name_for_model_id("codex-gpt-5.3", "codex") == "gpt-5.3"
        assert providers.model_name_for_model_id("gemini-cli", "gemini") is None

    def test_resolve_generator_stub_flag(self):
        gen = providers.resolve_generator("claude-code-sonnet", stub=True)
        assert "cube" in gen(_request())

    def test_resolve_generator_model_map_override(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _completed(stdout=json.dumps({"result": "```scad\ncube(1);\n```"}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.resolve_generator(
            "my-custom-entrant",
            model_map={"my-custom-entrant": {"provider": "claude", "model": "opus"}},
        )
        assert gen(_request("my-custom-entrant")) == "cube(1);"
        assert seen["cmd"][seen["cmd"].index("--model") + 1] == "opus"

    def test_preflight_stub_needs_nothing(self):
        assert providers.preflight_binaries(["stub-a", "stub-b"], stub=True) == []

    def test_model_map_timeout_and_max_turns_reach_the_cli(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["timeout"] = kwargs.get("timeout")
            return _completed(stdout=json.dumps({"result": "```scad\ncube(1);\n```"}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.resolve_generator(
            "claude-code-sonnet",
            model_map={"claude-code-sonnet": {"timeout_s": 1234, "max_turns": 3}},
        )
        assert gen(_request()) == "cube(1);"
        assert seen["timeout"] == 1234
        assert seen["cmd"][seen["cmd"].index("--max-turns") + 1] == "3"

    def test_model_map_timeout_beats_run_level_default(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return _completed(stdout="```scad\ncube(2);\n```")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.resolve_generator(
            "codex-gpt-5.3",
            model_map={"codex-gpt-5.3": {"timeout_s": 111}},
            timeout_s=999,
        )
        gen(_request("codex-gpt-5.3"))
        assert seen["timeout"] == 111

    def test_claude_default_timeout_is_900(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return _completed(stdout=json.dumps({"result": "```scad\ncube(3);\n```"}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0)
        gen(_request())
        assert seen["timeout"] == 900
