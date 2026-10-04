"""Advisory string-instrument geometry (#981): detection, count, lengths, clearance.

Fixtures are unioned meshes (as OpenSCAD exports them): a box soundbox, a bridge and a nut,
and strings fused to both, so detection cannot rely on separate bodies.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from makerbench import advisory_report
from makerbench import code_cad_arena_runner as runner
from makerbench import string_geometry as sg
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts

REGISTRY = Path(__file__).resolve().parents[1] / "tasks" / "code_cad_arena" / "registry.json"
EXPLANATION_KEYS = {"check", "measured", "threshold", "unit", "requires", "body_id", "detail"}

SCALE = 400.0
BOX_TOP = 40.0


def _string(x, z0, z1, length=SCALE, y0=-SCALE / 2, r=0.6):
    """A capsule-like string from (x, y0, z0) to (x, y0 + length, z1)."""
    a, b = np.array([x, y0, z0]), np.array([x, y0 + length, z1])
    rod = trimesh.creation.cylinder(radius=r, segment=[a, b], sections=8)
    return rod


def _instrument(n=6, *, buried=(), lengths=None, string_height=8.0):
    """A soundbox with a nut and a bridge; ``n`` strings fused to both ends."""
    box = trimesh.creation.box(extents=[80, SCALE + 60, BOX_TOP])
    box.apply_translation([0, 0, BOX_TOP / 2])
    nut = trimesh.creation.box(extents=[70, 6, string_height + 2])
    nut.apply_translation([0, SCALE / 2, BOX_TOP + (string_height + 2) / 2])
    bridge = trimesh.creation.box(extents=[70, 6, string_height + 2])
    bridge.apply_translation([0, -SCALE / 2, BOX_TOP + (string_height + 2) / 2])
    parts = [box, nut, bridge]
    xs = np.linspace(-25, 25, n)
    for i, x in enumerate(xs):
        length = (lengths or [SCALE] * n)[i]
        z = BOX_TOP - 1.0 if i in buried else BOX_TOP + string_height
        parts.append(_string(x, z, z, length=length + 4, y0=-SCALE / 2 - 2))
    return trimesh.boolean.union(parts, engine="manifold")


def _spec(**constraints):
    return {"id": "box-guitar", "family": "strings", "task_kind": "multi_part_assembly",
            "assembly": True, "min_bodies": 1, "envelope_mm": [200, 600, 200], "min_wall_mm": 0.5,
            "constraints": {"string_count": 6, "scale_length_mm": SCALE, **constraints}}


@pytest.fixture(scope="module")
def good():
    return _instrument()


def _explained(failures):
    assert failures
    for failure in failures:
        assert EXPLANATION_KEYS <= set(failure), failure
        assert failure["detail"]


def test_strings_are_detected_on_a_unioned_mesh(good):
    assert good.body_count == 1  # fused: detection cannot use separate bodies
    found = sg.detect_strings(good)
    assert len(found["strings"]) == 6
    for s in found["strings"]:
        assert s["length_mm"] == pytest.approx(SCALE, rel=0.05)
        assert s["diameter_mm"] == pytest.approx(1.2, abs=0.2)


def test_good_instrument_is_consistent(good):
    result = sg.advise(_spec(), good)
    assert result["label"] == "advisory" and result["affects_scoring"] is False
    assert result["status"] == "consistent", result["failures"]
    assert result["detected"] == 6 and len(result["strings"]) == 6
    assert all(s["min_clearance_mm"] > 1.0 for s in result["strings"])


def test_missing_strings_are_counted_and_explained():
    result = sg.advise(_spec(string_count=8), _instrument(6))
    (failure,) = result["failures"]
    assert failure["check"] == "string_count" and failure["measured"] == 6 and failure["threshold"] == 8
    _explained(result["failures"])


def test_sympathetic_strings_are_allowed(good):
    result = sg.advise(_spec(string_count=4, sympathetic_string_count=2), good)
    assert not [f for f in result["failures"] if f["check"] == "string_count"]


def test_wrong_scale_length_is_flagged(good):
    result = sg.advise(_spec(scale_length_mm=600), good)
    (failure,) = result["failures"]
    assert failure["check"] == "string_length" and failure["threshold"] == 600
    assert failure["which"] == "every string" and len(failure["strings"]) == 6
    assert failure["measured"] == pytest.approx(SCALE, rel=0.05)
    _explained([failure])


def test_harp_style_length_range_checks_both_ends():
    harp = _instrument(4, lengths=[150, 250, 330, 400])
    ok = sg.advise(_spec(string_count=4, scale_length_mm=None, string_length_range_mm=[150, 400]), harp)
    assert not [f for f in ok["failures"] if f["check"] == "string_length"], ok["failures"]
    bad = sg.advise(_spec(string_count=4, scale_length_mm=None, string_length_range_mm=[250, 400]), harp)
    (failure,) = [f for f in bad["failures"] if f.get("which") == "shortest string"]
    assert failure["threshold"] == 250


def test_string_lying_in_the_soundboard_is_flagged():
    result = sg.advise(_spec(), _instrument(6, buried=(2,), string_height=8.0))
    # a string sunk into the top has no free surface along its span: it is either not detected
    # (count) or detected with no clearance; either way the advisory explains it
    checks = {f["check"] for f in result["failures"]}
    assert result["status"] == "inconsistent" and checks & {"string_count", "string_clearance"}
    _explained(result["failures"])


def test_string_resting_on_the_top_has_no_clearance():
    low = _instrument(6, string_height=0.9)  # 0.3 mm above the top
    result = sg.advise(_spec(), low)
    clearance = [f for f in result["failures"] if f["check"] == "string_clearance"]
    assert len(clearance) == 6 and all(f["body_id"].startswith("string_") for f in clearance)
    assert all(f["measured"] < sg.MIN_CLEARANCE_MM for f in clearance)
    _explained(clearance)


def _with(mesh, *blocks):
    return trimesh.boolean.union([mesh, *blocks], engine="manifold")


def _block(y0, y1, top):
    """A block on the soundbox top across every string, from y0 to y1, up to ``top``."""
    block = trimesh.creation.box(extents=[70, y1 - y0, top - BOX_TOP + 1])
    block.apply_translation([0, (y0 + y1) / 2, BOX_TOP - 1 + (top - BOX_TOP + 1) / 2])
    return block


def test_strings_running_past_the_nut_and_bridge_are_counted_once():
    # each string is cut into three stretches by the nut and the bridge: count strings, not stretches
    parts = [_instrument(0)]
    for x in np.linspace(-25, 25, 6):
        parts.append(_string(x, BOX_TOP + 8, BOX_TOP + 8, length=SCALE + 120, y0=-SCALE / 2 - 60))
    mesh = trimesh.boolean.union(parts, engine="manifold")
    result = sg.advise(_spec(), mesh)
    assert result["measured"]["segments"] == 18 and result["detected"] == 6
    assert result["status"] == "consistent", result["failures"]
    for s in result["strings"]:
        assert s["segments"] == 3
        assert s["speaking_length_mm"] == pytest.approx(SCALE - 6, abs=2.0)  # between the nut and bridge faces
        assert [k["kind"] for k in s["supports"] if k["kind"] != "anchor"] == ["support", "support"]


def test_one_short_string_among_full_length_ones_is_flagged():
    # a 199 mm string among 394 mm strings: the median passes a 400 mm scale, this must not
    parts = [_instrument(5)]
    stub_bridge = trimesh.creation.box(extents=[6, 6, 10])
    stub_bridge.apply_translation([30, 0, BOX_TOP + 5])
    parts += [stub_bridge, _string(30, BOX_TOP + 8, BOX_TOP + 8, length=203, y0=-2)]
    result = sg.advise(_spec(), trimesh.boolean.union(parts, engine="manifold"))
    (failure,) = [f for f in result["failures"] if f["check"] == "string_length"]
    assert failure["which"] == "every string" and len(failure["strings"]) == 1
    assert failure["measured"] == pytest.approx(194.0, abs=3.0)  # stub bridge face to nut face
    _explained([failure])


def test_protrusion_touching_every_string_mid_span_is_flagged():
    # a ridge on the top that reaches the strings in the middle (fused: 0 mm clearance)
    result = sg.advise(_spec(), _with(_instrument(), _block(-5, 5, BOX_TOP + 8)))
    assert result["detected"] == 6
    clearance = [f for f in result["failures"] if f["check"] == "string_clearance"]
    assert len(clearance) == 6
    for f in clearance:
        assert f["measured"] <= 0.0 and f["contacts"][0]["from"] == pytest.approx(0.5, abs=0.03)
    _explained(clearance)


def test_low_clearance_without_contact_is_flagged():
    # the ridge stops 0.5 mm under the strings' surface: not fused, still too close
    result = sg.advise(_spec(), _with(_instrument(), _block(-5, 5, BOX_TOP + 8 - 0.6 - 0.5)))
    clearance = [f for f in result["failures"] if f["check"] == "string_clearance"]
    assert len(clearance) == 6 and all(0.3 < f["measured"] < 0.7 for f in clearance)


def test_string_lying_on_the_body_near_its_end_is_flagged():
    # a 60 mm stretch next to the bridge where the strings lie on a raised deck: too long for a support
    result = sg.advise(_spec(), _with(_instrument(), _block(-190, -130, BOX_TOP + 8)))
    clearance = [f for f in result["failures"] if f["check"] == "string_clearance"]
    assert len(clearance) == 6
    for f in clearance:
        (contact,) = f["contacts"]
        assert contact["length_mm"] == pytest.approx(62.0, abs=3.0)  # the deck plus the fused flanks
        assert min(contact["from"], 1.0 - contact["to"]) < 0.05  # next to the bridge end, either direction


def test_declared_intermediate_bridges_are_supports():
    mesh = _with(_instrument(), _block(-3, 3, BOX_TOP + 8))  # a 6 mm bridge mid-span under every string
    plain = sg.advise(_spec(), mesh)
    assert {f["check"] for f in plain["failures"]} >= {"string_clearance"}
    zheng = sg.advise(_spec(bridges="moveable inverted-V bridges", scale_length_mm=200), mesh)
    assert zheng["declared"]["intermediate_bridges"] is True
    assert not [f for f in zheng["failures"] if f["check"] == "string_clearance"], zheng["failures"]
    assert all(s["speaking_length_mm"] == pytest.approx(SCALE / 2 - 6, abs=3.0) for s in zheng["strings"])


def test_separate_string_bodies_resting_on_the_top_are_flagged():
    # not unioned: each string is its own body lying 0.2 mm above the top for its whole length
    box = trimesh.creation.box(extents=[80, SCALE + 60, BOX_TOP])
    box.apply_translation([0, 0, BOX_TOP / 2])
    strings = [_string(x, BOX_TOP + 0.8, BOX_TOP + 0.8) for x in np.linspace(-25, 25, 6)]
    result = sg.advise(_spec(), trimesh.util.concatenate([box, *strings]))
    assert result["detected"] == 6
    assert len([f for f in result["failures"] if f["check"] == "string_clearance"]) == 6


def test_thin_soundboard_is_not_a_string():
    plate = trimesh.creation.box(extents=[200, 400, 3])
    assert sg.detect_strings(plate)["strings"] == []


def test_other_families_are_not_modelled(good):
    result = sg.advise({"family": "woodwind"}, good)
    assert result["status"] == "not modelled" and "not a string instrument" in result["reason"]


def test_shipped_registry_string_specs_declare_what_the_check_reads():
    specs = [s for s in json.loads(REGISTRY.read_text())["instruments"] if s["family"] == "strings"]
    assert all(sg.modelled_reason(s) is None for s in specs)
    sambuca = next(s for s in specs if s["id"] == "sambuca")
    assert sg.declared_count(sambuca["constraints"]) == (13, 0)
    assert sg.declared_lengths(sambuca["constraints"])["longest"] == 580
    ukulele = next(s for s in specs if s["id"] == "ukulele")
    assert sg.declared_count(ukulele["constraints"])[0] == 4  # "strings": 4
    assert sg.declared_lengths(ukulele["constraints"]) == {"scale": 381.0, "source": "scale_length_mm"}


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


@pytest.mark.parametrize("scale", [SCALE, 600.0])
def test_string_advisory_never_changes_scoring(tmp_path, monkeypatch, good, scale):
    spec = _spec(scale_length_mm=scale)
    with_advisory = _gate(tmp_path, good, spec)
    monkeypatch.setattr(sg, "advise", lambda spec, mesh: {"status": "stubbed"})
    without = _gate(tmp_path, good, spec)
    assert with_advisory["advisory"]["strings"]["family"] == "string_geometry"
    assert with_advisory["sub_scores"] == without["sub_scores"]
    assert with_advisory["objective_pass_rate"] == without["objective_pass_rate"]
    assert with_advisory["failures"] == without["failures"]


def test_string_advisory_failure_never_breaks_scoring(tmp_path, monkeypatch, good):
    def boom(spec, mesh):
        raise RuntimeError("ray caster exploded")

    monkeypatch.setattr(sg, "advise", boom)
    result = _gate(tmp_path, good, _spec())
    assert result["advisory"]["strings"]["status"] == "error"
    assert "objective_pass_rate" in result


def test_report_rows_split_advisories_and_tiers(tmp_path, good):
    payload = _gate(tmp_path, good, _spec(scale_length_mm=600))
    objective = {"objective_pass_rate": payload["objective_pass_rate"], "advisory": payload["advisory"]}
    log = {"trials": [
        {"trial_id": "a", "model_id": "m", "instrument_id": "box-guitar", "seed": 0, "status": "scored",
         "result": {"context_tier": "blind", "objective": objective}},
        {"trial_id": "b", "model_id": "m", "instrument_id": "box-guitar", "seed": 0, "status": "scored",
         "result": {"context_tier": "repo", "objective": objective}}]}
    rows = advisory_report.collect_advisory_report(log)["rows"]
    keys = {(r["context_tier"], r["advisory"]) for r in rows}
    assert keys == {("blind", "acoustic"), ("blind", "strings"), ("repo", "acoustic"), ("repo", "strings")}
    strings_blind = next(r for r in rows if r["context_tier"] == "blind" and r["advisory"] == "strings")
    assert strings_blind["status_counts"] == {"inconsistent": 1}
    assert strings_blind["failures"][0]["check"] == "string_length"
