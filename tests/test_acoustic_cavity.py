"""Acoustic cavity checks (#980): vessel-flute Helmholtz estimate, bore continuity and taper.

Known-good and known-bad fixtures for every check, the explanation shape of every failure,
scoring isolation, and the per-tier advisory report.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import trimesh

from makerbench import acoustic_advisory as acoustic
from makerbench import advisory_report
from makerbench import code_cad_arena_runner as runner
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts, _normalize_gate_result

REGISTRY = Path(__file__).resolve().parents[1] / "tasks" / "code_cad_arena" / "registry.json"
EXPLANATION_KEYS = {"check", "measured", "threshold", "unit", "requires", "body_id", "detail"}

R_IN, R_OUT, WINDOW, WALL = 30.0, 34.0, (9.0, 5.0), 4.0
CAVITY_MM3 = 4.0 / 3.0 * math.pi * R_IN ** 3  # 113.1 cm^3


def _vessel(finger_holes: int = 4) -> trimesh.Trimesh:
    """A spherical vessel flute: 4 mm wall, a 9 x 5 mm voicing window and 8 mm finger holes."""
    shell = trimesh.creation.icosphere(subdivisions=4, radius=R_OUT).difference(
        trimesh.creation.icosphere(subdivisions=4, radius=R_IN))
    window = trimesh.creation.box(extents=[WINDOW[0], WINDOW[1], 20])
    window.apply_translation([0, 0, R_OUT])
    shell = shell.difference(window)
    for i in range(finger_holes):
        hole = trimesh.creation.cylinder(radius=4, height=20)
        hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        hole.apply_translation([-15 + 10 * i, R_OUT, 0])
        shell = shell.difference(hole)
    return shell


def _vessel_spec(target_hz: float, **constraints) -> dict:
    return {"id": "vessel-like", "task_kind": "single_part_vessel", "envelope_mm": [100, 100, 100],
            "min_bodies": 1, "min_wall_mm": 1.0,
            "constraints": {"acoustic_model": "helmholtz_resonator", "target_note": f"({target_hz:g} Hz)",
                            "wall_thickness_mm": WALL, "voicing_window_mm": list(WINDOW), **constraints}}


def _matched_target() -> float:
    return acoustic.helmholtz_hz(CAVITY_MM3, math.sqrt(WINDOW[0] * WINDOW[1] / math.pi), WALL)


@pytest.fixture(scope="module")
def vessel():
    return _vessel()


def _explained(failures):
    assert failures, "an inconsistent advisory must explain itself"
    for failure in failures:
        assert EXPLANATION_KEYS <= set(failure), failure
        assert failure["detail"]


# --- vessel flutes ----------------------------------------------------------------------------


def test_cavity_volume_matches_the_analytic_sphere_through_open_holes(vessel):
    cavity = acoustic.measure_cavity(vessel)
    assert cavity["ok"], cavity
    assert cavity["volume_mm3"] == pytest.approx(CAVITY_MM3, rel=0.02)
    lo, hi = cavity["volume_range_mm3"]
    assert lo <= CAVITY_MM3 <= hi


def test_solid_body_has_no_cavity():
    ball = trimesh.creation.icosphere(subdivisions=3, radius=30)
    cavity = acoustic.measure_cavity(ball)
    assert cavity["ok"] is False and "no enclosed cavity" in cavity["error"]
    result = acoustic.advise(_vessel_spec(440.0), ball)
    assert result["status"] == "not measurable" and result["affects_scoring"] is False
    _explained(result["failures"])


def test_good_vessel_is_consistent(vessel):
    target = _matched_target()
    result = acoustic.advise(_vessel_spec(target, chamber_volume_cm3=CAVITY_MM3 / 1000), vessel)
    assert result["label"] == "advisory" and result["affects_scoring"] is False
    assert result["family"] == "vessel_flute_helmholtz"
    assert result["status"] == "consistent" and result["failures"] == []
    assert abs(result["error_cents"]) < 25
    assert result["band_hz"][0] < target < result["band_hz"][1]
    assert result["volume_check"]["status"] == "consistent"
    assert result["notes"] == []


def test_mistuned_vessel_is_inconsistent_with_an_explanation(vessel):
    result = acoustic.advise(_vessel_spec(_matched_target() * 1.5), vessel)  # target a fifth too high
    assert result["status"] == "inconsistent" and result["pitch_status"] == "inconsistent"
    _explained(result["failures"])
    (failure,) = result["failures"]
    assert failure["check"] == "helmholtz_pitch" and failure["unit"] == "Hz"
    assert "cents from the" in failure["detail"]


def test_wrong_chamber_volume_is_flagged(vessel):
    result = acoustic.advise(_vessel_spec(_matched_target(), chamber_volume_cm3=200), vessel)
    assert result["status"] == "inconsistent" and result["pitch_status"] == "consistent"
    (failure,) = result["failures"]
    assert failure["check"] == "cavity_volume" and failure["threshold"] == 200
    assert failure["measured"] == pytest.approx(CAVITY_MM3 / 1000, rel=0.02)


def test_spec_whose_declared_geometry_misses_its_own_target_is_noted(vessel):
    # Declared 113 cm^3 and a 9x5 window predict ~337 Hz; a 440 Hz target cannot be reached by
    # a candidate built to that geometry, and the advisory says so.
    result = acoustic.advise(_vessel_spec(440.0, chamber_volume_cm3=CAVITY_MM3 / 1000), vessel)
    assert result["status"] == "inconsistent"
    assert result["spec_estimate_hz"] == pytest.approx(_matched_target(), rel=1e-3)
    assert any("declared geometry disagree" in note for note in result["notes"])


@pytest.mark.parametrize("constraints, needle", [
    ({"target_note": "A4 (440 Hz)", "voicing_window_mm": [9, 5]}, "acoustic_model"),
    ({"acoustic_model": "helmholtz_resonator", "voicing_window_mm": [9, 5]}, "no target pitch"),
    ({"acoustic_model": "helmholtz_resonator", "target_note": "A4 (440 Hz)"}, "no voicing window"),
])
def test_vessels_missing_inputs_are_not_modelled(constraints, needle):
    spec = {"task_kind": "single_part_vessel", "constraints": constraints}
    assert needle in acoustic.vessel_reason(spec)
    assert acoustic.advise(spec, _vessel(0))["status"] == "not modelled"


def test_shipped_ocarina_spec_is_modelled_and_its_target_is_noted():
    specs = {s["id"]: s for s in json.loads(REGISTRY.read_text())["instruments"]}
    assert acoustic.vessel_reason(specs["ocarina"]) is None
    assert acoustic.declared_window_area_mm2(specs["ocarina"]["constraints"]) == 45.0
    assert acoustic.vessel_reason(specs["udu"]) is not None  # a drum, not a Helmholtz flute


# --- bore continuity and taper ----------------------------------------------------------------

KENA_LIKE = {
    "id": "kena-like", "task_kind": "single_part_pipe", "envelope_mm": [40, 40, 500],
    "min_bodies": 1, "min_wall_mm": 1.0,
    "constraints": {"fundamental": "G4 (~392 Hz)", "bore": "open cylindrical, open both ends"},
}


def _tube(length=400.0, r_in=9.0, r_out=12.0, z0=0.0):
    tube = trimesh.creation.annulus(r_min=r_in, r_max=r_out, height=length, sections=96)
    tube.apply_translation([0, 0, z0])
    return tube


def _with_finger_holes(mesh, zs, radius=3.0, r_out=12.0):
    for z in zs:
        hole = trimesh.creation.cylinder(radius=radius, height=10, sections=32)
        hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        hole.apply_translation([r_out, 0, z])
        mesh = mesh.difference(hole)
    return mesh


def _matched_length(target=392.0, r=9.0):
    return (acoustic.speed_of_sound_ms(20.0) / (2 * target)) * 1000.0 - 2 * 0.6 * r


def test_clean_tube_with_finger_holes_has_a_continuous_cylindrical_bore():
    length = _matched_length()
    # holes centred on bore stations, so those stations read as side holes, not faults
    zs = [-length / 2 + f * length for f in (0.55, 0.65, 0.75)]
    result = acoustic.advise(KENA_LIKE, _with_finger_holes(_tube(length=length), zs))
    assert result["status"] == "consistent", result["failures"]
    bore = result["bore"]
    assert bore["status"] == "consistent" and bore["declared_cylindrical"] is True
    kinds = {s["kind"] for s in bore["stations"]}
    assert kinds <= {"bore", "side_hole"} and "side_hole" in kinds
    assert abs(bore["taper_change_mm"]) < 0.1


def test_blocked_bore_is_flagged_at_its_station():
    tube = _tube()
    plug = trimesh.creation.cylinder(radius=10.0, height=30, sections=96)  # overlaps the wall
    blocked = trimesh.boolean.union([tube, plug], engine="manifold")
    result = acoustic.advise(KENA_LIKE, blocked)
    assert result["status"] == "inconsistent" and result["bore"]["status"] == "inconsistent"
    _explained(result["failures"])
    (failure,) = [f for f in result["failures"] if f["check"] == "bore_continuity"]
    assert failure["measured"] == "blocked" and failure["station"] == 0.5
    assert "blocked" in failure["detail"]


def test_broken_body_is_flagged_as_missing_stations():
    broken = trimesh.util.concatenate([_tube(length=180, z0=-110), _tube(length=180, z0=110)])
    bore = acoustic.bore_report(KENA_LIKE, broken, body_id="body")
    missing = [f for f in bore["failures"] if f["measured"] == "missing"]
    assert missing and all(f["check"] == "bore_continuity" for f in missing)
    assert bore["status"] == "inconsistent"


def test_radius_step_breaks_continuity():
    stepped = trimesh.boolean.union([_tube(length=200, r_in=9.0, z0=-100),
                                     _tube(length=200, r_in=5.0, z0=100)], engine="manifold")
    bore = acoustic.bore_report(KENA_LIKE, stepped, body_id="body")
    steps = [f for f in bore["failures"] if f["check"] == "bore_continuity" and f["unit"] == "mm"]
    assert len(steps) == 1 and steps[0]["measured"] == pytest.approx(4.0, abs=0.1)
    _explained(steps)


def _conical(length=400.0, r_small=6.0, r_large=10.0, r_out=13.0):
    outer = trimesh.creation.cylinder(radius=r_out, height=length, sections=96)
    # a frustum bore: the convex hull of the two end circles
    n = 96
    theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
    bottom = np.c_[r_small * np.cos(theta), r_small * np.sin(theta), np.full(n, -length / 2 - 1)]
    top = np.c_[r_large * np.cos(theta), r_large * np.sin(theta), np.full(n, length / 2 + 1)]
    bore = trimesh.convex.convex_hull(np.vstack([bottom, top]))
    return outer.difference(bore)


def test_taper_on_a_declared_cylindrical_bore_is_flagged_and_a_declared_conical_one_is_not():
    cone = _conical()
    flagged = acoustic.bore_report(KENA_LIKE, cone, body_id="body")
    (taper,) = [f for f in flagged["failures"] if f["check"] == "bore_taper"]
    # the fitted slope (4 mm over the 402 mm cutter) times the 400 mm bore length
    assert taper["measured"] == pytest.approx(4.0 * 400 / 402, rel=0.02)
    _explained([taper])
    conical = {**KENA_LIKE, "constraints": {**KENA_LIKE["constraints"], "bore": "conical, open both ends"}}
    report = acoustic.bore_report(conical, cone, body_id="body")
    assert report["declared_cylindrical"] is False
    assert not [f for f in report["failures"] if f["check"] == "bore_taper"]
    assert report["taper_slope_mm_per_mm"] > 0
    # #994 review: no other check may fail a clean declared cone either
    assert report["status"] == "consistent", report["failures"]


def _revolved(zs, r_in, wall=3.0, sections=96):
    """A pipe of revolution about z with bore radius ``r_in`` at each ``zs``."""
    zs, r_in = np.asarray(zs, float), np.asarray(r_in, float)
    loop = np.vstack([np.c_[r_in, zs], np.c_[r_in[::-1] + wall, zs[::-1]]])
    mesh = trimesh.creation.revolve(np.vstack([loop, loop[:1]])[::-1], sections=sections)
    assert mesh.is_watertight and mesh.volume > 0
    return mesh


CONICAL_OPEN = {**KENA_LIKE, "constraints": {**KENA_LIKE["constraints"], "bore": "conical, open both ends"}}


def test_steep_cone_narrowing_to_its_open_tip_is_fully_consistent():
    """#994 review: a 400 mm cone, bore radius 2 -> 15 mm. The end probes follow the bore to
    the narrow tip instead of keeping the first station's radius and hitting the wall."""
    z = np.linspace(0, 400, 81)
    report = acoustic.bore_report(CONICAL_OPEN, _revolved(z, 2 + 13 * z / 400), body_id="body")
    assert report["status"] == "consistent", report["failures"]
    assert report["through_path"] == []


