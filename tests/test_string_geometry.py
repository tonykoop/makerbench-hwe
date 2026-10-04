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


@pytest.mark.parametrize("runouts", [False, True])
def test_doubled_courses_count_every_string(runouts):
    """#995 review: six 0.4 mm strings in three courses of two, 0.8 mm apart. Side-by-side
    stretches overlap along their axis, so they are never merged; with run-outs past the nut
    and bridge, each run-out joins its own string, not its course partner."""
    parts = [_instrument(0)]
    z = BOX_TOP + 8
    for x in (-20.0, 0.0, 20.0):
        for dx in (-0.4, 0.4):
            if runouts:
                parts.append(_string(x + dx, z, z, length=SCALE + 120, y0=-SCALE / 2 - 60, r=0.2))
            else:
                parts.append(_string(x + dx, z, z, length=SCALE + 4, y0=-SCALE / 2 - 2, r=0.2))
    result = sg.advise(_spec(), trimesh.boolean.union(parts, engine="manifold"))
    assert result["detected"] == 6
    assert result["status"] == "consistent", result["failures"]
    if runouts:
        assert all(s["segments"] == 3 for s in result["strings"])


def test_run_outs_bent_over_the_nut_and_bridge_belong_to_their_string():
    """#995 review: 60 mm run-outs leaving the nut and the bridge at about 9.5 degrees are the
    same six strings (not 18), and the speaking length is still nut to bridge."""
    parts = [_instrument(6)]
    z = BOX_TOP + 8
    tilt = np.radians(9.5)
    for x in np.linspace(-25, 25, 6):
        for sign in (-1.0, 1.0):
            start = np.array([x, sign * SCALE / 2, z])
            end = start + 60.0 * np.array([0.0, sign * np.cos(tilt), np.sin(tilt)])
            parts.append(trimesh.creation.cylinder(radius=0.6, segment=[start, end], sections=8))
    result = sg.advise(_spec(), trimesh.boolean.union(parts, engine="manifold"))
    assert result["detected"] == 6 and result["measured"]["segments"] == 18
    assert result["status"] == "consistent", result["failures"]
    for s in result["strings"]:
        assert s["segments"] == 3
        assert s["speaking_length_mm"] == pytest.approx(SCALE - 6, abs=3.0)


def test_protrusion_touching_every_string_near_an_end_is_flagged():
    """#995 review: a block 30 mm inside the nut, touching every string, is not a support: the
    nut the strings are fused into terminates them, so the block is a contact fault."""
    result = sg.advise(_spec(), _with(_instrument(), _block(160, 170, BOX_TOP + 8)))
    assert result["detected"] == 6
    clearance = [f for f in result["failures"] if f["check"] == "string_clearance"]
    assert len(clearance) == 6, result["failures"]
    for f in clearance:
        (contact,) = f["contacts"]
        # 30-42 mm inside the nut end of the 404 mm path, whichever way the axis runs
        assert f["measured"] <= 0.0 and min(contact["from"], 1.0 - contact["to"]) == pytest.approx(0.08, abs=0.03)
    _explained(clearance)


def _saddled(saddle_width):
    """Strings from the nut over a narrow saddle 20 mm in from the bridge anchor, then bent down
    (~11 degrees) into the bridge block: a saddle beside the anchor, as on a guitar bridge."""
    z = BOX_TOP + 8
    box = trimesh.creation.box(extents=[80, SCALE + 60, BOX_TOP])
    box.apply_translation([0, 0, BOX_TOP / 2])
    nut = trimesh.creation.box(extents=[70, 6, 10])
    nut.apply_translation([0, SCALE / 2, BOX_TOP + 5])
    anchor = trimesh.creation.box(extents=[70, 8, 6])
    anchor.apply_translation([0, -SCALE / 2, BOX_TOP + 3])
    saddle = trimesh.creation.box(extents=[70, saddle_width, z - BOX_TOP + 1])
    saddle.apply_translation([0, -SCALE / 2 + 20, BOX_TOP - 1 + (z - BOX_TOP + 1) / 2])
    parts = [box, nut, anchor, saddle]
    for x in np.linspace(-25, 25, 6):
        top = np.array([x, -SCALE / 2 + 20, z])
        parts.append(trimesh.creation.cylinder(radius=0.6, segment=[top, [x, SCALE / 2 + 2, z]], sections=8))
        parts.append(trimesh.creation.cylinder(radius=0.6, segment=[top, [x, -SCALE / 2, BOX_TOP + 4]], sections=8))
    return trimesh.boolean.union(parts, engine="manifold")


@pytest.mark.parametrize("width", [3.0, 6.0])
def test_narrow_saddle_does_not_erase_the_strings(width):
    """#995 review: a 3 mm saddle fused under every string joined all six through its thin side
    faces into one wide group, which was discarded: 0 strings. Plate faces are dropped first."""
    result = sg.advise(_spec(scale_length_mm=380), _saddled(width))
    assert result["detected"] == 6
    assert result["status"] == "consistent", result["failures"]


def _afterlength(deg, length=60.0):
    """Six strings nut -> bridge, then a straight afterlength descending ``deg`` to a tailpiece."""
    z = BOX_TOP + 8
    parts = [_instrument(6)]
    t = np.radians(deg)
    for x in np.linspace(-25, 25, 6):
        start = np.array([x, -SCALE / 2, z])
        end = start + length * np.array([0.0, -np.cos(t), -np.sin(t)])
        parts.append(trimesh.creation.cylinder(radius=0.6, segment=[start, end], sections=8))
    tail = trimesh.creation.box(extents=[70, 6, 12])
    tail.apply_translation([0, -SCALE / 2 - length * np.cos(t), z - length * np.sin(t) - 4])
    parts.append(tail)
    return trimesh.boolean.union(parts, engine="manifold")


