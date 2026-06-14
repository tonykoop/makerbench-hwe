"""Compatibility shim for Bob's WorkflowManifest schema.

The sprint lane that owns the canonical schema may not be merged yet. Import it
when present; otherwise keep this SDK standalone with a small dataclass carrying
the fields described in mb#89.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


try:  # pragma: no cover - exercised once Bob's schema lands.
    from makerbench.schema import WorkflowManifest as MakerBenchWorkflowManifest
except (ImportError, AttributeError):
    MakerBenchWorkflowManifest = None


@dataclass
class FallbackWorkflowManifest:
    """Local v0.1 WorkflowManifest shape used until makerbench.schema grows one."""

    schema_version: str = "0.1"
    manifest_type: str = "makerbench.workflow_manifest"
    run_id: str = ""
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    stack: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    human_intervention_index: dict[str, Any] = field(default_factory=dict)
    autonomy_ratio: float = 1.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, *_, **__) -> dict[str, Any]:
        return asdict(self)

    def model_dump_json(self, *, indent: int | None = None, **__) -> str:
        return json.dumps(self.model_dump(), indent=indent, sort_keys=True)


WorkflowManifest = MakerBenchWorkflowManifest or FallbackWorkflowManifest


def make_workflow_manifest(payload: dict[str, Any]):
    """Build the installed WorkflowManifest, falling back if signatures differ."""
    if MakerBenchWorkflowManifest is not None:
        try:
            return MakerBenchWorkflowManifest(**payload)
        except Exception:  # noqa: BLE001 - schema branches may drift during sprint integration.
            return FallbackWorkflowManifest(**payload)
    return FallbackWorkflowManifest(**payload)


def manifest_to_dict(manifest: object) -> dict[str, Any]:
    """Return a JSON-ready dictionary for pydantic or dataclass manifests."""
    if hasattr(manifest, "model_dump"):
        return manifest.model_dump(mode="json")  # type: ignore[call-arg]
    if is_dataclass(manifest):
        return asdict(manifest)
    if isinstance(manifest, dict):
        return manifest
    raise TypeError(f"Unsupported manifest type: {type(manifest).__name__}")
