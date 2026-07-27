"""Unified headless-CAD environment wrapper (makerbench-hwe #306).

The FakeBackend path runs everywhere (no CAD engine); the OpenSCADBackend
integration test is gated on the binary being present (it is, in CI).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from makerbench.environment_wrapper import (
    DEFAULT_VIEWPORTS,
    HWE_PLACEMENTS,
    CADBackend,
    EnvironmentResult,
    FakeBackend,
    OpenSCADBackend,
    UnifiedEnvironment,
    build_for_all_placements,
)
from makerbench.render import openscad_available

CUBE = "cube([10, 10, 10]);"


# --- backend protocol -------------------------------------------------------

def test_backends_satisfy_protocol():
    assert isinstance(FakeBackend(), CADBackend)
    assert isinstance(OpenSCADBackend(), CADBackend)


# --- fake backend: unified build returns geometry + viewports ---------------

def test_build_returns_geometry_and_all_viewports(tmp_path):
    env = UnifiedEnvironment(backend=FakeBackend())
    result = env.build("HWE-01", CUBE, str(tmp_path))

    assert isinstance(result, EnvironmentResult)
    assert result.compiled is True
    assert result.geometry_path and Path(result.geometry_path).exists()
    assert set(result.viewports) == set(DEFAULT_VIEWPORTS)
    for path in result.viewports.values():
        assert Path(path).exists()
    assert result.warnings == []


def test_one_interface_serves_all_four_placements(tmp_path):
    sources = {pid: CUBE for pid in HWE_PLACEMENTS}
    results = build_for_all_placements(sources, str(tmp_path), backend=FakeBackend())
    assert set(results) == set(HWE_PLACEMENTS)
    for pid, res in results.items():
        assert res.placement_id == pid
        assert res.compiled and res.geometry_path
        assert set(res.viewports) == set(DEFAULT_VIEWPORTS)


def test_unknown_placement_is_served_with_warning(tmp_path):
    env = UnifiedEnvironment(backend=FakeBackend())
    result = env.build("HWE-99", CUBE, str(tmp_path))
    assert result.compiled
    assert any("not in" in w for w in result.warnings)


def test_compile_failure_is_recorded_not_raised(tmp_path):
    env = UnifiedEnvironment(backend=FakeBackend())
    result = env.build("HWE-02", "FAIL_COMPILE", str(tmp_path))
    assert result.compiled is False
    assert result.geometry_path is None
    assert any(w.startswith("compile:") for w in result.warnings)
    # renders are independent and still happen
    assert set(result.viewports) == set(DEFAULT_VIEWPORTS)


def test_custom_viewports_and_format(tmp_path):
    env = UnifiedEnvironment(
        backend=FakeBackend(),
        viewports={"only": None},
        geometry_format="stl",
    )
    result = env.build("HWE-03", CUBE, str(tmp_path))
    assert result.geometry_format == "stl"
    assert result.geometry_path.endswith("output.stl")
    assert set(result.viewports) == {"only"}


def test_result_to_dict_is_serializable(tmp_path):
    env = UnifiedEnvironment(backend=FakeBackend())
    d = env.build("HWE-04", CUBE, str(tmp_path)).to_dict()
    assert d["placement_id"] == "HWE-04" and d["backend"] == "fake"
    assert "viewports" in d and "geometry_path" in d


def test_determinism_same_source_same_geometry_bytes(tmp_path):
    env = UnifiedEnvironment(backend=FakeBackend())
    a = env.build("HWE-01", CUBE, str(tmp_path / "a"))
    b = env.build("HWE-01", CUBE, str(tmp_path / "b"))
    assert Path(a.geometry_path).read_text() == Path(b.geometry_path).read_text()


# --- real OpenSCAD backend (gated on the binary) ----------------------------

@pytest.mark.skipif(not openscad_available(), reason="OpenSCAD binary not installed")
def test_openscad_backend_builds_real_geometry_and_renders(tmp_path):
    env = UnifiedEnvironment(backend=OpenSCADBackend(), viewports={"front": "0,0,0,0,0,0,0"})
    result = env.build("HWE-01", CUBE, str(tmp_path))
    assert result.backend == "openscad"
    assert result.compiled and Path(result.geometry_path).stat().st_size > 0
    front = result.viewports["front"]
    assert Path(front).suffix == ".png" and Path(front).stat().st_size > 0
