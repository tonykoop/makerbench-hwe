"""Workflow Manifest telemetry helpers (makerbench-hwe #295).

Stdlib-only utilities for the optional ``workflow_env`` object that may ride
along with a MakerBench ``results.json`` row. ``workflow_env`` records *how* a
result was produced -- the orchestration surface, the backbone model(s), the
tooling/MCP extensions, and the human-in-the-loop (HITL) tier.

Four public helpers:

* :func:`validate_workflow_env` -- returns a list of human-readable warnings
  (empty list == clean). Flags bad ``hitl_tier`` values, missing required
  fields, and wrong field types. Never raises on a malformed object.
* :func:`render_recipe_tag` -- renders the canonical recipe tag, e.g.
  ``[Claude Code] + [o3-mini] + [Blender MCP] (HITL-1)``.
* :func:`parse_result_entry` -- normalizes a results.json row into a small view
  dict. Tolerates a missing ``workflow_env`` (the Autonomous Core / legacy
  case) without raising.
* :func:`dominant_recipe_tag` -- aggregate a list of ``workflow_env`` objects
  (collected across all result rows for a model) into the single most common
  recipe tag; returns ``AUTONOMOUS_CORE_TAG`` when the list is empty.

See ``docs/workflow-manifest.md`` and ``schema/workflow_env.schema.json``.
"""

from __future__ import annotations

# HITL tier enumeration: tier -> human-readable meaning.
HITL_TIERS = {
    0: "Fully-Autonomous",
    1: "Approved tool calls",
    2: "Text nudges",
    3: "Interactive CAD steering",
}

REQUIRED_FIELDS = ("orchestration", "backbone_models", "tooling", "hitl_tier")

# Tag shown for legacy / Autonomous Core rows that carry no workflow_env.
AUTONOMOUS_CORE_TAG = "[Autonomous Core] (HITL-0)"


def validate_workflow_env(obj):
    """Validate a ``workflow_env`` object; return a list of warning strings.

    An empty list means the object is well-formed. This function is lenient by
    design -- it accumulates every problem it finds and never raises, so a
    caller can surface all issues at once.
    """
    warnings = []

    if not isinstance(obj, dict):
        return ["workflow_env must be an object/dict, got {}".format(type(obj).__name__)]

    # Missing required fields.
    for field in REQUIRED_FIELDS:
        if field not in obj:
            warnings.append("missing required field: {}".format(field))

    # orchestration: non-empty string.
    if "orchestration" in obj:
        if not isinstance(obj["orchestration"], str):
            warnings.append("orchestration must be a string")
        elif not obj["orchestration"].strip():
            warnings.append("orchestration must not be empty")

    # backbone_models: non-empty list of strings.
    if "backbone_models" in obj:
        models = obj["backbone_models"]
        if not isinstance(models, list):
            warnings.append("backbone_models must be a list")
        else:
            if not models:
                warnings.append("backbone_models must not be empty")
            for i, m in enumerate(models):
                if not isinstance(m, str):
                    warnings.append("backbone_models[{}] must be a string".format(i))

    # tooling: list of strings (may be empty).
    if "tooling" in obj:
        tooling = obj["tooling"]
        if not isinstance(tooling, list):
            warnings.append("tooling must be a list")
        else:
            for i, t in enumerate(tooling):
                if not isinstance(t, str):
                    warnings.append("tooling[{}] must be a string".format(i))

    # hitl_tier: integer in 0..3.
    if "hitl_tier" in obj:
        tier = obj["hitl_tier"]
        # bool is a subclass of int; reject it explicitly.
        if isinstance(tier, bool) or not isinstance(tier, int):
            warnings.append("hitl_tier must be an integer 0-3")
        elif tier not in HITL_TIERS:
            warnings.append(
                "hitl_tier {} out of range; must be 0-3".format(tier)
            )

    return warnings


def _join_bracket(items):
    """Render a list as ``Item(+Item2+...)`` inside a single bracket pair."""
    parts = [str(x) for x in items if str(x).strip()]
    return "+".join(parts)


