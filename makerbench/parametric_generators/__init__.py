"""Deterministic parametric instrument generators for the `parametric` backend.

Each generator is a ``build(spec) -> trimesh.Trimesh`` returning a watertight
mesh, built from code (parametric tube-sweep networks, revolved bells, ...) so
the same instrument yields byte-identical geometry every run — no LLM, no live
CAD seat. Register by exact instrument id and/or by family (id wins).
"""
from __future__ import annotations

from . import trumpet

# instrument_id -> build fn
REGISTRY = {
    "trumpet-sheetmetal": trumpet.build,
}

# family -> build fn (fallback when no exact id match)
FAMILY_REGISTRY = {
    "brass": trumpet.build,
}


def generator_for(instrument_id: str, spec: dict | None = None):
    """Resolve a generator by exact id, then by family; None if unavailable."""
    if instrument_id in REGISTRY:
        return REGISTRY[instrument_id]
    family = str((spec or {}).get("family", "")).lower()
    return FAMILY_REGISTRY.get(family)