def test_smooth_bell_flare_is_not_a_step_but_an_abrupt_one_is():
    """#994 review: an 18 mm bore flaring smoothly to 36 mm over the last 80 mm changes more
    than the step tolerance between stations, but over the whole span, not at a step."""
    z = np.linspace(0, 400, 401)
    flare = np.where(z < 320, 9.0, 9.0 + 9.0 * ((z - 320) / 80) ** 2)
    bell = {**KENA_LIKE, "constraints": {**KENA_LIKE["constraints"], "bore": "tapered bell, open both ends"}}
    report = acoustic.bore_report(bell, _revolved(z, flare), body_id="body")
    assert report["status"] == "consistent", report["failures"]
    # the same 9 -> 18 mm radius change made as a step at 360 mm is still a discontinuity
    stepped = np.where(z < 360, 9.0, 18.0)
    zs = np.r_[z[z < 360], 360.0, 360.0, z[z > 360]]
    rs = np.r_[stepped[z < 360], 9.0, 18.0, stepped[z > 360]]
    report = acoustic.bore_report(bell, _revolved(zs, rs), body_id="body")
    steps = [f for f in report["failures"] if f["check"] == "bore_continuity" and f["unit"] == "mm"]
    assert len(steps) == 1 and "abruptly" in steps[0]["detail"]
    _explained(steps)


