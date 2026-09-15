"""Parametric edit harness for the design workbench (#788 W1b).

Take a CAD master, change **one declared literal parameter** through
:mod:`makerbench.cad_params` (W1), recompile the edited source **inside the
W0 sandbox**, and report what the edit measurably did to the mesh: bounding
box, extents, volume and body count, as ratios against the unedited baseline.

Nothing here runs a candidate on the host. The default compiler is
``compiler_for_backend(backend, sandboxed=True)``; a sandbox that cannot start
raises :class:`makerbench.scad_sandbox.SandboxUnavailable` (an environment
error) and no compile happens. A candidate that fails to compile after the
edit is a *result* (``effect == "edited_failed"``), not an exception.

The harness never writes into an instrument repository: every compile lands
in the caller's ``work_dir``.

Honest effects, one per probe:

``changed``          both compiles succeeded and the mesh measurably changed
``unchanged``        both succeeded and no measurement moved beyond tolerance
``edited_failed``    the baseline compiled, the edited source did not
``baseline_failed``  the master itself does not compile here
``skipped``          the parameter has no numeric edit (strings, options)
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import render
from .cad_params import Parameter, ParameterError, apply_parameters, extract_parameters
from .code_cad_arena_runner import compiler_for_backend
from .code_cad_objective import RenderArtifacts

__all__ = [
    "Expectation",
    "MeshMeasure",
    "CompileOutcome",
    "ProbeResult",
    "measure_mesh",
    "compile_and_measure",
    "default_edit",
    "probe_parameter",
    "probe_master",
    "sandboxed_compiler",
]

Compiler = Callable[[Path, Path], RenderArtifacts]

SOURCE_NAMES = {"openscad": "input.scad", "cadquery": "input.py"}
#: Relative change below which a measurement counts as "did not move".
DEFAULT_TOLERANCE = 1e-3


def sandboxed_compiler(backend: str) -> Compiler:
    """The confined compiler for ``backend``; never a host compiler."""

    return compiler_for_backend(backend, sandboxed=True)


@dataclass(frozen=True)
class MeshMeasure:
    """Public, deterministic measurements of one compiled mesh."""

    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    size: tuple[float, float, float]
    volume_mm3: float
    faces: int
    bodies: int
    watertight: bool

    def to_dict(self) -> dict:
        return asdict(self)


def measure_mesh(stl_path: Path) -> MeshMeasure:
    """Bounding box, extents, volume, face count, body count and watertightness."""

    import trimesh

    loaded = trimesh.load(Path(stl_path).as_posix(), force="mesh")
    if not isinstance(loaded, trimesh.Trimesh) or loaded.faces.shape[0] == 0:
        raise render.CompileError("mesh has no faces")
    bounds = loaded.bounds
    try:
        bodies = len(loaded.split(only_watertight=False)) or 1
    except Exception:  # noqa: BLE001 - splitting needs scipy/networkx; 1 is the honest floor
        bodies = 1
    lo = tuple(float(v) for v in bounds[0])
    hi = tuple(float(v) for v in bounds[1])
    return MeshMeasure(
        bbox_min=lo,  # type: ignore[arg-type]
        bbox_max=hi,  # type: ignore[arg-type]
        size=tuple(float(h - low) for h, low in zip(hi, lo)),  # type: ignore[arg-type]
        volume_mm3=float(abs(loaded.volume)),
        faces=int(loaded.faces.shape[0]),
        bodies=int(bodies),
        watertight=bool(loaded.is_watertight),
    )


@dataclass
class CompileOutcome:
    ok: bool
    duration_s: float
    error: Optional[str] = None
    warnings: tuple[str, ...] = ()
    measure: Optional[MeshMeasure] = None
    stl_path: Optional[str] = None
    png_path: Optional[str] = None

    def to_dict(self) -> dict:
        out = asdict(self)
        out["measure"] = self.measure.to_dict() if self.measure else None
        return out


def compile_and_measure(
    source: str,
    *,
    backend: str,
    work_dir: Path,
    compiler: Optional[Compiler] = None,
) -> CompileOutcome:
    """Write ``source`` under ``work_dir``, compile it with the sandboxed
    compiler (or ``compiler``), and measure the mesh. A compile failure is a
    ``CompileOutcome(ok=False)``; a sandbox that cannot start propagates."""

    if backend not in SOURCE_NAMES:
        raise ValueError(f"unknown backend {backend!r}; choose one of {sorted(SOURCE_NAMES)}")
    compiler = compiler or sandboxed_compiler(backend)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    source_path = work_dir / SOURCE_NAMES[backend]
    source_path.write_text(source, encoding="utf-8")
    out_dir = work_dir / "artifacts"
    started = time.monotonic()
    try:
        artifacts = compiler(source_path, out_dir)
        measure = measure_mesh(artifacts.stl_path)
    except render.CompileError as exc:
        return CompileOutcome(ok=False, duration_s=time.monotonic() - started, error=str(exc))
    return CompileOutcome(
        ok=True,
        duration_s=time.monotonic() - started,
        warnings=tuple(artifacts.warnings),
        measure=measure,
        stl_path=artifacts.stl_path.name,
        png_path=artifacts.png_path.name,
    )


@dataclass(frozen=True)
class Expectation:
    """What the edit should do to the mesh. ``axis`` is ``x``/``y``/``z`` for
    an extent, ``volume`` for the volume, or ``any`` for "something moved".
    ``ratio`` is the expected edited/baseline ratio (``None`` = just changed)."""

    axis: str = "any"
    ratio: Optional[float] = None
    tolerance: float = 0.02

    def check(self, delta: Optional[Mapping[str, Any]]) -> Optional[bool]:
        if delta is None:
            return None
        if self.axis == "any":
            return bool(delta["changed"])
        if self.axis == "volume":
            observed = delta["volume_ratio"]
        elif self.axis in "xyz" and len(self.axis) == 1:
            observed = delta["size_ratio"]["xyz".index(self.axis)]
        else:
            raise ValueError("Expectation.axis must be x, y, z, volume or any")
        if observed is None:
            return False
        if self.ratio is None:
            return abs(observed - 1.0) > self.tolerance
        return math.isclose(observed, self.ratio, rel_tol=self.tolerance, abs_tol=self.tolerance)


def _ratio(after: float, before: float) -> Optional[float]:
    if before == 0:
        return None if after == 0 else math.inf
    return after / before


def _delta(baseline: MeshMeasure, edited: MeshMeasure, tolerance: float) -> dict:
    size_delta = tuple(e - b for e, b in zip(edited.size, baseline.size))
    size_ratio = tuple(_ratio(e, b) for e, b in zip(edited.size, baseline.size))
    volume_ratio = _ratio(edited.volume_mm3, baseline.volume_mm3)

    def moved(ratio: Optional[float]) -> bool:
        return ratio is None or math.isinf(ratio) or abs(ratio - 1.0) > tolerance

    bbox_moved = any(
        abs(e - b) > tolerance * max(1.0, abs(b))
        for e, b in zip(edited.bbox_min + edited.bbox_max, baseline.bbox_min + baseline.bbox_max)
    )
    changed = (
        any(moved(r) for r in size_ratio)
        or moved(volume_ratio)
        or bbox_moved
        or edited.bodies != baseline.bodies
    )
    return {
        "size_delta": size_delta,
        "size_ratio": size_ratio,
        "volume_delta_mm3": edited.volume_mm3 - baseline.volume_mm3,
        "volume_ratio": volume_ratio,
        "bodies_delta": edited.bodies - baseline.bodies,
        "faces_delta": edited.faces - baseline.faces,
        "changed": changed,
    }


@dataclass
class ProbeResult:
    name: str
    backend: str
    old_value: Any
    new_value: Any
    effect: str
    baseline: Optional[CompileOutcome] = None
    edited: Optional[CompileOutcome] = None
    delta: Optional[dict] = None
    expectation: Optional[Expectation] = None
    expectation_met: Optional[bool] = None
    reason: Optional[str] = None
    unit: Optional[str] = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "backend": self.backend,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "unit": self.unit,
            "effect": self.effect,
            "reason": self.reason,
            "notes": list(self.notes),
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "edited": self.edited.to_dict() if self.edited else None,
            "delta": self.delta,
            "expectation": asdict(self.expectation) if self.expectation else None,
            "expectation_met": self.expectation_met,
        }


def default_edit(param: Parameter, *, scale: float = 1.25) -> tuple[Optional[Any], Optional[str]]:
    """A geometric edit for ``param``: numbers and numeric vectors are scaled
    (a zero becomes ``1``), booleans flip. Strings have no numeric edit and
    return ``(None, reason)``. Options and declared ranges are respected."""

    if not param.editable:
        return None, f"{param.state}: not editable"
    if param.options:
        others = [o for o in param.options if o != param.value]
        return (others[0], None) if others else (None, "single option")
    if param.kind == "bool":
        return (not param.value), None
    if param.kind == "number":
        value = param.value
        is_int = isinstance(value, int) and not isinstance(value, bool)
        new: float = value * scale if value != 0 else 1.0
        if is_int and not param.range:
            # keep integers integral; a rounding that lands on the old value
            # steps by one so the edit is never a no-op
            new = int(round(new))
            if new == value:
                new = value + (1 if scale >= 1 else -1)
        if param.range:
            lo, hi = param.range.get("min"), param.range.get("max")
            if hi is not None and new > hi:
                new = hi
            if lo is not None and new < lo:
                new = lo
            if new == value:
                # already at the bound the scale pushes towards; use the other one
                other = lo if scale >= 1 else hi
                new = other if other is not None and other != value else value
            if new == value:
                return None, "range leaves no other value"
        return new, None
    if param.kind == "vector":
        return [v * scale if v != 0 else 1.0 for v in param.value], None
    return None, f"{param.kind} parameter has no numeric edit"


def probe_parameter(
    source: str,
    name: str,
    new_value: Any,
    *,
    backend: str = "openscad",
    work_dir: Path,
    compiler: Optional[Compiler] = None,
    expect: Optional[Expectation] = None,
    baseline: Optional[CompileOutcome] = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ProbeResult:
    """Change one declared literal parameter and measure the effect.

    ``baseline`` lets a caller reuse one compile of the unedited master
    across many probes. Raises :class:`ParameterError` for an unknown,
    derived, reassigned or invalid edit (nothing is compiled then).
    """

    model = extract_parameters(source, backend)
    param = model.by_name().get(name)
    if param is None:
        raise ParameterError(f"unknown parameter {name!r}")
    if not param.editable:
        raise ParameterError(f"parameter {name!r} is {param.state}: {'; '.join(param.notes)}")
    edited_source = apply_parameters(source, {name: new_value}, backend)
    work_dir = Path(work_dir)
    if baseline is None:
        baseline = compile_and_measure(source, backend=backend, work_dir=work_dir / "baseline", compiler=compiler)
    result = ProbeResult(
        name=name,
        backend=backend,
        old_value=param.value,
        new_value=new_value,
        effect="baseline_failed",
        baseline=baseline,
        expectation=expect,
        unit=param.unit,
        notes=tuple(param.notes),
    )
    if not baseline.ok:
        result.reason = baseline.error
        return result
    if edited_source == source:
        result.effect = "unchanged"
        result.edited = baseline
        result.reason = "the new value equals the current one; nothing to compile"
        result.delta = _delta(baseline.measure, baseline.measure, tolerance)
        result.expectation_met = expect.check(result.delta) if expect else None
        return result
    edited = compile_and_measure(
        edited_source, backend=backend, work_dir=work_dir / f"edited-{name}", compiler=compiler
    )
    result.edited = edited
    if not edited.ok:
        result.effect = "edited_failed"
        result.reason = edited.error
        result.expectation_met = False if expect else None
        return result
    assert baseline.measure is not None and edited.measure is not None
    result.delta = _delta(baseline.measure, edited.measure, tolerance)
    result.effect = "changed" if result.delta["changed"] else "unchanged"
    if result.effect == "unchanged":
        result.reason = "the mesh did not move: this parameter has no geometric effect at this value"
    result.expectation_met = expect.check(result.delta) if expect else None
    return result


def probe_master(
    source: str,
    *,
    backend: str = "openscad",
    work_dir: Path,
    names: Optional[Iterable[str]] = None,
    scale: float = 1.25,
    max_params: Optional[int] = None,
    compiler: Optional[Compiler] = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[ProbeResult]:
    """Probe every editable parameter of a master (or the ``names`` given),
    compiling the baseline once. Parameters without a numeric edit are
    reported as ``skipped`` with the reason, never silently dropped."""

    model = extract_parameters(source, backend)
    wanted = list(names) if names is not None else [p.name for p in model.parameters]
    by_name = model.by_name()
    work_dir = Path(work_dir)
    baseline = compile_and_measure(source, backend=backend, work_dir=work_dir / "baseline", compiler=compiler)
    results: list[ProbeResult] = []
    probed = 0
    for name in wanted:
        param = by_name.get(name)
        if param is None:
            results.append(ProbeResult(name=name, backend=backend, old_value=None, new_value=None,
                                       effect="skipped", reason="unknown parameter"))
            continue
        if max_params is not None and probed >= max_params:
            results.append(ProbeResult(name=name, backend=backend, old_value=param.value, new_value=None,
                                       effect="skipped", reason=f"over --max-params {max_params}", unit=param.unit))
            continue
        new_value, reason = default_edit(param, scale=scale)
        if new_value is None:
            results.append(ProbeResult(name=name, backend=backend, old_value=param.value, new_value=None,
                                       effect="skipped", reason=reason, unit=param.unit, notes=tuple(param.notes)))
            continue
        probed += 1
        try:
            results.append(
                probe_parameter(
                    source, name, new_value, backend=backend, work_dir=work_dir,
                    compiler=compiler, baseline=baseline, tolerance=tolerance,
                )
            )
        except ParameterError as exc:
            results.append(ProbeResult(name=name, backend=backend, old_value=param.value, new_value=new_value,
                                       effect="skipped", reason=str(exc), unit=param.unit))
    return results


def report_rows(results: Sequence[ProbeResult]) -> list[dict]:
    """Flat rows for a table or JSON report; no host paths."""

    rows = []
    for r in results:
        size_ratio = r.delta["size_ratio"] if r.delta else None
        rows.append(
            {
                "name": r.name,
                "unit": r.unit,
                "old": r.old_value,
                "new": r.new_value,
                "effect": r.effect,
                "baseline_ok": bool(r.baseline.ok) if r.baseline else None,
                "edited_ok": bool(r.edited.ok) if r.edited else None,
                "size_ratio": [None if v is None else round(v, 4) for v in size_ratio] if size_ratio else None,
                "volume_ratio": None if not r.delta or r.delta["volume_ratio"] is None else round(r.delta["volume_ratio"], 4),
                "edited_s": round(r.edited.duration_s, 2) if r.edited else None,
                "reason": r.reason,
            }
        )
    return rows


def dump_report(path: Path, payload: Mapping[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
