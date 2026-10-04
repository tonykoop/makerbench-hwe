"""Advisory assembly interface fit (#982): floating parts and part-to-part interference."""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from makerbench import assembly_fit as af

ASSEMBLY = {"id": "box-assembly", "assembly": True, "min_bodies": 2, "envelope_mm": [300, 300, 300]}
EXPLANATION_KEYS = {"check", "measured", "threshold", "unit", "requires", "body_id", "detail"}


def _box(extents, center):
    box = trimesh.creation.box(extents=extents)
    box.apply_translation(center)
    return box


def _body_and_neck(neck_x=60.0, neck_len=60.0):
    """A 100 mm body with a 20x20 neck whose end sits at ``neck_x - neck_len / 2``."""
    body = _box([100, 60, 40], [0, 0, 0])
    neck = _box([neck_len, 20, 20], [neck_x, 0, 0])
    return body, neck


def _explained(failures):
    assert failures
    for failure in failures:
        assert EXPLANATION_KEYS <= set(failure), failure
        assert failure["detail"]


def test_mated_parts_are_consistent():
    body, neck = _body_and_neck(neck_x=80.0)  # neck face at x = 50 meets the body face
    cap = _box([10, 20, 20], [115, 0, 0])   # meets the neck end at x = 110
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([body, neck, cap]))
    assert result["label"] == "advisory" and result["affects_scoring"] is False
    assert result["status"] == "consistent", result["failures"]
    assert result["parts"] == 3 and result["groups"] == [3] and result["overlaps"] == []


def test_part_within_the_contact_tolerance_is_attached():
    body, neck = _body_and_neck(neck_x=80.3)  # 0.3 mm gap < 0.5 mm
    assert af.advise(ASSEMBLY, trimesh.util.concatenate([body, neck]))["status"] == "consistent"


def test_floating_part_is_flagged_with_its_gap():
    body, neck = _body_and_neck(neck_x=80.0)
    floating = _box([10, 10, 10], [0, 0, 45])  # 20 mm above the body top (z = 20) -> 20 mm gap
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([body, neck, floating]))
    assert result["status"] == "inconsistent"
    (failure,) = result["failures"]
    _explained([failure])
    assert failure["check"] == "floating_part" and failure["body_id"] == "body_2"
    assert failure["measured"] == pytest.approx(20.0, abs=1e-3) and failure["threshold"] == 0.5
    assert failure["nearest"] == "body_0"


def test_a_floating_pair_counts_as_floating_even_though_they_touch_each_other():
    body = _box([100, 60, 40], [0, 0, 0])
    peg_a = _box([10, 10, 10], [0, 0, 60])
    peg_b = _box([10, 10, 10], [10, 0, 60])  # touches peg_a, both 35 mm above the body
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([body, peg_a, peg_b]))
    floating = sorted(f["body_id"] for f in result["failures"] if f["check"] == "floating_part")
    assert floating == ["body_1", "body_2"]
    assert all(f["measured"] == pytest.approx(35.0, abs=1e-3) for f in result["failures"])


def test_neck_running_through_the_bowl_wall_is_interference():
    body, neck = _body_and_neck(neck_x=60.0)  # neck spans x 30..90: 20 mm inside the body
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([body, neck]))
    assert result["status"] == "inconsistent"
    (failure,) = result["failures"]
    _explained([failure])
    assert failure["check"] == "part_interference" and failure["unit"] == "mm3"
    assert failure["measured"] == pytest.approx(20 * 20 * 20, rel=1e-3)
    assert {failure["body_id"], failure["other_body_id"]} == {"body_0", "body_1"}


def test_interference_within_the_volume_tolerance_is_allowed():
    body, neck = _body_and_neck(neck_x=79.999)  # 0.001 mm overlap -> 0.4 mm3
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([body, neck]))
    assert result["status"] == "consistent", result["failures"]
    # a declared tighter tolerance flags the same pair
    strict = af.advise({**ASSEMBLY, "interference_tolerance_mm3": 0.1}, trimesh.util.concatenate([body, neck]))
    assert [f["check"] for f in strict["failures"]] == ["part_interference"]


def test_declared_contact_tolerance_is_used():
    body, neck = _body_and_neck(neck_x=82.0)  # 2 mm gap
    mesh = trimesh.util.concatenate([body, neck])
    assert af.advise(ASSEMBLY, mesh)["status"] == "inconsistent"
    loose = af.advise({**ASSEMBLY, "constraints": {"contact_tolerance_mm": 3.0}}, mesh)
    assert loose["status"] == "consistent" and loose["tolerances"]["contact_tolerance_mm"] == 3.0


