"""Tests for the OpenAI build123d/STEP adapter."""

from __future__ import annotations

from makerbench.schema import TaskSpec

from agents import openai_agent, openai_build123d_agent


def test_extract_python_accepts_fenced_or_plain_text():
    assert openai_build123d_agent._extract_python("```python\nprint('x')\n```") == "print('x')"
    assert openai_build123d_agent._extract_python("print('x')") == "print('x')"


def test_agent_uses_build123d_system_and_restores_base_system(monkeypatch):
    seen: dict[str, str] = {}

    def fake_call(prompt: str):
        seen["prompt"] = prompt
        seen["system"] = openai_agent.SYSTEM
        return (
            "```python\nfrom pathlib import Path\nPath('output.step').write_text('STEP')\n```",
            {"id": "resp_123"},
        )

    monkeypatch.setattr(openai_agent, "_call_openai", fake_call)
    original_system = openai_agent.SYSTEM
    spec = TaskSpec(
        task_id="cadgenbench:101",
        seed=0,
        params={},
        brief="CADGenBench sample 101. Produce output.step.",
        allowed_tools=[],
    )

    attempt = openai_build123d_agent.agent(
        spec,
        track="blind",
        tools={},
        perceive=None,
        budget=1,
    )

    assert "build123d" in seen["system"]
    assert "output.step" in seen["prompt"]
    assert attempt.source.startswith("from pathlib import Path")
    assert attempt.trace[0]["step"] == "draft"
    assert attempt.trace[0]["response_id"] == "resp_123"
    assert attempt.trace[0]["out_chars"] > 0
    assert openai_agent.SYSTEM == original_system