def test_lip_narrowing_an_open_end_is_still_a_closed_end():
    """The end probes follow a taper, not a lip: an 18 mm bore whose last 2 mm narrows to
    8 mm is still closed at that end."""
    z = np.array([0.0, 398.0, 398.0, 400.0])
    report = acoustic.bore_report(KENA_LIKE, _revolved(z, [9.0, 9.0, 4.0, 4.0]), body_id="body")
    assert [(b["kind"], b["between"][1]) for b in report["through_path"]] == [("closed_end", 1.0)]


DUDUK_LIKE = {"id": "duduk-like", "task_kind": "multi_part_assembly", "assembly": True, "min_bodies": 2,
              "envelope_mm": [80, 80, 420], "min_wall_mm": 1.5,
              "constraints": {"body_length_mm": 336, "bore_id_mm": 12, "wall_mm": 3.5}}


def _duduk(r_in=6.0):
    body = _tube(length=336, r_in=r_in, r_out=r_in + 3.5)
    reed = trimesh.creation.box(extents=[14, 3, 60])
    reed.apply_translation([0, 0, 168 + 31])
    return trimesh.util.concatenate([body, reed])


def test_assembly_with_a_declared_bore_is_profiled_on_its_bore_body():
    good = acoustic.advise(DUDUK_LIKE, _duduk())
    assert good["family"] == "pipe_bore" and good["pitch_status"] == "not modelled"
    assert good["status"] == "consistent", good["failures"]
    assert good["bore"]["median_radius_mm"] == pytest.approx(6.0, rel=0.01)
    bad = acoustic.advise(DUDUK_LIKE, _duduk(r_in=4.0))
    (failure,) = bad["failures"]
    assert failure["check"] == "bore_diameter" and failure["body_id"] == "bore body"
    assert failure["measured"] == pytest.approx(8.0, rel=0.01) and failure["threshold"] == 12


