"""Declared joints must touch (#1150): identification, measurement, gate and Morning Review."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench import joint_contact as jc
from makerbench.arena_studio import control_room as cr
from makerbench.code_cad_generator import instrument_spec_from_registry
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts

REGISTRY = Path(__file__).resolve().parents[1] / "tasks" / "code_cad_arena" / "registry.json"
# The four major bodies (hull, soundboard, string holder, neck) of the 2026-10-18 SolidWorks
# rehearsal's r1 STL (rehearsals/20261018T174920Z-solidworks-sambuca, sha256 56ebf879...),
# copied out read-only; the neck's arc stops ~10 mm short of the hull.
R1_FIXTURE = Path(__file__).parent / "fixtures" / "joint_contact" / "sambuca_r1_major_bodies.npz"


def _spec() -> dict:
    return instrument_spec_from_registry(json.loads(REGISTRY.read_text(encoding="utf-8")), "sambuca")


def _r1_bodies() -> list[trimesh.Trimesh]:
    data = np.load(R1_FIXTURE)
    return [trimesh.Trimesh(data[f"v{i}"].astype(float), data[f"f{i}"], process=False) for i in range(4)]


def _box(lo, hi) -> trimesh.Trimesh:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    box = trimesh.creation.box(extents=hi - lo)
    box.apply_translation((lo + hi) / 2)
    return box


def _harp(neck_bottom: float = 100.0, holder_bottom: float = 106.0) -> list[trimesh.Trimesh]:
    """A box sambuca: hull 0..100, soundboard 100..106, holder on the board, neck rising at the bow."""
    return [
        _box((100, 0, 0), (600, 200, 100)),                 # hull
        _box((100, 0, 100), (600, 200, 106)),               # soundboard (plate)
        _box((300, 80, holder_bottom), (580, 120, holder_bottom + 15)),  # string holder (strip)
        _box((100, 80, neck_bottom), (140, 120, neck_bottom + 700)),     # neck (longest)
    ]


def _by(report) -> dict:
    return {row["joint"]: row for row in report["joints"]}


def test_sambuca_declares_its_three_joints_in_a_versioned_block():
    spec = _spec()
    assert spec["brief_version"] == 2 and "Joints (graded)" in spec["task_brief"]
    block = jc.declared(spec)
    assert block["version"] == 1 and block["tolerance_mm"] == 0.5
    assert [jc.joint_label(*pair) for pair in block["pairs"]] == [
        "neck↔hull", "soundboard↔hull", "string holder↔soundboard"]


def test_the_r1_rehearsal_fails_neck_to_hull():
    report = jc.check(_spec(), _r1_bodies())
    joints = _by(report)
    assert report["status"] == "fail"
    neck = joints["neck↔hull"]
    assert neck["status"] == "fail" and 9.0 < neck["gap_mm"] < 11.0
    assert neck["sentence"] == "neck↔hull: 10 mm gap — FAIL"
    assert {row["identified"][part]["by"] for row in report["joints"] for part in row["parts"]} == {"geometry"}
    # r1 also left 1 mm air under the soundboard and the holder; both are real gaps above 0.5 mm.
    assert joints["soundboard↔hull"]["status"] == "fail" and joints["string holder↔soundboard"]["status"] == "fail"


def test_touching_and_seated_joints_pass_and_too_deep_ones_fail():
    spec = _spec()
    seated = _by(jc.check(spec, _harp(neck_bottom=90.0)))
    assert all(row["status"] == "pass" for row in seated.values())
    assert seated["neck↔hull"]["penetration_mm"] == pytest.approx(10.0, abs=0.5)
    assert seated["soundboard↔hull"]["gap_mm"] == pytest.approx(0.0, abs=1e-6)
    deep_report = jc.check(spec, _harp(neck_bottom=60.0))
    deep = _by(deep_report)["neck↔hull"]
    assert deep["status"] == "fail" and deep["reason"] == "penetration" and jc.sub_score(deep_report) == 0.0
    assert deep["sentence"] == "neck↔hull: interpenetrates 40 mm, beyond the 20 mm joinery allowance — FAIL"
    gapped = _by(jc.check(spec, _harp(holder_bottom=106.8)))["string holder↔soundboard"]
    assert gapped["status"] == "fail" and gapped["sentence"] == "string holder↔soundboard: 0.8 mm gap — FAIL"


def test_unidentifiable_parts_are_unknown_never_pass():
    spec = _spec()
    hull, board, holder, _neck = _harp()
    twin = _box((100, 0, 0), (600, 200, 100.5))  # two hull candidates within the ambiguity margin
    twin.apply_translation((0, 300, 0))
    report = jc.check(spec, [hull, twin, board, holder])
    assert report["status"] == "unknown" and jc.sub_score(report) is None
    assert all(row["status"] == "unknown" for row in report["joints"] if "hull" in row["parts"])
    assert "ambiguous" in _by(report)["soundboard↔hull"]["sentence"]


def test_named_bodies_from_a_bridge_listing_win_over_geometry():
    bodies = _harp()
    listing = [{"name": name, "bbox_mm": {"min": body.bounds[0].tolist(), "max": body.bounds[1].tolist()}}
               for name, body in zip(("Hull-Shell", "Soundboard", "String_Holder", "Neck Arc"), bodies)]
    listing.reverse()  # listing order is irrelevant
    report = jc.check(_spec(), bodies, listing)
    assert report["status"] == "pass"
    assert {row["identified"][p]["by"] for row in report["joints"] for p in row["parts"]} == {"name"}
    # a listing whose names do not match any part falls back to geometry
    fallback = jc.check(_spec(), bodies, [{"name": "Body-Move/Copy9", "bbox_mm": listing[0]["bbox_mm"]}])
    assert {row["identified"][p]["by"] for row in fallback["joints"] for p in row["parts"]} == {"geometry"}


def test_a_spec_without_joints_is_untouched():
    assert jc.check({"id": "x", "assembly": True}, _harp()) is None


def _gate(tmp_path: Path, bodies, listing=None) -> dict:
    stl = tmp_path / "output.stl"
    trimesh.util.concatenate(bodies).export(stl.as_posix())
    if listing is not None:
        (tmp_path / runner.BODY_LISTING_FILE).write_text(json.dumps({"result": {"bodies": listing}}), encoding="utf-8")
    png = tmp_path / "preview.png"
    png.write_bytes(b"\x89PNG\r\n")
    context = ObjectiveContext(trial_id="t", model_id="m", instrument_id="sambuca", seed=0,
                               scad_path=tmp_path / "input.scad", artifacts=RenderArtifacts(stl_path=stl, png_path=png))
    return runner.mesh_objective_gate(_spec())(context)


def test_the_gate_scores_and_explains_a_failed_joint(tmp_path):
    objective = _gate(tmp_path, _r1_bodies())
    assert objective["sub_scores"]["joints"] == 0.0 and not objective["passed"]
    assert objective["checks"]["joints"]["version"] == 1
    joint_failures = [f for f in objective["failures"] if f["check"] == "joints"]
    assert joint_failures[0]["detail"] == "neck↔hull: 10 mm gap — FAIL" and joint_failures[0]["unit"] == "mm gap"


def test_the_gate_reads_a_saved_body_listing_and_blocks_pass_when_unknown(tmp_path):
    bodies = _harp()
    names = ("hull", "soundboard", "holder", "neck")
    listing = [{"name": n, "bbox_mm": {"min": b.bounds[0].tolist(), "max": b.bounds[1].tolist()}} for n, b in zip(names, bodies)]
    (tmp_path / "a").mkdir()
    good = _gate(tmp_path / "a", bodies, listing)
    assert good["sub_scores"]["joints"] == 1.0
    assert {row["identified"][p]["by"] for row in good["checks"]["joints"]["joints"] for p in row["parts"]} == {"name"}
    (tmp_path / "b").mkdir()
    hull, board, holder, _ = bodies
    twin = _box((100, 300, 0), (600, 500, 100.5))
    unknown = _gate(tmp_path / "b", [hull, twin, board, holder])
    assert "joints" not in unknown["sub_scores"] and unknown["checks"]["joints"]["status"] == "unknown"
    assert unknown["passed"] is False


def test_morning_review_shows_one_chip_per_joint_and_demotes_stl_checks(tmp_path):
    objective = _gate(tmp_path, _r1_bodies())
    row = {"trial_id": "sambuca__seed0__rep0__solidworks-sol", "model_id": "solidworks-sol", "status": "scored",
           "provider": "solidworks-live", "result": {"objective": objective}}
    design = cr._design(tmp_path, tmp_path, row, {})
    names = [check["name"] for check in design["checks"]]
    assert "watertight" not in names and "body_count" in names
    assert names[-3:] == ["neck↔hull", "soundboard↔hull", "string holder↔soundboard"]
    assert {check["state"] for check in design["checks"] if check.get("joint")} == {"fail"}
    assert "neck↔hull: 10 mm gap — FAIL" in design["failures"]
    assert [item["name"] for item in design["diagnostics"]] == ["watertight"]
    assert design["all_pass"] is False


def test_morning_review_keeps_stl_checks_for_designs_without_joints(tmp_path):
    objective = {"sub_scores": {name: 1.0 for name in cr.BASE_CHECKS}, "failures": []}
    row = {"trial_id": "t", "model_id": "m", "status": "scored", "provider": "claude", "result": {"objective": objective}}
    design = cr._design(tmp_path, tmp_path, row, {})
    assert [check["name"] for check in design["checks"]] == list(cr.BASE_CHECKS)
    assert design["diagnostics"] == [] and design["all_pass"] is True


def _panel(corners) -> trimesh.Trimesh:
    return trimesh.Trimesh(np.asarray(corners, float), [(0, 1, 2), (0, 2, 3)], process=False)


def test_open_panels_touching_along_an_edge_measure_zero():
    """Codex's #1152 reproduction: a sampled distance said 1.3 mm; the panels touch at (2, y, 0)."""
    a = _panel([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    b = _panel([(2, -5, 0), (2, 15, 0), (2, 15, 10), (2, -5, 10)])
    measured = jc.measure(a, b)
    assert measured["method"] == "triangle-exact" and measured["gap_mm"] == pytest.approx(0.0, abs=1e-9)
    assert jc._sampled_gap(a, b) > 0.5  # what the old upper bound would have FAILed on
    lifted = b.copy()
    lifted.apply_translation((0, 0, 0.8))
    assert jc.measure(a, lifted)["gap_mm"] == pytest.approx(0.8, abs=1e-9)
    crossing = b.copy()
    crossing.apply_translation((0, 0, -5))  # B passes through A's interior: distance 0
    assert jc.measure(a, crossing)["gap_mm"] == pytest.approx(0.0, abs=1e-9)


def test_exact_gap_matches_known_distances_between_open_meshes():
    a = _panel([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    corner = _panel([(13, 14, 0), (20, 14, 0), (20, 20, 0), (13, 20, 0)])  # nearest: (10,10)-(13,14) = 5
    assert jc.measure(a, corner)["gap_mm"] == pytest.approx(5.0, abs=1e-9)
    tilted = _panel([(5, 5, 3), (6, 5, 9), (6, 6, 9), (5, 6, 3)])  # nearest: its lower edge, 3 above A
    assert jc.measure(a, tilted)["gap_mm"] == pytest.approx(3.0, abs=1e-9)


def test_a_too_deep_joint_explains_its_penetration_in_the_gate(tmp_path):
    objective = _gate(tmp_path, _harp(neck_bottom=60.0))
    assert objective["sub_scores"]["joints"] == 0.0 and not objective["passed"]
    row = next(f for f in objective["failures"] if f["check"] == "joints")
    assert (row["reason"], row["unit"], row["threshold"]) == ("penetration", "mm penetration", 20.0)
    assert row["measured"] == pytest.approx(40.0, abs=0.5)
