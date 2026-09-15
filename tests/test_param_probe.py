"""Parametric edit harness (#788 W1b): change one declared parameter, recompile
in the W0 sandbox, measure the effect.

Hermetic tests use a fake compiler that emits an ASCII STL box from the
fixture's parameters. Real-sandbox tests run OpenSCAD inside Bubblewrap and
are skipped only when it is unavailable; ``MAKERBENCH_REQUIRE_SANDBOX=1`` (CI)
turns that skip into a failure.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from makerbench import cad_params, param_probe, render, scad_sandbox
from makerbench.cad_params import ParameterError
from makerbench.cli import app
from makerbench.code_cad_objective import RenderArtifacts
from makerbench.param_probe import Expectation, probe_master, probe_parameter

FIXTURES = Path(__file__).parent / "fixtures" / "param_probe"
BOX = (FIXTURES / "box.scad").read_text(encoding="utf-8")
TWO_BODIES = (FIXTURES / "two_bodies.scad").read_text(encoding="utf-8")

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_AVAILABLE = scad_sandbox.sandbox_available()
if REQUIRE_SANDBOX and not _AVAILABLE:  # pragma: no cover - CI guard
    pytest.fail("MAKERBENCH_REQUIRE_SANDBOX=1 but the OpenSCAD sandbox cannot start", pytrace=False)
needs_sandbox = pytest.mark.skipif(not _AVAILABLE, reason="OpenSCAD sandbox unavailable here")

runner = CliRunner()


# --- hermetic fake compiler ---------------------------------------------------


def _box_stl(w: float, d: float, h: float, ox: float = 0.0, oy: float = 0.0) -> str:
    """A closed ASCII STL box with corner at (ox, oy, 0)."""

    x0, y0, z0, x1, y1, z1 = ox, oy, 0.0, ox + w, oy + d, h
    v = {
        "a": (x0, y0, z0), "b": (x1, y0, z0), "c": (x1, y1, z0), "d": (x0, y1, z0),
        "e": (x0, y0, z1), "f": (x1, y0, z1), "g": (x1, y1, z1), "h": (x0, y1, z1),
    }
    faces = [
        ("a", "c", "b"), ("a", "d", "c"),  # bottom
        ("e", "f", "g"), ("e", "g", "h"),  # top
        ("a", "b", "f"), ("a", "f", "e"),  # front
        ("b", "c", "g"), ("b", "g", "f"),  # right
        ("c", "d", "h"), ("c", "h", "g"),  # back
        ("d", "a", "e"), ("d", "e", "h"),  # left
    ]
    out = ["solid box"]
    for tri in faces:
        out.append("facet normal 0 0 0\nouter loop")
        for key in tri:
            out.append("vertex %g %g %g" % v[key])
        out.append("endloop\nendfacet")
    out.append("endsolid box\n")
    return "\n".join(out)


def _fake_compiler(calls: list | None = None):
    """Parse ``name = number;`` from the fixture and emit a box STL. ``h_mm = 0``
    is a compile failure, like OpenSCAD's empty mesh."""

    def compile_(scad_path: Path, out_dir: Path) -> RenderArtifacts:
        if calls is not None:
            calls.append(scad_path)
        src = scad_path.read_text(encoding="utf-8")
        vals = {m.group(1): float(m.group(2)) for m in re.finditer(r"^(\w+)\s*=\s*(-?[0-9.]+)\s*;", src, re.M)}
        vec = re.search(r"^offsets_mm\s*=\s*\[([^\]]*)\]", src, re.M)
        ox, oy = (float(x) for x in vec.group(1).split(",")) if vec else (0.0, 0.0)
        if vals.get("h_mm", 1) <= 0:
            raise render.CompileError("OpenSCAD produced an empty mesh (no geometry).")
        out_dir.mkdir(parents=True, exist_ok=True)
        stl = out_dir / "output.stl"
        stl.write_text(_box_stl(vals["w_mm"], vals["d_mm"], vals["h_mm"], ox, oy), encoding="utf-8")
        png = out_dir / "preview.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        return RenderArtifacts(stl_path=stl, png_path=png, warnings=("WARNING: fake",))

    return compile_