def _plugged(z, thickness=2.0, length=400.0):
    plug = trimesh.creation.cylinder(radius=10.0, height=thickness, sections=96)  # overlaps the wall
    plug.apply_translation([0, 0, z])
    return trimesh.boolean.union([_tube(length=length), plug], engine="manifold")


def test_thin_plug_between_stations_is_caught_by_the_through_path_probe():
    # stations every 20 mm (5 % of 400 mm) at z = -180, -160, ...; this 2 mm plug sits at
    # z = -10, between the 45 % and 50 % stations, so no cross-section sees it
    plugged = _plugged(-10.0)
    result = acoustic.advise(KENA_LIKE, plugged)
    assert {s["kind"] for s in result["bore"]["stations"]} == {"bore"}
    assert result["status"] == "inconsistent"
    (failure,) = [f for f in result["failures"] if f["check"] == "bore_continuity"]
    _explained([failure])
    assert failure["measured"] == "obstruction" and "45% and 50%" in failure["detail"]
    (path,) = result["bore"]["through_path"]
    assert path["probes_hit"] == path["probes"] == len(acoustic.PROBE_OFFSETS) == 13
    assert path["at_mm"] == pytest.approx(189.0, abs=0.5)


def _lipped(z, r_lip=6.0, thickness=2.0, length=400.0):
    # a fused annular joint lip: overlaps the wall (r 10 > 9) and narrows the 18 mm bore to 2 r_lip
    lip = trimesh.creation.annulus(r_min=r_lip, r_max=10.0, height=thickness, sections=96)
    lip.apply_translation([0, 0, z])
    return trimesh.boolean.union([_tube(length=length), lip], engine="manifold")


