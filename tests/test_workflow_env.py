"""Tests for the Workflow Manifest telemetry helpers (makerbench-hwe #295).

Self-contained: imports the stdlib-only module directly from ``scripts/`` so it
does not depend on the wider makerbench package import graph.

Note: this suite covers the lightweight ``workflow_env`` telemetry helpers in
``scripts/workflow_manifest.py``. It is intentionally separate from
``tests/test_workflow_manifest.py``, which covers the pydantic ``WorkflowManifest``
model, Human Intervention Index, and ``.mbc`` certificate contracts.
"""

import os
import sys

import pytest

# Make scripts/ importable without relying on package install.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import workflow_manifest as wm  # noqa: E402


# --- HITL tier enumeration --------------------------------------------------

@pytest.mark.parametrize("tier", [0, 1, 2, 3])
def test_hitl_tiers_0_to_3_valid(tier):
    obj = {
        "orchestration": "Claude Code",
        "backbone_models": ["o3-mini"],
        "tooling": ["Blender MCP"],
        "hitl_tier": tier,
    }
    assert wm.validate_workflow_env(obj) == []


def test_hitl_tier_4_invalid():
    obj = {
        "orchestration": "Claude Code",
        "backbone_models": ["o3-mini"],
        "tooling": ["Blender MCP"],
        "hitl_tier": 4,
    }
    warnings = wm.validate_workflow_env(obj)
    assert warnings
    assert any("hitl_tier" in w for w in warnings)


def test_hitl_tiers_enumeration_meanings():
    assert wm.HITL_TIERS[0] == "Fully-Autonomous"
    assert wm.HITL_TIERS[1] == "Approved tool calls"
    assert wm.HITL_TIERS[2] == "Text nudges"
    assert wm.HITL_TIERS[3] == "Interactive CAD steering"


# --- recipe tag rendering ---------------------------------------------------

def test_recipe_tag_exact_format():
    obj = {
        "orchestration": "Claude Code",
        "backbone_models": ["o3-mini"],
        "tooling": ["Blender MCP"],
        "hitl_tier": 1,
    }
    assert wm.render_recipe_tag(obj) == "[Claude Code] + [o3-mini] + [Blender MCP] (HITL-1)"


def test_recipe_tag_multi_model_and_tool():
    obj = {
        "orchestration": "Codex CLI",
        "backbone_models": ["gpt-5.5", "sonnet-4.6"],
        "tooling": ["OpenSCAD CLI", "Blender MCP"],
        "hitl_tier": 2,
    }
    assert (
        wm.render_recipe_tag(obj)
        == "[Codex CLI] + [gpt-5.5+sonnet-4.6] + [OpenSCAD CLI+Blender MCP] (HITL-2)"
    )


def test_recipe_tag_omits_empty_tool_bracket():
    obj = {
        "orchestration": "Antigravity",
        "backbone_models": ["gemini-3.5-flash"],
        "tooling": [],
        "hitl_tier": 0,
    }
    assert wm.render_recipe_tag(obj) == "[Antigravity] + [gemini-3.5-flash] (HITL-0)"


# --- backward compatibility -------------------------------------------------

def test_entry_without_workflow_env_parses():
    entry = {"task_id": "enclosure_fastened", "seed": 0, "cost_usd": 0.04}
    view = wm.parse_result_entry(entry)
    assert view["task_id"] == "enclosure_fastened"
    assert view["has_workflow_env"] is False
    assert view["workflow_env"] is None
    assert view["recipe_tag"] == wm.AUTONOMOUS_CORE_TAG
    assert view["recipe_tag"] == "[Autonomous Core] (HITL-0)"
    assert view["warnings"] == []


def test_entry_with_workflow_env_parses():
    entry = {
        "task_id": "bracket_press_fit",
        "workflow_env": {
            "orchestration": "Claude Code",
            "backbone_models": ["o3-mini"],
            "tooling": ["Blender MCP"],
            "hitl_tier": 1,
        },
    }
    view = wm.parse_result_entry(entry)
    assert view["has_workflow_env"] is True
    assert view["recipe_tag"] == "[Claude Code] + [o3-mini] + [Blender MCP] (HITL-1)"
    assert view["warnings"] == []