class TestMeasure:
    def test_measure_mesh_reports_bbox_volume_faces_bodies(self, tmp_path):
        stl = tmp_path / "b.stl"
        stl.write_text(_box_stl(10, 20, 5), encoding="utf-8")
        m = param_probe.measure_mesh(stl)
        assert m.size == pytest.approx((10.0, 20.0, 5.0))
        assert m.bbox_min == pytest.approx((0.0, 0.0, 0.0)) and m.bbox_max == pytest.approx((10.0, 20.0, 5.0))
        assert m.volume_mm3 == pytest.approx(1000.0)
        assert m.faces == 12 and m.bodies == 1 and m.watertight

    def test_empty_mesh_is_a_compile_error(self, tmp_path):
        stl = tmp_path / "e.stl"
        stl.write_text("solid e\nendsolid e\n", encoding="utf-8")
        with pytest.raises(render.CompileError):
            param_probe.measure_mesh(stl)


class TestProbeHermetic:
    def test_changed_with_expected_ratio(self, tmp_path):
        r = probe_parameter(BOX, "w_mm", 20, work_dir=tmp_path, compiler=_fake_compiler(),
                            expect=Expectation(axis="x", ratio=2.0))
        assert r.effect == "changed" and r.old_value == 10 and r.new_value == 20 and r.unit == "mm"
        assert r.baseline.ok and r.edited.ok
        assert r.baseline.measure.size == pytest.approx((10, 20, 5))
        assert r.edited.measure.size == pytest.approx((20, 20, 5))
        assert r.delta["size_ratio"] == pytest.approx((2.0, 1.0, 1.0))
        assert r.delta["volume_ratio"] == pytest.approx(2.0)
        assert r.expectation_met is True
        # the fixture's declared metadata travels with the result
        assert "range not declared" in r.notes
        d = r.to_dict()
        assert d["effect"] == "changed" and d["delta"]["changed"] is True and d["expectation"]["axis"] == "x"

    def test_expectation_not_met_is_reported_not_raised(self, tmp_path):
        r = probe_parameter(BOX, "w_mm", 20, work_dir=tmp_path, compiler=_fake_compiler(),
                            expect=Expectation(axis="volume", ratio=3.0))
        assert r.effect == "changed" and r.expectation_met is False
        r2 = probe_parameter(BOX, "w_mm", 20, work_dir=tmp_path / "b", compiler=_fake_compiler(),
                             expect=Expectation(axis="y"))
        assert r2.expectation_met is False  # y did not move

    def test_unchanged_when_the_parameter_has_no_geometric_effect(self, tmp_path):
        r = probe_parameter(BOX, "unused_mm", 30, work_dir=tmp_path, compiler=_fake_compiler(),
                            expect=Expectation())
        assert r.effect == "unchanged" and r.edited.ok and r.delta["changed"] is False
        assert "no geometric effect" in r.reason
        assert r.expectation_met is False

    def test_same_value_compiles_nothing_new(self, tmp_path):
        calls: list = []
        r = probe_parameter(BOX, "w_mm", 10, work_dir=tmp_path, compiler=_fake_compiler(calls))
        assert r.effect == "unchanged" and "equals the current" in r.reason
        assert len(calls) == 1  # the baseline only

    def test_edited_compile_failure_is_a_result(self, tmp_path):
        r = probe_parameter(BOX, "h_mm", 0, work_dir=tmp_path, compiler=_fake_compiler(),
                            expect=Expectation(axis="z"))
        assert r.effect == "edited_failed" and r.baseline.ok and not r.edited.ok
        assert "empty mesh" in r.reason and r.delta is None and r.expectation_met is False

    def test_baseline_failure_is_a_result(self, tmp_path):
        broken = BOX.replace("h_mm = 5;", "h_mm = 0;")
        r = probe_parameter(broken, "w_mm", 20, work_dir=tmp_path, compiler=_fake_compiler())
        assert r.effect == "baseline_failed" and r.edited is None and "empty mesh" in r.reason

    @pytest.mark.parametrize("name, value, message", [
        ("nope", 1, "unknown parameter"),
        ("half_w_mm", 1, "derived"),
        ("w_mm", "wide", "number"),
        ("wall_mm", 9, "between"),
    ])
    def test_invalid_edits_compile_nothing(self, tmp_path, name, value, message):
        calls: list = []
        with pytest.raises(ParameterError, match=message):
            probe_parameter(BOX, name, value, work_dir=tmp_path, compiler=_fake_compiler(calls))
        assert calls == []

    def test_vector_and_bool_edits(self, tmp_path):
        r = probe_parameter(BOX, "offsets_mm", [5, 7], work_dir=tmp_path, compiler=_fake_compiler())
        assert r.effect == "changed"
        assert r.delta["size_ratio"] == pytest.approx((1, 1, 1))  # same size, moved bbox
        assert r.edited.measure.bbox_min == pytest.approx((5, 7, 0))

    def test_reused_baseline_is_not_recompiled(self, tmp_path):
        calls: list = []
        base = param_probe.compile_and_measure(BOX, backend="openscad", work_dir=tmp_path / "b", compiler=_fake_compiler(calls))
        assert base.ok and len(calls) == 1
        probe_parameter(BOX, "w_mm", 20, work_dir=tmp_path, compiler=_fake_compiler(calls), baseline=base)
        assert len(calls) == 2

    def test_default_compiler_is_the_sandboxed_one_and_never_the_host(self, tmp_path, monkeypatch):
        assert param_probe.sandboxed_compiler("openscad") is scad_sandbox.compile_scad_sandboxed
        launched: list = []
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: False)
        monkeypatch.setattr(render, "_run", lambda *a, **k: launched.append(a))
        monkeypatch.setattr(scad_sandbox.subprocess, "run", lambda *a, **k: launched.append(a))
        with pytest.raises(scad_sandbox.SandboxUnavailable):
            probe_parameter(BOX, "w_mm", 20, work_dir=tmp_path)
        assert launched == []

    def test_unknown_backend_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="unknown backend"):
            param_probe.compile_and_measure(BOX, backend="blender", work_dir=tmp_path)