def test_annular_lip_narrowing_the_bore_between_stations_is_an_obstruction():
    # #994 review: an 18 mm bore narrowed to 12 mm (56 % of the area gone) by a 2 mm lip at
    # z = -10, between the 45 % and 50 % stations. The centre and half-radius probes (r <= 4.5 mm)
    # pass through the 6 mm hole; the outer ring (r = 7.2 mm) hits the lip.
    result = acoustic.advise(KENA_LIKE, _lipped(-10.0))
    assert {s["kind"] for s in result["bore"]["stations"]} == {"bore"}
    assert result["status"] == "inconsistent"
    (failure,) = [f for f in result["failures"] if f["check"] == "bore_continuity"]
    _explained([failure])
    assert failure["measured"] == "obstruction" and "45% and 50%" in failure["detail"]
    (path,) = result["bore"]["through_path"]
    assert path["probes_hit"] == 8 and path["probes"] == 13  # exactly the outer ring
    assert path["at_mm"] == pytest.approx(189.0, abs=0.5)


def test_outer_probe_ring_stays_inside_clean_bores_of_any_size():
    tube = _tube()
    assert acoustic.bore_report(KENA_LIKE, tube, body_id="body")["through_path"] == []
    for r_in in (4.0, 6.0, 12.0):
        thin = _tube(r_in=r_in, r_out=r_in + 1.5)
        assert acoustic.bore_report(KENA_LIKE, thin, body_id="body")["through_path"] == [], r_in