def test_parse_result_entry_tolerates_non_dict():
    view = wm.parse_result_entry(None)
    assert view["recipe_tag"] is None
    assert view["has_workflow_env"] is False


# --- validation flags bad tier / missing fields -----------------------------

def test_validate_flags_bad_tier_string():
    obj = {
        "orchestration": "Claude Code",
        "backbone_models": ["o3-mini"],
        "tooling": [],
        "hitl_tier": "1",
    }
    warnings = wm.validate_workflow_env(obj)
    assert any("hitl_tier" in w for w in warnings)


def test_validate_flags_missing_field():
    obj = {"orchestration": "Claude Code", "hitl_tier": 0}
    warnings = wm.validate_workflow_env(obj)
    assert any("backbone_models" in w for w in warnings)
    assert any("tooling" in w for w in warnings)


def test_validate_flags_wrong_type():
    obj = {
        "orchestration": "Claude Code",
        "backbone_models": "o3-mini",
        "tooling": [],
        "hitl_tier": 0,
    }
    warnings = wm.validate_workflow_env(obj)
    assert any("backbone_models" in w for w in warnings)


# --- dominant_recipe_tag (mb#90) -------------------------------------------

def _wenv(orchestration="Claude Code", backbone_models=None, tooling=None, hitl_tier=1):
    """Minimal valid workflow_env factory for dominant_recipe_tag tests."""
    return {
        "orchestration": orchestration,
        "backbone_models": backbone_models or ["o3-mini"],
        "tooling": tooling or ["Blender MCP"],
        "hitl_tier": hitl_tier,
    }


def test_dominant_recipe_tag_empty_returns_autonomous_core():
    """With no workflow_env objects the autonomous-core sentinel is returned."""
    assert wm.dominant_recipe_tag([]) == wm.AUTONOMOUS_CORE_TAG
    assert wm.dominant_recipe_tag([]) == "[Autonomous Core] (HITL-0)"


def test_dominant_recipe_tag_single_env():
    """A single workflow_env returns its own rendered tag."""
    env = _wenv()
    tag = wm.dominant_recipe_tag([env])
    assert tag == wm.render_recipe_tag(env)
    assert tag == "[Claude Code] + [o3-mini] + [Blender MCP] (HITL-1)"


def test_dominant_recipe_tag_repeated_same_env():
    """Repeated identical envs still return the same rendered tag."""
    env = _wenv()
    tag = wm.dominant_recipe_tag([env, env, env])
    assert tag == wm.render_recipe_tag(env)


def test_dominant_recipe_tag_picks_most_common():
    """When two tags compete, the one with more occurrences wins."""
    common = _wenv(orchestration="Claude Code")
    rare = _wenv(orchestration="Codex CLI")
    # common appears 3 times, rare appears 1 time.
    tag = wm.dominant_recipe_tag([common, rare, common, common])
    assert tag == wm.render_recipe_tag(common)
    assert "Claude Code" in tag


def test_dominant_recipe_tag_tie_broken_lexicographically():
    """Ties are broken lexicographically so results are deterministic."""
    env_a = _wenv(orchestration="AAA-Orchestrator")
    env_b = _wenv(orchestration="ZZZ-Orchestrator")
    tag_a = wm.render_recipe_tag(env_a)
    tag_b = wm.render_recipe_tag(env_b)
    # Each appears exactly once — lexicographic winner should be whichever is larger.
    result = wm.dominant_recipe_tag([env_a, env_b])
    assert result == max(tag_a, tag_b)


def test_dominant_recipe_tag_non_dict_silently_ignored():
    """Non-dict values in the iterable are silently skipped."""
    env = _wenv()
    tag = wm.dominant_recipe_tag([None, "bad", 42, env, None])
    assert tag == wm.render_recipe_tag(env)


def test_dominant_recipe_tag_all_non_dict_returns_autonomous_core():
    """An iterable of only non-dict values behaves like an empty list."""
    assert wm.dominant_recipe_tag([None, "oops", []]) == wm.AUTONOMOUS_CORE_TAG


def test_dominant_recipe_tag_returns_string_never_none():
    """The return value is always a non-None string."""
    for envs in ([], [_wenv()], [None]):
        result = wm.dominant_recipe_tag(envs)
        assert isinstance(result, str)
        assert result