class TestDefaultEdit:
    def test_numbers_scale_bools_flip_strings_skip(self):
        by = cad_params.extract_parameters(BOX, "openscad").by_name()
        assert param_probe.default_edit(by["w_mm"]) == (12, None)  # int stays int
        assert param_probe.default_edit(by["w_mm"], scale=2.0) == (20, None)
        assert param_probe.default_edit(by["hollow"]) == (True, None)
        assert param_probe.default_edit(by["offsets_mm"]) == ([1.0, 1.0], None)  # zeros become 1
        value, reason = param_probe.default_edit(by["label"])
        assert value is None and "no numeric edit" in reason
        value, reason = param_probe.default_edit(by["half_w_mm"])
        assert value is None and "derived" in reason

    def test_declared_range_is_respected(self):
        by = cad_params.extract_parameters(BOX, "openscad").by_name()
        assert by["wall_mm"].range == {"min": 0.5, "max": 4, "step": 0.5}
        assert param_probe.default_edit(by["wall_mm"], scale=10) == (4, None)  # clamped to max
        assert param_probe.default_edit(by["wall_mm"], scale=0.1) == (0.5, None)  # clamped to min


class TestProbeMaster:
    def test_probes_every_editable_parameter_and_compiles_the_baseline_once(self, tmp_path):
        calls: list = []
        results = probe_master(BOX, work_dir=tmp_path, compiler=_fake_compiler(calls))
        by = {r.name: r for r in results}
        assert set(by) == {"w_mm", "d_mm", "h_mm", "unused_mm", "label", "hollow", "wall_mm", "offsets_mm", "half_w_mm"}
        assert by["w_mm"].effect == "changed" and by["d_mm"].effect == "changed" and by["h_mm"].effect == "changed"
        assert by["unused_mm"].effect == "unchanged"
        assert by["label"].effect == "skipped" and "no numeric edit" in by["label"].reason
        assert by["half_w_mm"].effect == "skipped" and "derived" in by["half_w_mm"].reason
        assert by["offsets_mm"].effect == "changed"
        probed = [r for r in results if r.effect != "skipped"]
        assert len(calls) == 1 + len(probed)
        rows = param_probe.report_rows(results)
        assert [r["name"] for r in rows] == [r.name for r in results]
        assert tmp_path.as_posix() not in json.dumps(rows) + json.dumps([r.to_dict() for r in results])

    def test_names_and_max_params(self, tmp_path):
        results = probe_master(BOX, work_dir=tmp_path, compiler=_fake_compiler(), names=["d_mm", "nope", "w_mm"], max_params=1)
        assert [(r.name, r.effect) for r in results] == [("d_mm", "changed"), ("nope", "skipped"), ("w_mm", "skipped")]
        assert "max-params" in results[2].reason