@pytest.mark.parametrize("z, end", [(199.0, "upper"), (-199.0, "lower")])
def test_end_cap_on_a_declared_open_end_is_flagged(z, end):
    capped = _plugged(z)
    result = acoustic.advise(KENA_LIKE, capped)
    assert result["status"] == "inconsistent"
    (failure,) = [f for f in result["failures"] if f["check"] == "bore_continuity"]
    _explained([failure])
    assert failure["measured"] == "closed_end" and f"{end} end" in failure["detail"]


def test_end_cap_is_allowed_when_the_spec_declares_a_stopped_end():
    stopped = {**KENA_LIKE, "constraints": {**KENA_LIKE["constraints"], "bore": "cylindrical, stopped at one end"}}
    report = acoustic.bore_report(stopped, _plugged(199.0), body_id="body")
    assert report["open_ends_required"] is False and report["through_path"] == []
    assert not [f for f in report["failures"] if f["check"] == "bore_continuity"]
    # an interior plug is still an obstruction
    report = acoustic.bore_report(stopped, _plugged(-10.0), body_id="body")
    assert [f["measured"] for f in report["failures"] if f["check"] == "bore_continuity"] == ["obstruction"]


def test_clean_tube_has_an_open_through_path():
    report = acoustic.bore_report(KENA_LIKE, _tube(), body_id="body")
    assert report["status"] == "consistent" and report["through_path"] == []


def _split_duduk(gap=8.0, r_in=6.0):
    half = (336 - gap) / 2
    lower = _tube(length=half, r_in=r_in, r_out=r_in + 3.5, z0=-168 + half / 2)
    upper = _tube(length=half, r_in=r_in, r_out=r_in + 3.5, z0=168 - half / 2)
    reed = trimesh.creation.box(extents=[14, 3, 60])
    reed.apply_translation([0, 0, 168 + 31])
    return trimesh.util.concatenate([lower, upper, reed])


def test_assembly_body_split_with_a_gap_is_flagged():
    result = acoustic.advise(DUDUK_LIKE, _split_duduk(gap=8.0))
    bore = result["bore"]
    assert bore["pieces"] == 2 and bore["other_bodies"] == 1
    assert bore["length_mm"] == pytest.approx(336.0, abs=0.01)  # the full body extent, both halves
    assert result["status"] == "inconsistent"
    gaps = [f for f in result["failures"] if f["check"] == "bore_continuity" and f["unit"] == "mm"]
    assert len(gaps) == 1 and gaps[0]["measured"] == pytest.approx(8.0, abs=0.01)
    _explained(gaps)


def test_assembly_body_in_two_touching_pieces_is_consistent():
    result = acoustic.advise(DUDUK_LIKE, _split_duduk(gap=0.0))
    assert result["bore"]["pieces"] == 2
    assert result["status"] == "consistent", result["failures"]


def test_assembly_with_only_half_a_body_fails_the_declared_length():
    half = _tube(length=164, r_in=6.0, r_out=9.5, z0=-86)
    reed = trimesh.creation.box(extents=[14, 3, 60])
    reed.apply_translation([0, 0, 168 + 31])
    result = acoustic.advise(DUDUK_LIKE, trimesh.util.concatenate([half, reed]))
    (failure,) = [f for f in result["failures"] if f["check"] == "bore_length"]
    assert failure["measured"] == pytest.approx(164.0, abs=0.01) and failure["threshold"] == 336
    _explained([failure])


def test_reed_seated_into_the_bore_is_not_an_obstruction():
    body = _tube(length=336, r_in=6.0, r_out=9.5)
    reed = trimesh.creation.box(extents=[8, 3, 60])
    reed.apply_translation([0, 0, 168 + 10])  # 20 mm of the reed inside the top of the bore
    result = acoustic.advise(DUDUK_LIKE, trimesh.util.concatenate([body, reed]))
    assert result["bore"]["other_bodies"] == 1
    assert result["status"] == "consistent", result["failures"]


