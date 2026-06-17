"""Tests for the Workflow Manifest telemetry helpers (makerbench-hwe #295).

Self-contained: imports the stdlib-only module directly from ``scripts/`` so it
does not depend on the wider makerbench package import graph.
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