# --- real sandbox ------------------------------------------------------------


@needs_sandbox
class TestRealSandbox:
    def test_box_width_doubles_in_the_sandbox(self, tmp_path, monkeypatch):
        monkeypatch.setattr(render, "_run", lambda *a, **k: pytest.fail("host openscad path used"))
        r = probe_parameter(BOX, "w_mm", 20, work_dir=tmp_path, expect=Expectation(axis="x", ratio=2.0))
        assert r.effect == "changed", r.reason
        assert r.baseline.measure.size == pytest.approx((10, 20, 5))
        assert r.edited.measure.size == pytest.approx((20, 20, 5))
        assert r.delta["volume_ratio"] == pytest.approx(2.0)
        assert r.expectation_met is True
        assert (tmp_path / "baseline" / "artifacts" / "preview.png").stat().st_size > 0
        assert r.edited.png_path == "preview.png"

    def test_hollow_flip_changes_volume_not_bbox(self, tmp_path):
        r = probe_parameter(BOX, "hollow", True, work_dir=tmp_path, expect=Expectation(axis="volume"))
        assert r.effect == "changed"
        assert r.delta["size_ratio"] == pytest.approx((1, 1, 1))
        assert r.delta["volume_ratio"] < 1.0 and r.expectation_met is True

    def test_two_bodies_gap_moves_bbox_and_keeps_body_count(self, tmp_path):
        r = probe_parameter(TWO_BODIES, "gap_mm", 10, work_dir=tmp_path, expect=Expectation(axis="x"))
        assert r.effect == "changed" and r.expectation_met is True
        assert r.baseline.measure.bodies == 2 and r.edited.measure.bodies == 2
        assert r.baseline.measure.size[0] == pytest.approx(13) and r.edited.measure.size[0] == pytest.approx(18)

    def test_zero_height_is_an_honest_edited_failure(self, tmp_path):
        r = probe_parameter(BOX, "h_mm", 0, work_dir=tmp_path)
        assert r.effect == "edited_failed" and r.baseline.ok
        assert r.reason and ("empty" in r.reason or "no geometry" in r.reason.lower())

    def test_cli_probe_over_a_fixture_instruments_root(self, tmp_path, monkeypatch):
        root = tmp_path / "instruments"
        cad = root / "strings" / "boxolin" / "cad"
        cad.mkdir(parents=True)
        (cad / "boxolin.scad").write_text(BOX, encoding="utf-8")
        upper = root / "percussion" / "twobody" / "CAD"
        upper.mkdir(parents=True)
        (upper / "twobody.scad").write_text(TWO_BODIES, encoding="utf-8")
        monkeypatch.setattr(render, "_run", lambda *a, **k: pytest.fail("host openscad path used"))
        before = sorted(p.as_posix() for p in root.rglob("*"))
        out = tmp_path / "report.json"
        result = runner.invoke(app, [
            "arena", "param-probe", "--instruments-root", root.as_posix(), "--instruments", "boxolin,twobody",
            "--params", "w_mm,label,gap_mm", "--scale", "2", "--work-dir", (tmp_path / "work").as_posix(),
            "--out", out.as_posix(),
        ])
        assert result.exit_code == 0, result.stdout
        report = json.loads(out.read_text(encoding="utf-8"))
        assert [m["instrument"] for m in report["masters"]] == ["twobody", "boxolin"] or \
            sorted(m["instrument"] for m in report["masters"]) == ["boxolin", "twobody"]
        by = {(m["instrument"], r["name"]): r for m in report["masters"] for r in m["results"]}
        assert by[("boxolin", "w_mm")]["effect"] == "changed"
        assert by[("boxolin", "w_mm")]["delta"]["size_ratio"][0] == pytest.approx(2.0)
        assert by[("boxolin", "label")]["effect"] == "skipped"
        assert by[("twobody", "gap_mm")]["effect"] == "changed"
        assert "changed=" in result.stdout and "skipped=" in result.stdout
        # the instruments root is untouched and no host path leaks into the report
        assert sorted(p.as_posix() for p in root.rglob("*")) == before
        assert tmp_path.as_posix() not in out.read_text(encoding="utf-8")


