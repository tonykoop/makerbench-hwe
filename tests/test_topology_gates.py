"""Topology (Betti/Euler) and interface sub-volume gates (#797)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench import topology
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts, _normalize_gate_result


def _torus() -> trimesh.Trimesh:
    return trimesh.creation.torus(major_radius=20, minor_radius=5, major_sections=64,
                                  minor_sections=32)


TONE_HOLE_Z = (-30.0, 0.0, 30.0)


def _flute(holes=TONE_HOLE_Z) -> trimesh.Trimesh:
    """A 100 mm tube (bore 16, OD 20) with side tone holes through the +x wall."""
    tube = trimesh.creation.annulus(r_min=8, r_max=10, height=100, sections=96)
    for z in holes:
        hole = trimesh.creation.cylinder(radius=2.5, height=6, sections=48)
        hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        hole.apply_translation([10.0, 0.0, z])
        tube = tube.difference(hole)
    return tube


def _tone_hole(z: float, **overrides) -> dict:
    item = {"kind": "hole", "name": f"th{z:g}", "center_mm": [9.0, 0.0, z], "axis": [1, 0, 0],
            "diameter_mm": 5.0, "depth_mm": 1.5, "tolerance_mm": 0.4}
    item.update(overrides)
    return item


def _pocketed_block(depth: float = 11.0) -> trimesh.Trimesh:
    """A 40x20x20 block (top face z=10) with a 10x10 pocket ``depth`` deep."""
    block = trimesh.creation.box(extents=[40, 20, 20])
    if depth <= 0:
        return block
    cut = trimesh.creation.box(extents=[10, 10, depth + 1.0])  # +1 clears the top face
    cut.apply_translation([0, 0, 10 - depth + (depth + 1.0) / 2])
    return block.difference(cut)


NECK_POCKET = {"kind": "pocket", "name": "neck", "min_mm": [-5, -5, -1], "max_mm": [5, 5, 10],
               "open_faces": ["+z"]}


def _hollow_sphere() -> trimesh.Trimesh:
    return trimesh.creation.icosphere(radius=10).difference(trimesh.creation.icosphere(radius=6))


# --- Betti numbers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory, betti",
    [
        (lambda: trimesh.creation.box(extents=[5, 5, 5]), [1, 0, 0]),
        (_torus, [1, 1, 0]),
        (_flute, [1, 4, 0]),
        (_hollow_sphere, [1, 0, 1]),
        (lambda: trimesh.util.concatenate([_torus(), trimesh.creation.box(extents=[2, 2, 2])]),
         [2, 1, 0]),
    ],
    ids=["box", "torus-genus-1", "flute-3-holes", "hollow-sphere-void", "two-bodies"],
)
def test_betti_numbers_and_euler_characteristic(factory, betti):
    result = topology.betti_numbers(factory())
    assert result["ok"], result
    assert result["betti"] == betti
    assert result["euler_characteristic"] == betti[0] - betti[1] + betti[2]


def test_open_mesh_topology_is_undefined_not_guessed():
    box = trimesh.creation.box(extents=[5, 5, 5])
    open_box = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[:-2], process=False)
    result = topology.betti_numbers(open_box)
    assert result == {"ok": False, "error": "mesh is not closed and manifold; topology undefined"}


def test_topology_gate_flips_when_a_tone_hole_is_removed():
    spec = {"topology": {"betti": [1, 4, 0]}}
    assert topology.topology_check(_flute(), spec)["status"] == "pass"
    missing = topology.topology_check(_flute(holes=TONE_HOLE_Z[:2]), spec)
    assert missing["status"] == "fail"
    assert missing["observed"] == {"betti": [1, 3, 0]}


def test_euler_only_declaration():
    assert topology.topology_check(_torus(), {"topology": {"euler_characteristic": 0}})["status"] == "pass"
    assert topology.topology_check(_torus(), {"topology": {"euler_characteristic": 2}})["status"] == "fail"


# --- interfaces ---------------------------------------------------------------------------


def test_declared_tone_holes_pass_and_a_removed_hole_fails():
    spec = {"interfaces": [_tone_hole(z) for z in TONE_HOLE_Z]}
    assert topology.interface_check(_flute(), spec)["status"] == "pass"

    result = topology.interface_check(_flute(holes=TONE_HOLE_Z[:2]), spec)

    assert result["status"] == "fail"
    by_name = {r["name"]: r for r in result["interfaces"]}
    assert by_name["th30"]["status"] == "fail" and by_name["th30"]["core_points_in_material"] > 0
    assert by_name["th0"]["status"] == "pass"


@pytest.mark.parametrize(
    "override, reason",
    [
        ({"diameter_mm": 3.0}, "declared smaller than the real 5 mm hole: ring lands in air"),
        ({"diameter_mm": 7.0}, "declared larger than the real hole: core lands in material"),
        ({"center_mm": [9.0, 0.0, 1.5]}, "displaced by more than the tolerance"),
    ],
)
def test_hole_diameter_and_centre_are_held_to_tolerance(override, reason):
    spec = {"interfaces": [_tone_hole(0.0, **override)]}
    assert topology.interface_check(_flute(), spec)["status"] == "fail", reason


def test_hole_within_tolerance_passes():
    spec = {"interfaces": [_tone_hole(0.0, center_mm=[9.0, 0.0, 0.3], diameter_mm=5.2)]}
    assert topology.interface_check(_flute(), spec)["status"] == "pass"


def test_neck_pocket_passes_and_a_missing_or_shallow_pocket_fails():
    spec = {"interfaces": [NECK_POCKET]}
    assert topology.interface_check(_pocketed_block(), spec)["status"] == "pass"
    assert topology.interface_check(_pocketed_block(depth=0), spec)["status"] == "fail"
    assert topology.interface_check(_pocketed_block(depth=5), spec)["status"] == "fail"


def test_pocket_deeper_than_declared_fails_on_its_floor():
    # Core is empty either way; only the floor probe (declared floor - tol) lands in air.
    result = topology.interface_check(_pocketed_block(depth=15), {"interfaces": [NECK_POCKET]})
    (pocket,) = result["interfaces"]
    assert pocket["core_points_in_material"] == 0
    assert pocket["wall_material_fraction"] < topology.WALL_MATERIAL_FRACTION
    assert result["status"] == "fail"


def test_invalid_interface_declaration_fails_with_a_reason():
    spec = {"interfaces": [{"kind": "slot", "name": "x"}, {"kind": "hole", "name": "no-axis"}]}
    result = topology.interface_check(_flute(), spec)
    assert result["status"] == "fail"
    assert [r["name"] for r in result["interfaces"]] == ["x", "no-axis"]
    assert all("invalid declaration" in r["error"] for r in result["interfaces"])


# --- gate integration ---------------------------------------------------------------------


def _gate_result(tmp_path: Path, mesh: trimesh.Trimesh, spec: dict) -> dict:
    stl = tmp_path / "output.stl"
    png = tmp_path / "preview.png"
    mesh.export(stl)
    png.write_bytes(b"png")
    context = ObjectiveContext(
        trial_id="t", model_id="m", instrument_id=spec.get("id", "flute"), seed=0,
        scad_path=tmp_path / "input.scad", artifacts=RenderArtifacts(stl_path=stl, png_path=png),
    )
    return runner.mesh_objective_gate(spec, part_module_counter=lambda _p: 0)(context)


BASE_SPEC = {"id": "flute", "envelope_mm": [30, 30, 120], "min_bodies": 1, "min_wall_mm": 1.0}


def test_undeclared_spec_reports_not_declared_and_adds_no_sub_scores(tmp_path):
    result = _gate_result(tmp_path, _flute(), BASE_SPEC)

    assert list(result["sub_scores"]) == [
        "renders", "watertight", "nonzero_volume", "fits_envelope", "min_wall", "body_count"
    ]
    assert result["checks"] == {"topology": {"status": "not declared"},
                                "interfaces": {"status": "not declared"}}


def test_declared_topology_and_interfaces_become_sub_scores(tmp_path):
    spec = {**BASE_SPEC, "topology": {"betti": [1, 4, 0]},
            "interfaces": [_tone_hole(z) for z in TONE_HOLE_Z]}

    good = _gate_result(tmp_path, _flute(), spec)
    assert good["sub_scores"]["topology"] == 1.0 and good["sub_scores"]["interfaces"] == 1.0
    assert len(good["sub_scores"]) == 8
    assert good["checks"]["topology"]["status"] == "pass"

    bad = _gate_result(tmp_path, _flute(holes=TONE_HOLE_Z[:2]), spec)
    assert bad["sub_scores"]["topology"] == 0.0 and bad["sub_scores"]["interfaces"] == 0.0
    assert bad["passed"] is False
    # Same base sub-scores either way (the ray-cast min wall reads thin at the
    # tone-hole edges on both), so the two declared checks move the rate by 2/8.
    base = ("renders", "watertight", "nonzero_volume", "fits_envelope", "min_wall", "body_count")
    assert {k: good["sub_scores"][k] for k in base} == {k: bad["sub_scores"][k] for k in base}
    assert good["objective_pass_rate"] - bad["objective_pass_rate"] == pytest.approx(2 / 8)


def test_normalized_objective_keeps_checks(tmp_path):
    normalized = _normalize_gate_result(_gate_result(tmp_path, _torus(), BASE_SPEC))
    assert normalized["checks"]["topology"] == {"status": "not declared"}


#: Scoreline over every shipped registry spec for the flute fixture, generated on
#: main at b47b89e, which has no topology code at all. It is a baseline that does
#: not run this feature's integration path. Regenerate it only when the registry
#: or the base gate changes, never to absorb a change made by the topology
#: feature.
PRE_FEATURE_SCORELINE = Path(__file__).parent / "fixtures" / "topology_undeclared_registry_scoreline.json"


def test_existing_registry_scorelines_match_the_pre_feature_baseline(tmp_path):
    """Every shipped spec (none declare topology/interfaces), scored by the current
    gate, gives exactly the scoreline bytes main produced before this feature.

    The comparison is against a pinned pre-feature fixture, not against a
    monkeypatched run of this code, so a sub-score or rate change anywhere in the
    runner integration, including after ``declared_checks()``, fails here.
    """

    registry = runner.load_arena_registry(Path("tasks/code_cad_arena/registry.json"))
    specs = registry["instruments"]
    assert specs and not any("topology" in s or "interfaces" in s for s in specs)

    mesh = _flute()
    trials = []
    for index, spec in enumerate(specs):
        sub = tmp_path / f"{index}"
        sub.mkdir()
        objective = _normalize_gate_result(_gate_result(sub, mesh, dict(spec)))
        trials.append({"trial_id": f"t{index}", "model_id": f"model-{index % 3}",
                       "status": "scored", "result": {"objective": objective}})
    rows = runner.collect_objective_scoreline({"trials": trials})

    assert json.dumps(rows, indent=2, sort_keys=True) + "\n" == PRE_FEATURE_SCORELINE.read_text()