def test_cavity_of_a_hollow_part_is_not_a_floating_part():
    # a hollow bowl: its inner surface is a separate inward-facing shell 3 mm inside the outer one
    outer = trimesh.creation.icosphere(subdivisions=3, radius=50.0)
    inner = trimesh.creation.icosphere(subdivisions=3, radius=47.0)
    inner.invert()
    bowl = trimesh.util.concatenate([outer, inner])
    neck = _box([60, 10, 10], [79.5, 0, 0])  # meets the bowl surface near x = 49.5..50
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([bowl, neck]))
    assert result["void_shells"] == 1 and result["parts"] == 2
    assert not [f for f in result["failures"] if f["check"] == "floating_part"], result["failures"]
    # only the void and its bowl: one part, nothing to compare
    assert af.advise(ASSEMBLY, bowl)["status"] == "not measurable"


def test_fused_assembly_is_not_measurable_and_non_assembly_is_not_modelled():
    body, neck = _body_and_neck(neck_x=60.0)
    fused = trimesh.boolean.union([body, neck], engine="manifold")
    result = af.advise(ASSEMBLY, fused)
    assert result["status"] == "not measurable" and result["failures"] == []
    single = af.advise({"id": "flute", "min_bodies": 1}, trimesh.util.concatenate([body, neck]))
    assert single["status"] == "not modelled"


def test_gate_carries_the_advisory_without_changing_scores(tmp_path):
    from makerbench import code_cad_arena_runner as runner
    from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts

    body, neck = _body_and_neck(neck_x=60.0)
    floating = _box([10, 10, 10], [0, 0, 45])
    results = {}
    for name, parts in (("good", [_body_and_neck(neck_x=80.0)[0], _body_and_neck(neck_x=80.0)[1]]),
                        ("bad", [body, neck, floating])):
        stl = tmp_path / f"{name}.stl"
        trimesh.util.concatenate(parts).export(stl)
        png = tmp_path / f"{name}.png"
        png.write_bytes(b"\x89PNG\r\n")
        gate = runner.mesh_objective_gate(ASSEMBLY, part_module_counter=lambda _p: 0)
        context = ObjectiveContext(trial_id=name, model_id="m", instrument_id="box-assembly", seed=0,
                                   scad_path=tmp_path / "x.scad",
                                   artifacts=RenderArtifacts(stl_path=stl, png_path=png))
        results[name] = gate(context)
    assert results["good"]["advisory"]["assembly_fit"]["status"] == "consistent"
    assert results["bad"]["advisory"]["assembly_fit"]["status"] == "inconsistent"
    assert results["good"]["sub_scores"] == results["bad"]["sub_scores"]
    assert results["good"]["objective_pass_rate"] == results["bad"]["objective_pass_rate"]


def test_advisory_failure_never_breaks_scoring_and_report_is_per_tier(tmp_path, monkeypatch):
    from makerbench import advisory_report
    from makerbench import code_cad_arena_runner as runner
    from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts

    body, neck = _body_and_neck(neck_x=80.0)
    floating = _box([10, 10, 10], [0, 0, 45])
    stl = tmp_path / "bad.stl"
    trimesh.util.concatenate([body, neck, floating]).export(stl)
    png = tmp_path / "bad.png"
    png.write_bytes(b"\x89PNG\r\n")
    context = ObjectiveContext(trial_id="t", model_id="m", instrument_id="box-assembly", seed=0,
                               scad_path=tmp_path / "x.scad", artifacts=RenderArtifacts(stl_path=stl, png_path=png))
    gate = runner.mesh_objective_gate(ASSEMBLY, part_module_counter=lambda _p: 0)
    payload = gate(context)

    objective = {"objective_pass_rate": payload["objective_pass_rate"], "advisory": payload["advisory"]}
    log = {"trials": [
        {"trial_id": f"t-{tier}", "model_id": "m", "instrument_id": "box-assembly", "seed": 0, "status": "scored",
         "result": {"context_tier": tier, "objective": objective}} for tier in ("blind", "repo")]}
    rows = [r for r in advisory_report.collect_advisory_report(log)["rows"] if r["advisory"] == "assembly_fit"]
    assert [r["context_tier"] for r in rows] == ["blind", "repo"]
    assert all(r["status_counts"] == {"inconsistent": 1} for r in rows)
    assert rows[0]["failures"][0]["check"] == "floating_part" and rows[0]["failures"][0]["trial_id"] == "t-blind"

    def boom(spec, mesh):
        raise RuntimeError("boolean engine exploded")

    monkeypatch.setattr(af, "advise", boom)
    broken = gate(context)
    assert broken["advisory"]["assembly_fit"]["status"] == "error"
    assert broken["sub_scores"] == payload["sub_scores"]
    assert broken["objective_pass_rate"] == payload["objective_pass_rate"]


# --- #982 review regressions -------------------------------------------------------------------

