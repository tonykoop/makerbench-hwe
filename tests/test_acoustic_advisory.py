"""Advisory acoustic check prototype (#800): analytic fixtures and scoring isolation."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from makerbench import acoustic_advisory as acoustic
from makerbench import code_cad_arena_runner as runner
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts, _normalize_gate_result

REGISTRY = Path(__file__).resolve().parents[1] / "tasks" / "code_cad_arena" / "registry.json"

KENA_LIKE = {
    "id": "kena-like", "task_kind": "single_part_pipe", "envelope_mm": [40, 40, 500],
    "min_bodies": 1, "min_wall_mm": 1.0,
    "constraints": {"fundamental": "G4 (~392 Hz)", "bore": "open cylindrical, open both ends"},
}


def _tube(length=400.0, r_in=9.0, r_out=12.0, sections=128):
    return trimesh.creation.annulus(r_min=r_in, r_max=r_out, height=length, sections=sections)


def _with_finger_holes(mesh, zs, radius=3.0, r_out=12.0):
    for z in zs:
        hole = trimesh.creation.cylinder(radius=radius, height=10, sections=32)
        hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        hole.apply_translation([r_out, 0, z])
        mesh = mesh.difference(hole)
    return mesh


# --- analytic fixtures ----------------------------------------------------------------------


def test_open_pipe_formula_matches_hand_arithmetic():
    # L = 400 mm, r = 9 mm, a = 0.6, T = 20 C: c = 331.3*sqrt(1+20/273.15) = 343.21 m/s,
    # L_eff = 0.400 + 2*0.6*0.009 = 0.4108 m, f = 343.21 / 0.8216 = 417.73 Hz.
    c = 331.3 * math.sqrt(1 + 20 / 273.15)
    assert acoustic.open_pipe_hz(400.0, 9.0) == pytest.approx(c / (2 * 0.4108), rel=1e-9)
    assert acoustic.open_pipe_hz(400.0, 9.0) == pytest.approx(417.73, abs=0.02)


def test_helmholtz_formula_matches_hand_arithmetic():
    # V = 130 cm^3, opening r = 5 mm, wall/neck L = 3 mm, a = 0.85:
    # A = pi*0.005^2 = 7.854e-5 m^2, L_eff = 0.003 + 2*0.85*0.005 = 0.0115 m,
    # f = 343.21/(2 pi) * sqrt(7.854e-5 / (1.3e-4 * 0.0115)) = 54.62 * 7.248 = 395.9 Hz.
    f = acoustic.helmholtz_hz(130_000.0, 5.0, 3.0)
    assert f == pytest.approx(395.9, abs=0.5)
    # Scaling law: quadrupling the volume halves the frequency.
    assert acoustic.helmholtz_hz(520_000.0, 5.0, 3.0) == pytest.approx(f / 2, rel=1e-9)


@pytest.mark.parametrize("length, r_in", [(400.0, 9.0), (250.0, 6.5), (600.0, 11.0)])
def test_mesh_measured_open_pipe_is_within_tolerance_of_the_analytic_answer(length, r_in):
    geometry = acoustic.measure_open_pipe(_tube(length=length, r_in=r_in))
    assert geometry["ok"], geometry
    assert geometry["length_mm"] == pytest.approx(length, rel=1e-6)
    # Faceted 128-gon bore: polygon area radius is within 0.1% of the nominal radius.
    assert geometry["radius_mm"] == pytest.approx(r_in, rel=1e-3)
    estimate = acoustic.open_pipe_hz(geometry["length_mm"], geometry["radius_mm"])
    assert abs(acoustic.cents(estimate, acoustic.open_pipe_hz(length, r_in))) < 1.0


def test_bore_is_measured_through_finger_holes_and_any_orientation():
    pipe = _with_finger_holes(_tube(), zs=(-120, -60, 0, 60, 120))
    lying = pipe.copy()
    lying.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    for mesh in (pipe, lying):
        geometry = acoustic.measure_open_pipe(mesh)
        assert geometry["ok"], geometry
        assert geometry["length_mm"] == pytest.approx(400.0, rel=1e-6)
        assert geometry["radius_mm"] == pytest.approx(9.0, rel=2e-3)


def test_solid_rod_has_no_bore():
    rod = trimesh.creation.cylinder(radius=10, height=300, sections=64)
    assert acoustic.measure_open_pipe(rod) == {
        "ok": False, "error": "no through bore found (0 of 9 stations)"}


# --- advisory result ------------------------------------------------------------------------


def test_consistent_and_inconsistent_pipes_are_labelled_advisory():
    target = 392.0
    # Choose L so the nominal estimate lands on 392 Hz for r = 9 mm.
    c = acoustic.speed_of_sound_ms(20.0)
    length = (c / (2 * target)) * 1000.0 - 2 * 0.6 * 9.0
    good = acoustic.advise(KENA_LIKE, _tube(length=length, r_in=9.0))
    assert good["label"] == "advisory" and good["affects_scoring"] is False
    assert good["status"] == "consistent"
    assert abs(good["error_cents"]) < 2.0
    assert good["band_hz"][0] < target < good["band_hz"][1]
    assert any("end-correction" in a for a in good["assumptions"])

    short = acoustic.advise(KENA_LIKE, _tube(length=length * 0.8, r_in=9.0))
    assert short["status"] == "inconsistent" and short["error_cents"] > 300


@pytest.mark.parametrize("spec, needle", [
    ({"task_kind": "single_part_vessel", "constraints": {"target_note": "A4 (440 Hz)"}},
     "outside the open-pipe model"),
    ({"task_kind": "multi_part_assembly", "constraints": {}}, "outside the open-pipe model"),
    ({"task_kind": "single_part_pipe", "constraints": {"fundamental": "G4 (~392 Hz)",
                                                        "bore": "closed at one end"}},
     "not declared open at both ends"),
    ({"task_kind": "single_part_pipe", "constraints": {"bore": "open both ends"}},
     "no target pitch in Hz"),
])
def test_other_families_report_not_modelled(spec, needle):
    result = acoustic.advise(spec, _tube())
    assert result["status"] == "not modelled" and needle in result["reason"]
    assert result["label"] == "advisory"


def test_shipped_registry_models_the_kena_and_not_the_ocarina():
    specs = {s["id"]: s for s in json.loads(REGISTRY.read_text())["instruments"]}
    assert acoustic.modelled_reason(specs["kena"]) is None
    assert acoustic.parse_target_hz(specs["kena"]["constraints"]) == 392.0
    assert acoustic.modelled_reason(specs["ocarina"]) is not None
    assert all(acoustic.modelled_reason(s) is not None for s in specs.values() if s["family"] == "strings")


# --- scoring isolation ----------------------------------------------------------------------


def _gate(tmp_path, mesh, spec):
    stl = tmp_path / "output.stl"
    mesh.export(stl)
    png = tmp_path / "preview.png"
    png.write_bytes(b"png")
    context = ObjectiveContext(trial_id="t", model_id="m", instrument_id=spec["id"], seed=0,
                               scad_path=tmp_path / "in.scad",
                               artifacts=RenderArtifacts(stl_path=stl, png_path=png))
    return runner.mesh_objective_gate(spec, part_module_counter=lambda _p: 0)(context)


@pytest.mark.parametrize("length_scale", [1.0, 0.8])
def test_advisory_never_changes_sub_scores_or_pass_rate(tmp_path, monkeypatch, length_scale):
    c = acoustic.speed_of_sound_ms(20.0)
    mesh = _tube(length=((c / 784.0) * 1000.0 - 10.8) * length_scale, r_in=9.0)

    with_advisory = _gate(tmp_path, mesh, KENA_LIKE)
    monkeypatch.setattr(acoustic, "advise", lambda spec, mesh: {"status": "stubbed"})
    without = _gate(tmp_path, mesh, KENA_LIKE)

    assert with_advisory["advisory"]["acoustic"]["label"] == "advisory"
    assert with_advisory["sub_scores"] == without["sub_scores"]
    assert with_advisory["objective_pass_rate"] == without["objective_pass_rate"]
    assert with_advisory["passed"] == without["passed"]
    assert "acoustic" not in with_advisory["sub_scores"]


def test_advisory_failure_is_reported_and_scoring_still_completes(tmp_path, monkeypatch):
    def boom(spec, mesh):
        raise RuntimeError("estimator exploded")

    monkeypatch.setattr(acoustic, "advise", boom)
    result = _gate(tmp_path, _tube(), KENA_LIKE)
    assert result["advisory"]["acoustic"]["status"] == "error"
    assert "estimator exploded" in result["advisory"]["acoustic"]["error"]
    assert "objective_pass_rate" in result


def test_normalized_objective_keeps_the_advisory(tmp_path):
    normalized = _normalize_gate_result(_gate(tmp_path, _tube(), KENA_LIKE))
    assert normalized["advisory"]["acoustic"]["label"] == "advisory"
    stringed = _normalize_gate_result(_gate(tmp_path, _tube(), {**KENA_LIKE,
                                                                "task_kind": "multi_part_assembly"}))
    assert stringed["advisory"]["acoustic"]["status"] == "not modelled"