class TestCliPreflight:
    def test_unknown_instrument_and_missing_root(self, tmp_path):
        root = tmp_path / "instruments"
        (root / "strings" / "uke" / "cad").mkdir(parents=True)
        (root / "strings" / "uke" / "cad" / "uke.scad").write_text(BOX, encoding="utf-8")
        result = runner.invoke(app, ["arena", "param-probe", "--instruments-root", root.as_posix(), "--instruments", "uke,nope"])
        assert result.exit_code == 1 and "no master for" in result.stdout and "nope" in result.stdout
        result = runner.invoke(app, ["arena", "param-probe", "--instruments-root", (tmp_path / "missing").as_posix(), "--instruments", "all"])
        assert result.exit_code == 1

    def test_unavailable_sandbox_fails_closed_without_a_host_compile(self, tmp_path, monkeypatch):
        root = tmp_path / "instruments"
        (root / "strings" / "uke" / "cad").mkdir(parents=True)
        (root / "strings" / "uke" / "cad" / "uke.scad").write_text(BOX, encoding="utf-8")
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: False)
        launched: list = []
        monkeypatch.setattr(render, "_run", lambda *a, **k: launched.append(a))
        monkeypatch.setattr(scad_sandbox.subprocess, "run", lambda *a, **k: launched.append(a))
        result = runner.invoke(app, ["arena", "param-probe", "--instruments-root", root.as_posix(), "--instruments", "uke"])
        assert result.exit_code == 1
        assert "never compiles on the host" in " ".join(result.stdout.split())
        assert launched == []

    def test_work_dir_inside_the_instruments_root_is_refused(self, tmp_path, monkeypatch):
        root = tmp_path / "instruments"
        (root / "strings" / "uke" / "cad").mkdir(parents=True)
        (root / "strings" / "uke" / "cad" / "uke.scad").write_text(BOX, encoding="utf-8")
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: True)
        result = runner.invoke(app, ["arena", "param-probe", "--instruments-root", root.as_posix(), "--instruments", "uke",
                                     "--work-dir", (root / "strings" / "uke" / "probe").as_posix()])
        assert result.exit_code == 1 and "must not be inside" in result.stdout