def _hollow_box(outer=100.0, inner=94.0):
    shell = _box([outer] * 3, [0, 0, 0])
    cavity = _box([inner] * 3, [0, 0, 0])
    cavity.invert()
    return trimesh.util.concatenate([shell, cavity])


def test_cavity_stays_with_its_part_so_a_block_inside_touching_the_wall_does_not_interfere():
    """Removing the cavity shell filled the cavity: a 10 mm block touching the inner wall read
    1,000 mm3 of interference. The cavity stays with its part."""
    block = _box([10, 10, 10], [0, 0, -42])  # inner floor at z = -47: the block sits on it
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([_hollow_box(), block]))
    assert result["void_shells"] == 1 and result["parts"] == 2
    assert result["status"] == "consistent", result["failures"]
    assert result["overlaps"] == []


def test_block_through_the_wall_of_a_hollow_part_reports_only_the_wall_volume():
    block = _box([10, 10, 10], [0, 0, -50])  # z -55..-45: 3 mm of it is in the wall (-50..-47)
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([_hollow_box(), block]))
    (failure,) = result["failures"]
    assert failure["check"] == "part_interference"
    assert failure["measured"] == pytest.approx(300.0, rel=1e-6)


def test_inverted_solid_penetrating_a_part_is_not_hidden_as_a_cavity():
    """A reversed-winding solid that penetrates a part, plus a third face-touching part, read
    consistent. An inward shell must be enclosed by a part to be its cavity."""
    body, neck = _body_and_neck(neck_x=80.0)
    inverted = _box([20, 20, 20], [0, 0, 25])  # pokes 5 mm into the body top (z = 20)
    inverted.invert()
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([body, neck, inverted]))
    assert result["status"] == "not measurable" and result["inverted_shells"]
    assert "inverted" in result["error"]


def test_perpendicular_rods_are_measured_at_the_triangles_not_sampled_points():
    """Rods crossing with a true 0.3 mm gap read 6.17 mm from surface samples and floated."""
    a = _box([100, 1, 1], [0, 0, 0])
    b = _box([1, 100, 1], [0, 0, 1.3])
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([a, b]))
    assert result["status"] == "consistent", result["failures"]
    touching = af.advise(ASSEMBLY, trimesh.util.concatenate([a, _box([1, 100, 1], [0, 0, 1.0])]))
    assert touching["status"] == "consistent", touching["failures"]
    far = af.advise(ASSEMBLY, trimesh.util.concatenate([a, _box([1, 100, 1], [0, 0, 3.0])]))
    (failure,) = far["failures"]
    assert failure["gap_kind"] == "measured" and failure["measured"] == pytest.approx(2.0, abs=1e-6)


def test_a_failed_boolean_is_incomplete_never_consistent(monkeypatch):
    body = _box([20, 20, 20], [0, 0, 0])
    other = _box([20, 20, 20], [10, 0, 0])  # 4,000 mm3 shared
    monkeypatch.setattr(af, "_overlap", lambda a, b: None)
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([body, other]))
    assert result["status"] == "incomplete" and result["unmeasured_pairs"] == [["body_0", "body_1"]]
    assert result["incomplete_reason"]


def test_reported_gap_is_measured_not_a_bounding_box_bound():
    """Two spheres whose boxes are 7.07 mm apart are really ~15.6 mm apart."""
    a = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    b = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    b.apply_translation([25, 25, 0])
    (failure,) = af.advise(ASSEMBLY, trimesh.util.concatenate([a, b]))["failures"]
    assert failure["check"] == "floating_part" and failure["gap_kind"] == "measured"
    assert failure["measured"] == pytest.approx(25 * 2 ** 0.5 - 20, abs=0.3)  # facets sit inside the sphere


def test_tolerances_compare_raw_values_not_rounded_ones():
    """A 0.0004 mm3 overlap rounded to 0 and passed a declared zero tolerance."""
    body, neck = _body_and_neck(neck_x=80.0 - 1e-6)  # 1e-6 mm x 20 x 20 = 0.0004 mm3
    mesh = trimesh.util.concatenate([body, neck])
    strict = af.advise({**ASSEMBLY, "interference_tolerance_mm3": 0.0}, mesh)
    (failure,) = strict["failures"]
    assert failure["check"] == "part_interference" and 0.0 < failure["measured"] < 0.001


def _press_fit(radial_interference):
    """A 10 mm deep, 10 mm hole in a block and a peg pressed into it."""
    block = trimesh.creation.box(extents=[30, 30, 20])
    hole = trimesh.creation.cylinder(radius=5.0, height=10.02, sections=128)
    hole.apply_translation([0, 0, 5.0])
    block = block.difference(hole)
    peg = trimesh.creation.cylinder(radius=5.0 + radial_interference, height=30, sections=128)
    peg.apply_translation([0, 0, 15.0])  # z 0..30: 10 mm engaged
    return trimesh.util.concatenate([block, peg])


