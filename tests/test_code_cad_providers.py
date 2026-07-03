"""Tests for the Code-CAD Arena local CLI provider adapters."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from makerbench import code_cad_providers as providers
from makerbench.code_cad_generator import GenerationRequest


def _request(
    model_id: str = "claude-code-sonnet",
    *,
    context_tier: str = "blind",
    workspace_dir=None,
) -> GenerationRequest:
    return GenerationRequest(
        model_id=model_id,
        instrument_id="ocarina",
        seed=0,
        spec={"id": "ocarina"},
        prompt="Instrument id: ocarina\nSeed: 0\n",
        prompt_sha256="0" * 64,
        context_tier=context_tier,
        workspace_dir=str(workspace_dir) if workspace_dir is not None else None,
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


class TestBlenderBackendAxis:
    """CAD-backend axis (#601): entrants can emit a bpy script instead of OpenSCAD."""

    def test_extract_candidate_defaults_to_openscad_fence(self):
        text = "```scad\ncube(1);\n```"
        assert providers.extract_candidate(text) == "cube(1);"

    def test_extract_candidate_reads_python_fence_for_blender(self):
        text = "notes\n```python\nbpy.ops.mesh.primitive_cube_add(size=1)\n```\ntrailer"
        assert (
            providers.extract_candidate(text, "blender")
            == "bpy.ops.mesh.primitive_cube_add(size=1)"
        )

    def test_extract_candidate_accepts_bpy_fence_label(self):
        text = "```bpy\nbpy.ops.mesh.primitive_cube_add(size=2)\n```"
        assert (
            providers.extract_candidate(text, "blender")
            == "bpy.ops.mesh.primitive_cube_add(size=2)"
        )

    def test_arena_prompt_uses_bpy_system_and_closing_for_blender(self):
        prompt = providers.arena_prompt(_request(), "blender")
        assert "Blender Python (bpy)" in prompt
        assert "```python block" in prompt
        assert "OpenSCAD" not in prompt

    def test_arena_prompt_defaults_to_openscad(self):
        prompt = providers.arena_prompt(_request())
        assert prompt == providers.arena_prompt(_request(), "openscad")
        assert "```scad block" in prompt

    def test_stub_generator_emits_bpy_script_for_blender_backend(self):
        gen = providers.make_stub_generator(backend="blender")
        text = gen(_request("stub-a"))
        assert "bpy.ops.mesh.primitive_cube_add" in text
        assert "difference()" not in text  # no OpenSCAD leaking into the blender stub

    def test_stub_generator_still_defaults_to_openscad(self):
        gen = providers.make_stub_generator()
        assert "cube" in gen(_request())

    def test_claude_generator_threads_backend_into_prompt_and_extraction(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["prompt"] = cmd[-1]
            payload = {"result": "```python\nbpy.ops.mesh.primitive_cube_add(size=5)\n```"}
            return _completed(stdout=json.dumps(payload))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0, backend="blender")
        assert gen(_request()) == "bpy.ops.mesh.primitive_cube_add(size=5)"
        assert "Blender Python (bpy)" in seen["prompt"]

    def test_resolve_generator_stub_threads_backend(self):
        gen = providers.resolve_generator("claude-code-sonnet", stub=True, backend="blender")
        assert "bpy.ops.mesh.primitive_cube_add" in gen(_request())


class TestSolidworksFusionBackendAxis:
    """CAD-backend axis (#627): SolidWorks VBA / Fusion Python entrants."""

    def test_extract_candidate_reads_vba_fence_for_solidworks(self):
        text = "notes\n```vba\nSub BuildPart()\n    ' body\nEnd Sub\n```\ntrailer"
        assert (
            providers.extract_candidate(text, "solidworks")
            == "Sub BuildPart()\n    ' body\nEnd Sub"
        )

    def test_extract_candidate_reads_fusion_python_fence_for_fusion(self):
        text = "notes\n```fusion-python\ndef build(app, design):\n    pass\n```\ntrailer"
        assert (
            providers.extract_candidate(text, "fusion")
            == "def build(app, design):\n    pass"
        )

    def test_fusion_python_fence_does_not_collide_with_blender_python_fence(self):
        # A bare ```python fence (no "fusion-python" label) must still be
        # extracted correctly for each backend's own regex, and must not
        # silently cross-match content meant for the other backend when a
        # backend is explicitly selected.
        vba_text = "```vba\nSub BuildPart()\nEnd Sub\n```"
        fusion_text = "```fusion-python\ndef build(app, design):\n    pass\n```"
        assert providers.extract_candidate(vba_text, "solidworks") == "Sub BuildPart()\nEnd Sub"
        assert (
            providers.extract_candidate(fusion_text, "fusion")
            == "def build(app, design):\n    pass"
        )
        # Selecting "blender" on VBA/fusion-python source should not extract
        # a VBA/Fusion body as if it were a bpy script.
        assert providers.extract_candidate(vba_text, "blender") != "Sub BuildPart()\nEnd Sub"

    def test_arena_prompt_uses_solidworks_system_and_vba_closing(self):
        prompt = providers.arena_prompt(_request(), "solidworks")
        assert "SolidWorks VBA" in prompt
        assert "```vba block" in prompt
        assert "OpenSCAD" not in prompt

    def test_arena_prompt_uses_fusion_system_and_fusion_python_closing(self):
        prompt = providers.arena_prompt(_request(), "fusion")
        assert "Fusion 360" in prompt
        assert "```fusion-python block" in prompt
        assert "OpenSCAD" not in prompt

    def test_backend_system_and_closing_and_fence_all_register_both_backends(self):
        for backend in ("solidworks", "fusion"):
            assert backend in providers.BACKEND_SYSTEM
            assert backend in providers._CLOSING_INSTRUCTION
            assert backend in providers._FENCE_RE_BY_BACKEND


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


class TestContextTierWorkspaceRouting:
    """#600: a non-blind request's subprocess cwd is the staged workspace,
    never the provider's own fixed isolated blind cwd."""

    def test_claude_uses_workspace_dir_as_cwd_when_present(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cwd"] = kwargs.get("cwd")
            return _completed(stdout=json.dumps({"result": "```scad\ncube(1);\n```"}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        gen(_request(context_tier="repo", workspace_dir=workspace))
        assert seen["cwd"] == str(workspace)

    def test_claude_blind_request_keeps_isolated_cwd(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cwd"] = kwargs.get("cwd")
            return _completed(stdout=json.dumps({"result": "```scad\ncube(1);\n```"}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0)
        gen(_request())  # default blind, no workspace_dir
        assert seen["cwd"] != str(tmp_path)
        assert Path(seen["cwd"]).is_dir()

    def test_codex_dash_c_flag_and_cwd_both_use_workspace(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["cwd"] = kwargs.get("cwd")
            return _completed(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_codex_generator(retry_sleep_s=0)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        gen(_request("codex-gpt-5.5", context_tier="packet", workspace_dir=workspace))
        assert seen["cwd"] == str(workspace)
        assert seen["cmd"][seen["cmd"].index("-C") + 1] == str(workspace)

    def test_gemini_uses_workspace_dir(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cwd"] = kwargs.get("cwd")
            return _completed(stdout="```scad\ncube(1);\n```")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_gemini_generator(retry_sleep_s=0)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        gen(_request("gemini-2.5-pro", context_tier="repo", workspace_dir=workspace))
        assert seen["cwd"] == str(workspace)

    def test_agy_uses_workspace_dir(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cwd"] = kwargs.get("cwd")
            return _completed(stdout="```scad\ncube(1);\n```")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_agy_generator(retry_sleep_s=0)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        gen(_request("antigravity-gemini-default", context_tier="repo", workspace_dir=workspace))
        assert seen["cwd"] == str(workspace)

    def test_arena_prompt_notes_context_tier_when_non_blind(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        blind_prompt = providers.arena_prompt(_request())
        repo_prompt = providers.arena_prompt(
            _request(context_tier="repo", workspace_dir=workspace)
        )
        assert "context tier" not in blind_prompt
        assert "context tier: repo" in repo_prompt

    def test_workspace_text_blob_inlines_staged_docs_for_non_cwd_backends(self, tmp_path):
        from makerbench import code_cad_context_staging as staging

        workspace = tmp_path / "ws"
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "design.md").write_text("brief text\n", encoding="utf-8")
        (repo / "master.scad").write_text("cube(1);\n", encoding="utf-8")
        staging.stage_workspace(
            tier="packet", instrument_id="ocarina", repo_dir=repo, workspace_dir=workspace
        )
        blob = providers._workspace_text_blob(
            _request(context_tier="packet", workspace_dir=workspace)
        )
        assert "design.md" in blob
        assert "brief text" in blob
        assert "master.scad" not in blob  # answer-key suffix never inlined

    def test_workspace_text_blob_empty_for_blind_requests(self):
        assert providers._workspace_text_blob(_request()) == ""


class TestImageTierAttachment:
    """#609: image-conditioned entrant tier — attachment routing."""

    def _staged_image_request(self, tmp_path, *, model_id="claude-code-sonnet"):
        from makerbench import code_cad_context_staging as staging

        image = tmp_path / "hero.png"
        image.write_bytes(b"\x89PNG\r\n")
        workspace = tmp_path / "ws"
        staging.stage_workspace(
            tier="image", instrument_id="ocarina", repo_dir=None,
            workspace_dir=workspace, image_path=image, image_seed=3,
        )
        return _request(model_id, context_tier="image", workspace_dir=workspace), workspace

    def test_staged_image_path_resolves_from_manifest(self, tmp_path):
        request, workspace = self._staged_image_request(tmp_path)
        assert providers._staged_image_path(request) == str(workspace / "reference-image.png")

    def test_staged_image_path_none_for_non_image_tiers(self, tmp_path):
        assert providers._staged_image_path(_request()) is None
        assert providers._staged_image_path(
            _request(context_tier="repo", workspace_dir=tmp_path)
        ) is None

    def test_arena_prompt_notes_inspiration_image(self, tmp_path):
        request, _ = self._staged_image_request(tmp_path)
        prompt = providers.arena_prompt(request)
        assert "inspiration image" in prompt
        assert "context tier: image" in prompt

    def test_claude_receives_staged_image_path_in_prompt(self, tmp_path, monkeypatch):
        request, workspace = self._staged_image_request(tmp_path)
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _completed(json.dumps({"result": "```scad\ncube(1);\n```"}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0)
        gen(request)
        assert str(workspace / "reference-image.png") in seen["cmd"][-1]
        # The installed Claude CLI has no local --image flag. The image stays
        # in the isolated cwd and its exact path is supplied in the prompt;
        # it must not be appended as an undocumented positional argument.
        assert seen["cmd"].count(str(workspace / "reference-image.png")) == 0

    def test_codex_attaches_staged_image_with_documented_flag(self, tmp_path, monkeypatch):
        request, workspace = self._staged_image_request(tmp_path, model_id="codex-gpt-5.5")
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _completed("")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_codex_generator(retry_sleep_s=0)
        gen(request)
        index = seen["cmd"].index("--image")
        assert seen["cmd"][index + 1] == str(workspace / "reference-image.png")
        assert "inspiration image" in seen["cmd"][-1]

    def test_blind_request_has_no_image_attachment(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _completed(json.dumps({"result": "```scad\ncube(1);\n```"}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        gen = providers.make_claude_generator("sonnet", retry_sleep_s=0)
        gen(_request())
        assert seen["cmd"][-1] != ""
        assert not seen["cmd"][-1].endswith(".png")

    def test_openrouter_rejects_image_tier_loudly(self, tmp_path, monkeypatch):
        request, _ = self._staged_image_request(tmp_path, model_id="openrouter-glm-5.2")
        gen = providers.make_openrouter_generator("glm-5.2", retry_sleep_s=0)
        with pytest.raises(RuntimeError, match="context-tier image"):
            gen(request)


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


class TestOpenRouterProvider:
    """API-lane entrant via OpenRouter (#620)."""

    def _request(self, monkeypatch, responses):
        calls = []

        def fake_request(path, payload, *, timeout_s):
            calls.append({"path": path, "payload": payload, "timeout_s": timeout_s})
            result = responses[min(len(calls) - 1, len(responses) - 1)]
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(providers, "_openrouter_request", fake_request)
        monkeypatch.setattr(providers, "_openrouter_slug_cache", {}, raising=False)
        return calls

    def _req(self, *, context_tier="blind", workspace_dir=None):
        return GenerationRequest(
            model_id="openrouter-glm-5.2",
            instrument_id="udu",
            seed=3,
            spec={},
            prompt="spec json here",
            prompt_sha256="0" * 64,
            context_tier=context_tier,
            workspace_dir=str(workspace_dir) if workspace_dir is not None else None,
        )

    def test_non_blind_tier_inlines_staged_docs_since_http_has_no_cwd(self, tmp_path, monkeypatch):
        from makerbench import code_cad_context_staging as staging

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "design.md").write_text("udu design brief\n", encoding="utf-8")
        workspace = tmp_path / "ws"
        staging.stage_workspace(
            tier="packet", instrument_id="udu", repo_dir=repo, workspace_dir=workspace
        )
        calls = self._request(
            monkeypatch,
            [
                {"data": [{"id": "z-ai/glm-5.2"}]},
                {"choices": [{"message": {"content": "```scad\ncube(1);\n```"}}]},
            ],
        )
        gen = providers.resolve_generator("openrouter-glm-5.2")
        gen(self._req(context_tier="packet", workspace_dir=workspace))
        content = calls[1]["payload"]["messages"][1]["content"]
        assert "udu design brief" in content

    def test_prefix_dispatch_and_slug_resolution(self, monkeypatch):
        assert providers.provider_for_model_id("openrouter-glm-5.2") == "openrouter"
        calls = self._request(
            monkeypatch,
            [
                {"data": [{"id": "z-ai/glm-5.2"}, {"id": "z-ai/glm-5"}]},
                {"choices": [{"message": {"content": "```scad\ncube(1);\n```"}}]},
            ],
        )
        gen = providers.resolve_generator("openrouter-glm-5.2")
        assert gen(self._req()) == "cube(1);"
        assert calls[0]["path"] == "/models"
        chat = calls[1]
        assert chat["path"] == "/chat/completions"
        assert chat["payload"]["model"] == "z-ai/glm-5.2"
        assert chat["payload"]["seed"] == 3
        assert chat["payload"]["messages"][0]["content"] == providers.SYSTEM

    def test_full_slug_passthrough_skips_models_call(self, monkeypatch):
        calls = self._request(
            monkeypatch,
            [{"choices": [{"message": {"content": "```scad\nsphere(2);\n```"}}]}],
        )
        gen = providers.make_openrouter_generator("z-ai/glm-5.2")
        assert gen(self._req()) == "sphere(2);"
        assert [c["path"] for c in calls] == ["/chat/completions"]

    def test_ambiguous_slug_is_an_error(self, monkeypatch):
        self._request(
            monkeypatch,
            [{"data": [{"id": "a/glm-5.2"}, {"id": "b/glm-5.2"}]}],
        )
        with pytest.raises(ValueError, match="ambiguous"):
            providers.resolve_openrouter_slug("glm-5.2")

    def test_retries_once_then_raises(self, monkeypatch):
        monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
        self._request(monkeypatch, [RuntimeError("boom")])
        gen = providers.make_openrouter_generator("z-ai/glm-5.2")
        with pytest.raises(RuntimeError, match="failed"):
            gen(self._req())

    def test_timeout_raises_timeout_error(self, monkeypatch):
        self._request(monkeypatch, [TimeoutError("deadline")])
        gen = providers.make_openrouter_generator("z-ai/glm-5.2")
        with pytest.raises(TimeoutError, match="timed out"):
            gen(self._req())

    def test_missing_key_preflights(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        missing = providers.preflight_binaries(["openrouter-glm-5.2"])
        assert missing and "OPENROUTER_API_KEY" in missing[0]