def test_shallow_afterlength_is_an_ambiguous_termination_not_a_fault():
    """#995 review: a 1 degree afterlength to a fused tailpiece read as speaking string (451 mm)
    with clearance faults at the bridge. A bend under the collinear limit at a short contact is
    reported as an ambiguous termination; the speaking length stops at the bridge."""
    result = sg.advise(_spec(), _afterlength(1.0))
    assert result["detected"] == 6
    assert result["status"] == "consistent", result["failures"]
    assert len(result["ambiguous_terminations"]) == 6
    assert all(a["break_angle_deg"] == pytest.approx(1.0, abs=0.2) for a in result["ambiguous_terminations"])
    for s in result["strings"]:
        assert s["speaking_length_mm"] == pytest.approx(SCALE - 6, abs=3.0)
    # a clear break angle is an ordinary bent run-out, nothing ambiguous
    steep = sg.advise(_spec(), _afterlength(9.5))
    assert steep["status"] == "consistent" and steep["ambiguous_terminations"] == []


def _headstock(deg):
    z = BOX_TOP + 8
    parts = [_instrument(6)]
    t = np.radians(deg)
    for x in np.linspace(-25, 25, 6):
        start = np.array([x, SCALE / 2, z])
        parts.append(trimesh.creation.cylinder(
            radius=0.6, segment=[start, start + 60.0 * np.array([0.0, np.cos(t), np.sin(t)])], sections=8))
    return trimesh.boolean.union(parts, engine="manifold")


@pytest.mark.parametrize("deg, unsupported", [(17.0, False), (21.0, True), (25.0, True)])
def test_steep_headstock_runouts_are_reported_not_counted(deg, unsupported):
    """#995 review: run-outs past 20 degrees counted as 6 extra strings. They belong to their
    string; past the calibrated range they are reported as unsupported, never failed."""
    result = sg.advise(_spec(), _headstock(deg))
    assert result["detected"] == 6
    assert result["status"] == "consistent", result["failures"]
    assert bool(result["unsupported_runouts"]) is unsupported
    if unsupported:
        assert all(r["angles_deg"] == [pytest.approx(deg, abs=0.5)] for r in result["unsupported_runouts"])


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
    assert keys == {(tier, name) for tier in ("blind", "repo") for name in ("acoustic", "strings", "assembly_fit")}
    strings_blind = next(r for r in rows if r["context_tier"] == "blind" and r["advisory"] == "strings")
    assert strings_blind["status_counts"] == {"inconsistent": 1}
    assert strings_blind["failures"][0]["check"] == "string_length"


# --- memory: shape_diameter casts its rays in batches (strings advisory OOM fix) -------------

def test_shape_diameter_batches_are_identical_to_one_cast(monkeypatch, good):
    monkeypatch.setattr(sg, "SDF_RAY_BATCH", 10**9)
    whole = sg.shape_diameter(good)
    monkeypatch.setattr(sg, "SDF_RAY_BATCH", 97)  # uneven batches
    assert np.array_equal(whole, sg.shape_diameter(good))


def test_strings_advisory_casts_at_most_a_batch_of_rays_per_call(monkeypatch, good):
    """The production path (advise) never hands the ray caster more than SDF_RAY_BATCH rays."""
    intersector = type(good.ray)
    real = intersector.intersects_id
    sizes = []

    def spy(self, ray_origins, ray_directions, *args, **kwargs):
        sizes.append(len(ray_origins))
        return real(self, ray_origins, ray_directions, *args, **kwargs)

    monkeypatch.setattr(intersector, "intersects_id", spy)
    result = sg.advise(_spec(), good)
    assert result["status"] == "consistent"
    assert sum(sizes) >= len(good.faces) and max(sizes) <= sg.SDF_RAY_BATCH <= 1000


def test_oversized_mesh_is_not_measured(monkeypatch, good):
    monkeypatch.setattr(sg, "MAX_FACES", len(good.faces) - 1)
    result = sg.advise(_spec(), good)
    assert result["status"] == "not measurable" and result["reason"] == "too_large"


def test_strings_advisory_peak_memory_on_a_dense_instrument(tmp_path):
    """Absolute peak in a fresh process on a 140k-face, 14-string instrument (just under
    MAX_FACES, so the full per-face cast runs): under 2 GiB. Measured ~440 MiB and ~60 s batched;
    the unbatched cast passed 15 GB on this mesh, and took a 91k-face frontier harpsichord to
    18 GB in the scoring gate."""
    import os
    import subprocess
    import sys

    root = str(Path(__file__).resolve().parents[1])
    script = tmp_path / "dense.py"
    script.write_text(
        "import resource, sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
        "from makerbench import string_geometry as sg\n"
        f"assert sg.__file__.startswith({root!r}), sg.__file__\n"
        "from test_string_geometry import _instrument, _spec\n"
        "mesh = _instrument(14)\n"
        "for _ in range(4):\n"
        "    mesh = mesh.subdivide()\n"
        "assert len(mesh.faces) <= sg.MAX_FACES\n"
        "r = sg.advise(_spec(string_count=14), mesh)\n"
        "print(len(mesh.faces), r['status'], r['detected'], resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([root, os.environ.get("PYTHONPATH", "")])}
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=900,
                         cwd=root, env=env, check=True).stdout.split()
    faces, status, detected, peak_mib = int(out[0]), out[1], int(out[2]), int(out[-1])
    assert faces > 130_000 and status == "consistent" and detected == 14
    assert peak_mib < 2048, (faces, peak_mib)
