"""Deterministic parametric CAD backend for the arena.

``arena run --backend parametric`` builds each entrant by running a registered
deterministic Python generator (parametric geometry -> watertight mesh) instead
of an LLM-authored program (code-CAD backends) or a live agent (``*-live``
backends). It is repeatable and seed-independent: the same instrument yields
byte-identical geometry every run, with no model call and no CAD seat — so it is
the stable control lane for the arena and a smoke test for the objective gate.

The mesh is wrapped through the same objective gate and trial-row schema every
other backend uses (via a pass-through compiler + ``evaluate_objective_trial``),
so votes / Elo / turntable / export need zero special-casing. Entrants are
tagged ``tier: "parametric"`` so they can be reported separately from blind
single-shot text entrants and never folded into that pool.

NOTE: the arena scores meshes; this backend emits a mesh for scoring/rendering.
It is NOT the fabrication deliverable — a real sheet-metal build needs a native
B-rep model (sweep/loft/revolve through the CAD connector), which is a separate
path. This backend proves the geometry recipe deterministically.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Mapping

from .code_cad_arena_runner import evaluate_objective_trial, mesh_objective_gate
from .code_cad_generator import instrument_spec_from_registry
from .code_cad_objective import RenderArtifacts
from .code_cad_orchestrator import ArenaTrial, TrialExecutor
from .parametric_generators import generator_for

PARAMETRIC_BACKEND = "parametric"
_SOURCE_DIR = Path(__file__).with_name("parametric_generators")


def unavailable_instruments(
    registry: Mapping[str, object], instrument_ids
) -> list[str]:
    """Instrument ids with no registered generator (by id or family)."""
    missing = []
    for iid in instrument_ids:
        try:
            spec = instrument_spec_from_registry(registry, iid)
        except Exception:  # noqa: BLE001 — unknown id -> unavailable
            spec = None
        if generator_for(iid, spec) is None:
            missing.append(iid)
    return missing


def make_parametric_execute_trial(
    *,
    registry: Mapping[str, object],
    run_dir: Path,
    gate_factory: Callable[[Mapping[str, object]], Callable] = mesh_objective_gate,
) -> TrialExecutor:
    """A TrialExecutor that builds each entrant from its deterministic generator.

    Row shape is identical to the other backends (pass-through STL compiler +
    ``evaluate_objective_trial``); adds ``backend`` and ``tier: "parametric"``.
    """

    def execute(trial: ArenaTrial) -> dict:
        spec = instrument_spec_from_registry(registry, trial.instrument_id)
        generator = generator_for(trial.instrument_id, spec)
        if generator is None:
            raise KeyError(f"no parametric generator for {trial.instrument_id!r}")

        gen_dir = run_dir / "gen" / trial.trial_id
        gen_dir.mkdir(parents=True, exist_ok=True)
        mesh = generator(spec)
        stl = gen_dir / "output.stl"
        mesh.export(stl)

        def compiler(_src: Path, out_dir: Path) -> RenderArtifacts:
            out_dir.mkdir(parents=True, exist_ok=True)
            staged = out_dir / "output.stl"
            shutil.copyfile(stl, staged)
            return RenderArtifacts(stl_path=staged, png_path=out_dir / "preview.missing.png")

        payload = evaluate_objective_trial(
            trial_id=trial.trial_id,
            model_id=trial.model_id,
            instrument_id=trial.instrument_id,
            seed=trial.seed,
            scad_path=stl,                    # the generated STL stands in as provenance
            out_dir=run_dir / "render" / trial.trial_id,
            objective_gate=gate_factory(spec),
            compiler=compiler,
        )
        payload["rep"] = trial.rep
        payload["backend"] = PARAMETRIC_BACKEND
        payload["tier"] = "parametric"
        payload["gen"] = {
            "generator": getattr(generator, "__module__", "?"),
            "source_dir": _SOURCE_DIR.as_posix(),
            "stl_path": stl.as_posix(),
        }
        return payload

    return execute
