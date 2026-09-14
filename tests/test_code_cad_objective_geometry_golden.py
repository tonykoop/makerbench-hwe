"""Real-kernel golden boundaries for the public Code-CAD objective gate."""

from __future__ import annotations

from pathlib import Path

import manifold3d
import numpy as np
import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench.code_cad_objective import (
    ENVELOPE_SLACK,
    MAX_PART_MODULES,
    MIN_BODY_VOLUME_MM3,
    MIN_WALL_FLOOR_MM,
    ObjectiveContext,
    RenderArtifacts,
)


def _kernel_box(extents: tuple[float, float, float]) -> trimesh.Trimesh:
    """Generate a box in manifold3d, then consume its kernel mesh in trimesh."""

    kernel_mesh = manifold3d.Manifold.cube(extents, center=True).to_mesh()
    return trimesh.Trimesh(
        vertices=np.asarray(kernel_mesh.vert_properties)[:, :3],
        faces=np.asarray(kernel_mesh.tri_verts),
        process=True,
    )


def _context(tmp_path: Path, mesh: trimesh.Trimesh) -> ObjectiveContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    stl_path = tmp_path / "kernel-output.stl"
    mesh.export(stl_path)
    png_path = tmp_path / "preview.png"
    png_path.write_bytes(b"\x89PNG\r\n")
    return ObjectiveContext(
        trial_id="golden",
        model_id="kernel",
        instrument_id="boundary",
        seed=0,
        scad_path=tmp_path / "source.scad",
        artifacts=RenderArtifacts(stl_path=stl_path, png_path=png_path),
    )


def _grade(
    tmp_path: Path,
    mesh: trimesh.Trimesh,
    *,
    envelope_mm: tuple[float, float, float] = (200.0, 200.0, 200.0),
    min_bodies: int = 1,
) -> dict:
    gate = runner.mesh_objective_gate(
        {
            "id": "boundary",
            "envelope_mm": envelope_mm,
            "min_bodies": min_bodies,
        }
    )
    return gate(_context(tmp_path, mesh))


def test_objective_constants_have_one_public_home():
    assert MIN_WALL_FLOOR_MM == runner.MIN_WALL_FLOOR_MM == 2.0
    assert MIN_BODY_VOLUME_MM3 == runner.MIN_BODY_VOLUME_MM3 == 1000.0
    assert ENVELOPE_SLACK == runner.ENVELOPE_SLACK == 1.5
    assert MAX_PART_MODULES == runner.MAX_PART_MODULES == 12


@pytest.mark.parametrize(
    ("thickness_mm", "expected"),
    ((MIN_WALL_FLOOR_MM - 0.06, 0.0), (MIN_WALL_FLOOR_MM + 0.01, 1.0)),
)
def test_min_wall_flips_across_two_mm_floor(
    tmp_path: Path, thickness_mm: float, expected: float
):
    result = _grade(tmp_path, _kernel_box((30.0, 30.0, thickness_mm)))

    assert result["sub_scores"]["min_wall"] == expected
    assert result["passed"] is bool(expected)
    assert result["metrics"]["min_wall_floor_mm"] == MIN_WALL_FLOOR_MM


@pytest.mark.parametrize(
    ("volume_mm3", "expected"),
    ((MIN_BODY_VOLUME_MM3 - 0.01, 0.0), (MIN_BODY_VOLUME_MM3 + 0.01, 1.0)),
)
def test_volume_flips_at_minimum_body_volume(
    tmp_path: Path, volume_mm3: float, expected: float
):
    side = volume_mm3 ** (1.0 / 3.0)
    result = _grade(tmp_path, _kernel_box((side, side, side)))

    assert result["sub_scores"]["nonzero_volume"] == expected
    assert result["passed"] is bool(expected)
    assert result["metrics"]["largest_body_volume_mm3"] == pytest.approx(
        volume_mm3, abs=0.01
    )


@pytest.mark.parametrize(
    ("width_mm", "expected"),
    ((100.0 * ENVELOPE_SLACK, 1.0), (100.0 * ENVELOPE_SLACK + 0.01, 0.0)),
)
def test_envelope_flips_immediately_above_slack_boundary(
    tmp_path: Path, width_mm: float, expected: float
):
    result = _grade(
        tmp_path,
        _kernel_box((width_mm, 30.0, 30.0)),
        envelope_mm=(100.0, 100.0, 100.0),
    )

    assert result["sub_scores"]["fits_envelope"] == expected
    assert result["passed"] is bool(expected)


def test_body_count_flips_from_one_to_two_real_kernel_bodies(tmp_path: Path):
    first = _kernel_box((20.0, 20.0, 20.0))
    second = _kernel_box((20.0, 20.0, 20.0))
    second.apply_translation((40.0, 0.0, 0.0))
    one = _grade(tmp_path / "one", first, min_bodies=2)
    two = _grade(
        tmp_path / "two",
        trimesh.util.concatenate((first, second)),
        min_bodies=2,
    )

    assert one["metrics"]["body_count"] == 1
    assert one["sub_scores"]["body_count"] == 0.0
    assert one["passed"] is False
    assert two["metrics"]["body_count"] == 2
    assert two["sub_scores"]["body_count"] == 1.0
    assert two["passed"] is True


def test_part_module_scan_stops_at_max_with_real_kernel_face_counts(tmp_path: Path):
    names = [f"part_{index}" for index in range(MAX_PART_MODULES + 1)]
    scad = tmp_path / "candidate.scad"
    scad.write_text("\n".join(f"module {name}() {{}}" for name in names), encoding="utf-8")
    visited: list[str] = []

    def real_face_counter(_path: Path, name: str) -> int:
        visited.append(name)
        return len(_kernel_box((2.0, 2.0, 2.0)).faces)

    count = runner.count_standalone_part_modules(scad, face_counter=real_face_counter)

    assert count == MAX_PART_MODULES
    assert visited == names[:MAX_PART_MODULES]
