"""Sandboxed multi-view and section-plane renders of a compiled candidate (#795).

``render_view`` compiles a candidate through its sandboxed compiler (OpenSCAD
through :mod:`makerbench.scad_sandbox`, CadQuery through the Bubblewrap backend
in :mod:`makerbench.cadquery_backend`) and then renders the resulting mesh with
OpenSCAD inside the same Bubblewrap wrapper. Nothing is rendered on the host.

Views are a fixed, deterministic set of orthographic cameras. Section images are
``projection(cut=true)`` of the mesh after a rigid transform that maps the cut
plane onto z=0, drawn by a top-down orthographic camera at an explicit distance,
so every pixel has a known size in millimetres and the cut area can be read
back from the image (:attr:`RenderedImage.cut_area_mm2`) and cross-checked
against :func:`makerbench.measure.section_area`.

Failures are explicit. A candidate that does not compile, or an image that is
missing or 0 bytes, gives ``ok=False`` with an error. An unavailable sandbox
raises :class:`SandboxUnavailable` before any process starts.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import trimesh

from . import render, scad_sandbox
from .entrant_sandbox import SandboxUnavailable

__all__ = [
    "SandboxUnavailable",
    "ViewSpec",
    "SectionSpec",
    "RenderedImage",
    "RenderViewResult",
    "VIEW_SET",
    "IMAGE_SIZE",
    "axis_section",
    "render_mesh_views",
    "render_view",
]

#: OpenSCAD's fixed vertical field of view. An orthographic camera at distance
#: ``d`` shows ``d * 2 * tan(fov / 2)`` millimetres across the image height.
OPENSCAD_FOV_DEG = 22.5
IMAGE_SIZE = (800, 600)
COLORSCHEME = "Tomorrow"
#: Fraction of the image height the section's in-plane extent may fill.
SECTION_FILL = 0.8
MESH_NAME = "mesh.stl"
_FOREGROUND_THRESHOLD = 30
#: View/section names become ``view-<name>.png`` / ``section-<name>.png``: one
#: path component, starting alphanumeric, so no separator, ``..`` or dotfile.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _is_safe_name(name: object) -> bool:
    return isinstance(name, str) and _SAFE_NAME.fullmatch(name) is not None


@dataclass(frozen=True)
class ViewSpec:
    name: str
    rotation_deg: tuple[float, float, float]


#: The fixed view set, as OpenSCAD gimbal camera rotations (x, y, z).
VIEW_SET: tuple[ViewSpec, ...] = (
    ViewSpec("iso", (55.0, 0.0, 25.0)),
    ViewSpec("front", (90.0, 0.0, 0.0)),
    ViewSpec("back", (90.0, 0.0, 180.0)),
    ViewSpec("left", (90.0, 0.0, 270.0)),
    ViewSpec("right", (90.0, 0.0, 90.0)),
    ViewSpec("top", (0.0, 0.0, 0.0)),
    ViewSpec("bottom", (180.0, 0.0, 0.0)),
)


@dataclass(frozen=True)
class SectionSpec:
    """A cut plane through ``origin`` with unit ``normal``."""

    name: str
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]


def axis_section(axis: str, offset_mm: float, name: str | None = None) -> SectionSpec:
    """An axis-aligned plane, e.g. ``axis_section("z", 12.0)``."""
    units = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
    if axis not in units:
        raise ValueError(f"axis must be one of x, y, z (got {axis!r})")
    unit = units[axis]
    origin = tuple(float(offset_mm) * c for c in unit)
    return SectionSpec(name or f"section-{axis}{offset_mm:g}", origin, unit)


@dataclass(frozen=True)
class RenderedImage:
    name: str
    kind: str  # "view" or "section"
    file: str
    bytes: int
    sha256: str
    camera: str
    px_per_mm: float | None = None
    cut_pixels: int | None = None
    cut_area_mm2: float | None = None


@dataclass(frozen=True)
class RenderViewResult:
    ok: bool
    images: tuple[RenderedImage, ...] = ()
    error: str | None = None
    stl_file: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _plane_transform(section: SectionSpec) -> np.ndarray:
    """Rigid 4x4 transform taking the section plane onto z=0 with normal +Z."""
    normal = np.asarray(section.normal, dtype=float)
    length = float(np.linalg.norm(normal))
    if normal.shape != (3,) or length == 0.0:
        raise ValueError(f"section {section.name!r}: normal must be a non-zero 3-vector")
    normal /= length
    align = trimesh.geometry.align_vectors(normal, [0.0, 0.0, 1.0])
    shift = trimesh.transformations.translation_matrix(-np.asarray(section.origin, dtype=float))
    return align @ shift


def _px_per_mm(distance_mm: float, image_height_px: int) -> float:
    visible_mm = distance_mm * 2.0 * math.tan(math.radians(OPENSCAD_FOV_DEG / 2.0))
    return image_height_px / visible_mm


def _section_camera(mesh_bounds: np.ndarray, transform: np.ndarray,
                    image_size: tuple[int, int]) -> tuple[str, float]:
    """Top-down ortho camera centred on the plane-local bbox of the mesh."""
    corners = trimesh.bounds.corners(mesh_bounds)
    local = trimesh.transform_points(corners, transform)
    lo, hi = local.min(axis=0), local.max(axis=0)
    centre = (lo + hi) / 2.0
    width, height = image_size
    extent = max(float(hi[0] - lo[0]) * height / width, float(hi[1] - lo[1]), 1e-3)
    distance = extent / (SECTION_FILL * 2.0 * math.tan(math.radians(OPENSCAD_FOV_DEG / 2.0)))
    camera = f"{centre[0]:.6f},{centre[1]:.6f},0,0,0,0,{distance:.6f}"
    return camera, _px_per_mm(distance, height)


def _foreground_pixels(png_path: Path) -> int:
    from PIL import Image

    with Image.open(png_path) as image:
        pixels = np.asarray(image.convert("RGB"), dtype=int)
    background = pixels[0, 0]
    return int(np.count_nonzero(np.abs(pixels - background).sum(axis=2) > _FOREGROUND_THRESHOLD))


def _matrix_literal(matrix: np.ndarray) -> str:
    rows = ", ".join("[" + ", ".join(f"{v:.12g}" for v in row) + "]" for row in matrix)
    return f"[{rows}]"


def _render_one(work_dir: Path, out_dir: Path, scad_name: str, png_name: str,
                camera_args: Sequence[str], image_size: tuple[int, int]) -> Path:
    png_path = (out_dir / png_name).resolve()
    if png_path.parent != Path(out_dir).resolve():
        raise ValueError(f"{png_name!r}: image path resolves outside the output directory")
    if png_path.exists():
        png_path.unlink()
    cmd = scad_sandbox.build_command(
        work_dir=work_dir,
        out_dir=out_dir,
        openscad_args=[
            "-o", f"{scad_sandbox.OUT_DIR}/{png_name}",
            *camera_args,
            "--projection=ortho",
            f"--colorscheme={COLORSCHEME}",
            f"--imgsize={image_size[0]},{image_size[1]}",
            f"{scad_sandbox.WORK_DIR}/{scad_name}",
        ],
        with_xvfb=True,
    )
    proc = scad_sandbox._run_sandboxed(cmd, timeout=scad_sandbox._timeout_s(), stage=png_name)
    if proc.returncode != 0 or not png_path.is_file():
        detail = (proc.stderr or proc.stdout).strip()[-600:]
        raise render.CompileError(f"{png_name}: render failed (rc={proc.returncode}): {detail}")
    if png_path.stat().st_size == 0:
        raise render.CompileError(f"{png_name}: render produced a 0-byte image")
    scad_sandbox._check_output_cap(out_dir, scad_sandbox._output_cap_bytes(), png_name)
    return png_path


def _image(name: str, kind: str, path: Path, camera: str, **extra: Any) -> RenderedImage:
    payload = path.read_bytes()
    return RenderedImage(name, kind, path.name, len(payload), hashlib.sha256(payload).hexdigest(),
                         camera, **extra)


def render_mesh_views(
    stl_path: str | Path,
    out_dir: str | Path,
    *,
    views: Sequence[ViewSpec] = VIEW_SET,
    sections: Sequence[SectionSpec] = (),
    image_size: tuple[int, int] = IMAGE_SIZE,
) -> RenderViewResult:
    """Render an already-compiled mesh: ``views`` then ``sections``, in order.

    Writes ``view-<name>.png`` and ``section-<name>.png`` into ``out_dir``.
    Raises :class:`SandboxUnavailable` (no process started) when the sandbox
    cannot run.
    """
    if not scad_sandbox.sandbox_available():
        raise SandboxUnavailable(
            "render sandbox unavailable: install bubblewrap, openscad and xvfb, "
            "and enable unprivileged user namespaces"
        )
    # Names become output file names: validate before any filesystem operation.
    names = [v.name for v in views] + [s.name for s in sections]
    unsafe = [name for name in names if not _is_safe_name(name)]
    if unsafe:
        return RenderViewResult(False, error=f"unsafe view/section name(s): {unsafe!r}")
    if len(names) != len(set(names)):
        return RenderViewResult(False, error="view and section names must be unique")
    stl_path = Path(stl_path).resolve(strict=True)
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        mesh = trimesh.load(stl_path.as_posix(), force="mesh")
    except Exception as exc:  # noqa: BLE001 - any parse failure is a candidate defect
        return RenderViewResult(False, error=f"{stl_path.name}: unreadable mesh ({exc})")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        return RenderViewResult(False, error=f"{stl_path.name}: empty mesh")

    images: list[RenderedImage] = []
    with tempfile.TemporaryDirectory(prefix="makerbench-render-view-") as tmp:
        work_dir = Path(tmp) / "work"
        work_dir.mkdir()
        # Only the compiled mesh and generated wrappers cross into the sandbox.
        shutil.copyfile(stl_path, work_dir / MESH_NAME)
        (work_dir / "view.scad").write_text(f'import("{MESH_NAME}");\n', encoding="utf-8")
        try:
            for view in views:
                rx, ry, rz = view.rotation_deg
                camera = f"0,0,0,{rx:g},{ry:g},{rz:g},0"
                png = _render_one(work_dir, out_dir, "view.scad", f"view-{view.name}.png",
                                  [f"--camera={camera}", "--viewall", "--autocenter"],
                                  image_size)
                images.append(_image(view.name, "view", png, camera))
            for index, section in enumerate(sections):
                transform = _plane_transform(section)
                camera, px_per_mm = _section_camera(mesh.bounds, transform, image_size)
                scad_name = f"section-{index}.scad"
                (work_dir / scad_name).write_text(
                    f"projection(cut=true) multmatrix({_matrix_literal(transform)}) "
                    f'import("{MESH_NAME}");\n',
                    encoding="utf-8",
                )
                png = _render_one(work_dir, out_dir, scad_name, f"section-{section.name}.png",
                                  [f"--camera={camera}"], image_size)
                pixels = _foreground_pixels(png)
                images.append(_image(section.name, "section", png, camera,
                                     px_per_mm=px_per_mm, cut_pixels=pixels,
                                     cut_area_mm2=pixels / (px_per_mm * px_per_mm)))
        except (render.CompileError, ValueError) as exc:
            return RenderViewResult(False, tuple(images), str(exc), stl_path.name)
    return RenderViewResult(True, tuple(images), None, stl_path.name)


def render_view(
    source_path: str | Path,
    out_dir: str | Path,
    *,
    backend: str,
    views: Sequence[ViewSpec] = VIEW_SET,
    sections: Sequence[SectionSpec] = (),
    image_size: tuple[int, int] = IMAGE_SIZE,
) -> RenderViewResult:
    """Compile ``source_path`` in its sandbox, then render views and sections.

    ``backend`` is ``"openscad"`` or ``"cadquery"``. Compile artifacts land in
    ``out_dir/compile``; images in ``out_dir``.
    """
    out_dir = Path(out_dir).resolve()
    compile_dir = out_dir / "compile"
    if backend == "openscad":
        compiler = scad_sandbox.compile_scad_sandboxed
    elif backend == "cadquery":
        from .cadquery_backend import compile_cadquery_to_artifacts as compiler
    else:
        raise ValueError(f"backend must be 'openscad' or 'cadquery' (got {backend!r})")
    try:
        artifacts = compiler(Path(source_path), compile_dir)
    except render.CompileError as exc:
        return RenderViewResult(False, error=f"compile failed: {exc}")
    result = render_mesh_views(artifacts.stl_path, out_dir, views=views, sections=sections,
                               image_size=image_size)
    return RenderViewResult(result.ok, result.images, result.error,
                            f"compile/{Path(artifacts.stl_path).name}",
                            tuple(artifacts.warnings))