def test_press_fit_passes_the_default_depth_allowance_but_a_declared_depth_flags_it():
    """A 0.05 mm radial press fit over 10 mm shares ~16 mm3 but is 0.05 mm deep: a fit, not a
    collision. A spec that declares a tighter depth tolerance still flags it."""
    mesh = _press_fit(0.05)
    result = af.advise(ASSEMBLY, mesh)
    (overlap,) = result["overlaps"]
    assert overlap["volume_mm3"] > 10.0 and overlap["depth_mm"] == pytest.approx(0.05, abs=0.02)
    assert result["status"] == "consistent", result["failures"]
    strict = af.advise({**ASSEMBLY, "interference_depth_tolerance_mm": 0.01}, mesh)
    assert [f["check"] for f in strict["failures"]] == ["part_interference"]
    # a peg 2 mm oversize is a collision under the defaults
    assert [f["check"] for f in af.advise(ASSEMBLY, _press_fit(2.0))["failures"]] == ["part_interference"]


def test_dense_assembly_peak_memory_stays_under_a_gigabyte(tmp_path):
    """#982 review: the absolute peak, not growth. A four-torus assembly of 524k faces, just under
    MAX_FACES, with contact, interference (penetration depth measured) and a floating part,
    must peak under 1 GiB RSS in a fresh process."""
    import os
    import subprocess
    import sys

    root = str(Path(__file__).resolve().parents[1])
    script = tmp_path / "dense.py"
    script.write_text(
        "import resource, trimesh\n"
        "from makerbench import assembly_fit as af\n"
        f"assert af.__file__.startswith({root!r}), af.__file__\n"
        "a = trimesh.creation.torus(major_radius=40, minor_radius=8, major_sections=256, minor_sections=256)\n"
        "parts = [a]\n"
        "for offset in ([0, 0, 16.2], [0, 0, 50], [30, 0, 8]):\n"
        "    p = a.copy(); p.apply_translation(offset); parts.append(p)\n"
        "mesh = trimesh.util.concatenate(parts)\n"
        "assert len(mesh.faces) <= af.MAX_FACES, len(mesh.faces)\n"
        "r = af.advise({'assembly': True}, mesh)\n"
        "print(r['status'], resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([root, os.environ.get("PYTHONPATH", "")])}
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=600,
                         cwd=root, env=env, check=True).stdout.split()
    status, peak_mib = out[0], int(out[-1])
    assert status == "inconsistent"
    assert peak_mib < 1024, peak_mib


def test_oversized_assembly_is_incomplete_too_large(monkeypatch):
    """Above MAX_FACES nothing is measured: incomplete, too_large, never consistent."""
    body, neck = _body_and_neck(neck_x=80.0)
    mesh = trimesh.util.concatenate([body, neck])
    monkeypatch.setattr(af, "MAX_FACES", len(mesh.faces) - 1)
    result = af.advise(ASSEMBLY, mesh)
    assert result["status"] == "incomplete" and result["incomplete_reason"] == "too_large"
    assert result["failures"] == []


def test_deep_tab_is_not_averaged_away_by_a_broad_shallow_overlap():
    """#982 review: a 100x100 mm plate overlapping the base by 0.05 mm, with a 5x5 mm tab running
    5 mm into it: the mean thickness 2V/A read 0.062 mm and passed. Penetration depth is 5 mm."""
    base = trimesh.creation.box(bounds=[[-50, -50, -20], [50, 50, 0]])
    plate = trimesh.creation.box(bounds=[[-50, -50, -0.05], [50, 50, 0.05]])
    tab = trimesh.creation.box(bounds=[[-2.5, -2.5, -5], [2.5, 2.5, 5]])
    other = trimesh.boolean.union([plate, tab], engine="manifold")
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([base, other]))
    (failure,) = result["failures"]
    assert failure["check"] == "part_interference"
    assert failure["depth_mm"] == pytest.approx(5.0, abs=1e-3)


@pytest.mark.parametrize("overlap, fails", [(0.201, True), (0.19, False)])
def test_penetration_just_over_the_allowance_fails(overlap, fails):
    """Two 10 mm cubes overlapping by 0.201 mm read 0.1932 mm mean thickness and passed."""
    a = _box([10, 10, 10], [0, 0, 0])
    b = _box([10, 10, 10], [0, 0, 10 - overlap])
    result = af.advise(ASSEMBLY, trimesh.util.concatenate([a, b]))
    (pair,) = result["overlaps"]
    assert pair["depth_mm"] == pytest.approx(overlap, abs=1e-6)
    assert bool(result["failures"]) is fails