def render_recipe_tag(obj):
    """Render the canonical recipe tag for a ``workflow_env`` object.

    Form: ``[Orchestrator] + [Model(+Model2+...)] + [Tool(+Tool2+...)] (HITL-N)``

    Example::

        >>> render_recipe_tag({
        ...     "orchestration": "Claude Code",
        ...     "backbone_models": ["o3-mini"],
        ...     "tooling": ["Blender MCP"],
        ...     "hitl_tier": 1,
        ... })
        '[Claude Code] + [o3-mini] + [Blender MCP] (HITL-1)'

    When ``tooling`` is empty the tool bracket is omitted. Returns ``None`` if
    ``obj`` is not a usable mapping.
    """
    if not isinstance(obj, dict):
        return None

    orch = obj.get("orchestration", "")
    models = obj.get("backbone_models", []) or []
    tooling = obj.get("tooling", []) or []
    tier = obj.get("hitl_tier", 0)

    segments = ["[{}]".format(orch)]
    segments.append("[{}]".format(_join_bracket(models)))
    tool_str = _join_bracket(tooling)
    if tool_str:
        segments.append("[{}]".format(tool_str))

    return "{} (HITL-{})".format(" + ".join(segments), tier)


def parse_result_entry(entry):
    """Normalize a results.json row into a small view dict.

    Returns a dict with keys:

    * ``task_id`` -- the row's task id (or ``None``).
    * ``has_workflow_env`` -- whether the row carried a ``workflow_env``.
    * ``workflow_env`` -- the raw object (or ``None``).
    * ``recipe_tag`` -- the rendered recipe tag, or the Autonomous Core tag
      when no ``workflow_env`` is present, or ``None`` if the row is not a dict.
    * ``warnings`` -- validation warnings for a present ``workflow_env``
      (empty list otherwise).

    Backward-compatible: a row WITHOUT ``workflow_env`` (the Autonomous Core /
    legacy case) parses cleanly and yields ``recipe_tag == AUTONOMOUS_CORE_TAG``.
    Never raises on a malformed row.
    """
    if not isinstance(entry, dict):
        return {
            "task_id": None,
            "has_workflow_env": False,
            "workflow_env": None,
            "recipe_tag": None,
            "warnings": [],
        }

    task_id = entry.get("task_id")
    wf = entry.get("workflow_env")

    if wf is None:
        # Legacy / Autonomous Core row -- tolerate the absence.
        return {
            "task_id": task_id,
            "has_workflow_env": False,
            "workflow_env": None,
            "recipe_tag": AUTONOMOUS_CORE_TAG,
            "warnings": [],
        }

    warnings = validate_workflow_env(wf)
    return {
        "task_id": task_id,
        "has_workflow_env": True,
        "workflow_env": wf,
        "recipe_tag": render_recipe_tag(wf),
        "warnings": warnings,
    }


def dominant_recipe_tag(workflow_envs):
    """Return the most common recipe tag across a list of workflow_env objects.

    Used by ``site/build_data.py`` to pick a single canonical tag per model row
    (mb#90 / mb#299). When multiple distinct ``workflow_env`` objects appear (e.g.
    a stack that alternated tool versions mid-sprint), the tag that appeared in
    the most result rows wins; ties are broken lexicographically so the output is
    deterministic. When ``workflow_envs`` is empty (Autonomous Core / legacy row),
    returns ``AUTONOMOUS_CORE_TAG``.

    Arguments:
        workflow_envs: iterable of raw ``workflow_env`` dict objects collected
            from result rows. Non-dict values are silently ignored.

    Returns:
        str: a recipe-tag string, never ``None``.
    """
    counts = {}
    for wenv in workflow_envs:
        tag = render_recipe_tag(wenv)
        if tag is None:
            continue
        counts[tag] = counts.get(tag, 0) + 1

    if not counts:
        return AUTONOMOUS_CORE_TAG

    # Most common; lexicographic tie-break for determinism.
    return max(counts, key=lambda t: (counts[t], t))
