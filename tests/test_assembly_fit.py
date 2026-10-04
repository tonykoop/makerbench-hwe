"""Advisory assembly interface fit (#982): floating parts and part-to-part interference."""

from __future__ import annotations

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