def test_shipped_duduk_and_kena_get_a_bore_profile():
    specs = {s["id"]: s for s in json.loads(REGISTRY.read_text())["instruments"]}
    assert acoustic.bore_reason(specs["duduk"]) is None
    assert acoustic._declares_cylindrical(specs["kena"]) is True


# --- scoring isolation and per-tier reporting -------------------------------------------------


def _gate(tmp_path, mesh, spec):
    stl = tmp_path / "output.stl"
    mesh.export(stl)
    png = tmp_path / "preview.png"
    png.write_bytes(b"png")
    context = ObjectiveContext(trial_id="t", model_id="m", instrument_id=spec["id"], seed=0,
                               scad_path=tmp_path / "in.scad",
                               artifacts=RenderArtifacts(stl_path=stl, png_path=png))
    return runner.mesh_objective_gate(spec, part_module_counter=lambda _p: 0)(context)


@pytest.mark.parametrize("target_scale", [1.0, 1.5])
def test_vessel_advisory_never_changes_sub_scores_or_pass_rate(tmp_path, monkeypatch, vessel, target_scale):
    spec = _vessel_spec(_matched_target() * target_scale)
    with_advisory = _gate(tmp_path, vessel, spec)
    monkeypatch.setattr(acoustic, "advise", lambda spec, mesh: {"status": "stubbed"})
    without = _gate(tmp_path, vessel, spec)
    assert with_advisory["advisory"]["acoustic"]["family"] == "vessel_flute_helmholtz"
    assert with_advisory["sub_scores"] == without["sub_scores"]
    assert with_advisory["objective_pass_rate"] == without["objective_pass_rate"]
    assert with_advisory["failures"] == without["failures"]  # advisory failures stay in the advisory
    assert _normalize_gate_result(with_advisory)["advisory"]["acoustic"]["status"] in ("consistent", "inconsistent")


def _trial(tid, model, tier, acoustic_result, *, status="scored", backend="openscad"):
    result = None
    if status == "scored":
        result = {"backend": backend, "context_tier": tier,
                  "objective": {"objective_pass_rate": 1.0, "advisory": {"acoustic": acoustic_result}}}
    return {"trial_id": tid, "model_id": model, "instrument_id": "ocarina", "seed": 0, "status": status,
            "result": result, "meta": {"context_tier": tier}}


def test_advisory_report_is_per_tier_and_separate_from_the_scoreline():
    bad = {"label": "advisory", "family": "vessel_flute_helmholtz", "status": "inconsistent",
           "failures": [{"check": "helmholtz_pitch", "measured": 300.0, "threshold": 440.0, "unit": "Hz",
                         "requires": "r", "body_id": "body", "detail": "d"}]}
    good = {"label": "advisory", "family": "vessel_flute_helmholtz", "status": "consistent", "failures": []}
    log = {"config": {"backend": "openscad"}, "trials": [
        _trial("a", "m1", "blind", good), _trial("b", "m1", "blind", bad),
        _trial("c", "m1", "image", good), _trial("d", "m1", "blind", None, status="error"),
        _trial("e", "m1", "consensus@3", bad)]}
    report = advisory_report.collect_advisory_report(log)
    assert report["schema"] == "makerbench-advisory-report-v1" and report["affects_scoring"] is False
    by_tier = {r["context_tier"]: r for r in report["rows"]}
    assert set(by_tier) == {"blind", "image"}  # consensus excluded, tiers never blended
    blind = by_tier["blind"]
    assert blind["n_trials"] == 3
    assert blind["status_counts"] == {"consistent": 1, "inconsistent": 1, "no result": 1}
    (failure,) = blind["failures"]
    assert failure["trial_id"] == "b" and failure["check"] == "helmholtz_pitch"
    assert by_tier["image"]["status_counts"] == {"consistent": 1}
    # the scoreline itself never carries advisory rows
    rows = runner.collect_objective_scoreline(log)
    assert all("advisory" not in row and "status_counts" not in row for row in rows)
