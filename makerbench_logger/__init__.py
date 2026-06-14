"""makerbench-logger — drop-in SDK that emits a WorkflowManifest (mb#92).

Wrap an agent run, log tool-calls with their human-steering level, and emit a
schema-valid ``workflow_manifest.json`` (the mb#89 contract). Zero hard
dependencies; works standalone beside any agent stack and upgrades to
authoritative validation when ``makerbench.schema`` is installed.

    from makerbench_logger import WorkflowLogger

    with WorkflowLogger(task_id="bracket_v1", seed=0, framework="blender-mcp") as log:
        log.tool_call("blender.add_cube", {"size": 20})
        log.tool_call("blender.bevel", {"width": 1}, steering="L1")
    log.emit("workflow_manifest.json")
"""

from __future__ import annotations

from .logger import WorkflowLogger
from .manifest import (
    HII_L0,
    HII_L1,
    HII_L2,
    HII_LEVELS,
    SCHEMA_VERSION,
    HumanInterventionIndex,
    Metrics,
    ProvenanceTrace,
    Stack,
    ToolCall,
    WorkflowManifest,
    normalize_hii,
    validate_with_schema,
)

__all__ = [
    "WorkflowLogger",
    "WorkflowManifest",
    "Stack",
    "Metrics",
    "ProvenanceTrace",
    "ToolCall",
    "HumanInterventionIndex",
    "normalize_hii",
    "validate_with_schema",
    "HII_L0",
    "HII_L1",
    "HII_L2",
    "HII_LEVELS",
    "SCHEMA_VERSION",
]

__version__ = "0.1.0"
