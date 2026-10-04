"""Advisory acoustic check for one instrument family: the end-blown open pipe (#800, R-7).

**Advisory only.** The result is labelled ``advisory`` and never changes a
sub-score, the objective pass rate, pass/fail or Elo. It gives a design
workbench and a human reviewer a first physical sanity check: does the compiled
bore plausibly speak near the spec's target pitch?

Modelled family
    ``task_kind == "single_part_pipe"`` with a bore declared open at both ends
    and a target pitch stated in Hz (for example the kena, "G4 (~392 Hz)").
    Every other spec reports ``not modelled``: vessels, closed pipes, strings,
    percussion, and specs with no Hz target.

Method (open-open cylindrical pipe, all finger holes assumed closed)
    1. The pipe axis is the longest bounding-box extent, and the bore length
       ``L`` is that extent. Measuring along it makes the check independent of
       how the part is oriented.
    2. The bore radius ``r`` is measured on 9 cross-sections perpendicular to the
       axis, between 10 % and 90 % of the length. At each station the largest
       interior loop gives ``r = sqrt(area / pi)``. Stations that land on a side
       hole, where the ring opens, show no interior loop and are skipped. At
       least 3 stations must see the bore, and ``r`` is their median.
    3. ``f = c / (2 * (L + 2 * a * r))``, with the end-correction coefficient
       ``a = 0.6`` and ``c = 331.3 * sqrt(1 + T / 273.15)`` m/s at ``T = 20 °C``.
       This is the same formula as
       :func:`makerbench.instrument_acoustics_ladder.bore_resonance_check`.

Error bars
    The band spans ``a`` from 0.6 (unflanged) to 0.85 (flanged), ``T`` from 15 to
    25 °C, and the measured bore radii from minimum to maximum. The status is
    ``consistent`` when the target lies inside the band widened by
    ``TOLERANCE_CENTS``, otherwise ``inconsistent``.

Not modelled
    Embouchure and edge-tone effects, wall compliance, bore taper, open tone
    holes and humidity. The estimate is a first-order screen, not a tuning
    prediction.

:func:`helmholtz_hz` (vessel flutes) is wired by #980; see "Vessel flutes" below.

Vessel flutes (#980)
    ``task_kind == "single_part_vessel"`` whose constraints declare
    ``acoustic_model: helmholtz_resonator``, a target pitch in Hz and a voicing
    window (``voicing_window_mm: [w, h]`` or ``voicing_window_area_mm2``), for
    example the ocarina. The enclosed cavity volume ``V`` is measured from the
    mesh: the surface is voxelized, every opening narrower than
    ``2 * CLOSING_RADIUS_MM`` (window, finger holes, windway) is closed
    morphologically, and the region the closed shell encloses is the cavity.
    ``f = c / (2 pi) * sqrt(A / (V * L_eff))`` with ``A`` the declared window
    area, ``L_eff = t + 2 a r_eq`` (``t`` the declared wall, ``r_eq`` the
    window's equal-area radius) and all finger holes closed. The band spans
    ``a`` 0.6-0.85, ``T`` 15-25 °C, the voxel uncertainty of ``V`` (half a voxel
    over the cavity surface) and ``AREA_SPREAD`` on ``A``. The measured volume is
    also compared with a declared ``chamber_volume_cm3`` (``VOLUME_TOLERANCE``),
    and the result reports what the spec's *declared* volume and window predict,
    so a target that the declared geometry itself cannot reach reads as a spec
    issue, not a candidate defect.

Bore continuity and taper (#980)
    Pipes (the open-pipe family above) and any spec declaring a bore diameter
    (``constraints.bore_id_mm``, for example the duduk study body, measured on
    its bore body) get a bore profile on ``BORE_STATIONS`` cross-sections
    from 5 % to 95 % of the axis. Each station is ``bore`` (an interior loop,
    radius from its area), ``side_hole`` (the ring is cut open by a tone hole:
    not a fault), ``blocked`` (a solid section, no air path) or ``missing`` (no
    material: the body is broken). Blocked and missing stations, and radius
    steps larger than ``max(STEP_MIN_MM, STEP_REL * r)`` between neighbouring
    bore stations, break continuity. The taper is the least-squares slope of
    radius against axial position; a bore declared cylindrical must change by
    at most ``max(TAPER_MIN_MM, TAPER_REL * r)`` over its length, and a declared
    ``bore_id_mm`` must match the median diameter within ``BORE_ID_TOLERANCE``.
    Probe rays through the bore between stations, and out of both ends unless an end is
    declared closed or stopped, catch plugs and end caps that fall between stations
    (``obstruction`` / ``closed_end``). For an assembly, every collinear tube piece is
    profiled (the "bore body"), axial gaps between pieces fail continuity, and a declared
    ``body_length_mm`` must match the bore body's extent.

Every advisory failure carries a #903-shaped explanation (``check``,
``measured``, ``threshold``, ``unit``, ``requires``, ``body_id``, ``detail``)
under ``failures``. None of it changes a sub-score or a pass rate.

Known limitations of the bore checks (#994 review, advisory)
    * The end-lip guard compares the bore just inside an end with the trend of the two
      outermost stations. On a strongly nonlinear taper that trend can fall to the lip's own
      radius, so a lip there is taken for the taper and the end probes pass through it.
    * An asymmetric ridge that sits exactly on a station, or in the last millimetre of an end,
      shifts that section's fitted centroid and equivalent radius, so the probes move off it.
      Its radius change is under the step tolerance, so nothing reports it. Between stations
      the probes catch it.
    * The step scan samples the bore every ``STEP_SCAN_MM`` (coarser on long bodies), so a
      feature narrower than the spacing can fall between samples.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

import numpy as np
import trimesh

from . import measure

LABEL = "advisory"
NOT_MODELLED = "not modelled"
FAMILY = "end_blown_open_pipe"
NOMINAL_TEMP_C = 20.0
TEMP_BAND_C = (15.0, 25.0)
NOMINAL_END_COEFF = 0.6
END_COEFF_BAND = (0.6, 0.85)
TOLERANCE_CENTS = 50.0
STATIONS = tuple(0.1 + 0.1 * i for i in range(9))
MIN_BORE_STATIONS = 3

_HZ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*Hz", re.IGNORECASE)


def speed_of_sound_ms(temp_c: float) -> float:
    return 331.3 * math.sqrt(1.0 + temp_c / 273.15)


def open_pipe_hz(length_mm: float, radius_mm: float, *, temp_c: float = NOMINAL_TEMP_C,
                 end_coeff: float = NOMINAL_END_COEFF) -> float:
    """Fundamental of an open-open cylindrical pipe with an end correction per end."""
    effective_m = (length_mm + 2.0 * end_coeff * radius_mm) / 1000.0
    return speed_of_sound_ms(temp_c) / (2.0 * effective_m)


def helmholtz_hz(cavity_volume_mm3: float, opening_radius_mm: float, neck_length_mm: float, *,
                 temp_c: float = NOMINAL_TEMP_C, end_coeff: float = 0.85) -> float:
    """Helmholtz resonance ``f = c/(2 pi) * sqrt(A / (V * L_eff))``, ``L_eff = L + 2 a r``."""
    area_m2 = math.pi * (opening_radius_mm / 1000.0) ** 2
    volume_m3 = cavity_volume_mm3 / 1e9
    effective_m = (neck_length_mm + 2.0 * end_coeff * opening_radius_mm) / 1000.0
    return speed_of_sound_ms(temp_c) / (2.0 * math.pi) * math.sqrt(area_m2 / (volume_m3 * effective_m))


def cents(f: float, reference: float) -> float:
    return 1200.0 * math.log2(f / reference)


def parse_target_hz(constraints: Mapping[str, Any]) -> float | None:
    for key in ("fundamental", "target_note", "target_fundamental_hz"):
        value = constraints.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
        if isinstance(value, str):
            match = _HZ_RE.search(value)
            if match:
                return float(match.group(1))
    return None


def modelled_reason(spec: Mapping[str, Any]) -> str | None:
    """``None`` when the spec is in the modelled family, else why it is not."""
    if spec.get("task_kind") != "single_part_pipe":
        return f"task_kind {spec.get('task_kind')!r} is outside the open-pipe model"
    constraints = spec.get("constraints") or {}
    bore = str(constraints.get("bore") or "").lower()
    if "open" not in bore or "both ends" not in bore:
        return "bore is not declared open at both ends"
    if parse_target_hz(constraints) is None:
        return "no target pitch in Hz is declared"
    return None


def measure_open_pipe(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """Bore length and radius of a pipe mesh, or ``{"ok": False, "error": ...}``."""
    problem = measure.mesh_problem(mesh)
    if problem:
        return {"ok": False, "error": problem}
    lo, hi = mesh.bounds
    extents = hi - lo
    axis = int(np.argmax(extents))
    normal = np.zeros(3)
    normal[axis] = 1.0
    radii = []
    for fraction in STATIONS:
        origin = (lo + hi) / 2.0
        origin[axis] = lo[axis] + fraction * extents[axis]
        section = mesh.section(plane_origin=origin, plane_normal=normal)
        if section is None:
            continue
        path_2d, _ = section.to_2D()
        holes = [ring for polygon in path_2d.polygons_full for ring in polygon.interiors]
        if not holes:
            continue
        from shapely.geometry import Polygon

        largest = max(abs(Polygon(ring).area) for ring in holes)
        radii.append(math.sqrt(largest / math.pi))
    if len(radii) < MIN_BORE_STATIONS:
        return {"ok": False,
                "error": f"no through bore found ({len(radii)} of {len(STATIONS)} stations)"}
    return {"ok": True, "length_mm": float(extents[axis]), "axis": "xyz"[axis],
            "radius_mm": float(np.median(radii)), "radius_range_mm": [min(radii), max(radii)],
            "stations_with_bore": len(radii)}


def advise(spec: Mapping[str, Any], mesh: trimesh.Trimesh) -> dict[str, Any]:
    """The labelled advisory for one compiled candidate. Never affects scoring."""
    base: dict[str, Any] = {"label": LABEL, "affects_scoring": False}
    reason = modelled_reason(spec)
    if reason:
        if vessel_reason(spec) is None:
            return advise_vessel(spec, mesh)
        if bore_reason(spec) is None:
            return advise_bore_only(spec, mesh)
        return {**base, "status": NOT_MODELLED, "reason": reason}
    result = _advise_open_pipe(spec, mesh)
    if result.get("status") in ("consistent", "inconsistent"):
        result = _with_bore(result, spec, mesh, body_id="body")
    return result


def _advise_open_pipe(spec: Mapping[str, Any], mesh: trimesh.Trimesh) -> dict[str, Any]:
    base: dict[str, Any] = {"label": LABEL, "affects_scoring": False}
    target = parse_target_hz(spec.get("constraints") or {})
    geometry = measure_open_pipe(mesh)
    if not geometry["ok"]:
        return {**base, "status": "not measurable", "family": FAMILY, "target_hz": target,
                "error": geometry["error"]}
    length, radius = geometry["length_mm"], geometry["radius_mm"]
    r_min, r_max = geometry["radius_range_mm"]
    estimate = open_pipe_hz(length, radius)
    low = open_pipe_hz(length, r_max, temp_c=TEMP_BAND_C[0], end_coeff=END_COEFF_BAND[1])
    high = open_pipe_hz(length, r_min, temp_c=TEMP_BAND_C[1], end_coeff=END_COEFF_BAND[0])
    band_cents = [cents(low, target), cents(high, target)]
    consistent = band_cents[0] - TOLERANCE_CENTS <= 0.0 <= band_cents[1] + TOLERANCE_CENTS
    return {
        **base,
        "status": "consistent" if consistent else "inconsistent",
        "failures": [] if consistent else [_explain(
            "pipe_pitch", measured=round(estimate, 2), threshold=target, unit="Hz",
            requires=f"target inside the estimate band {[round(low, 1), round(high, 1)]} Hz "
                     f"widened by {TOLERANCE_CENTS:g} cents", body_id="body",
            detail=f"open-pipe estimate {estimate:.1f} Hz is {cents(estimate, target):+.0f} cents from the "
                   f"{target:g} Hz target (bore length {length:.1f} mm, radius {radius:.2f} mm)")],
        "family": FAMILY,
        "target_hz": target,
        "estimate_hz": round(estimate, 2),
        "band_hz": [round(low, 2), round(high, 2)],
        "error_cents": round(cents(estimate, target), 1),
        "band_cents": [round(c, 1) for c in band_cents],
        "tolerance_cents": TOLERANCE_CENTS,
        "measured": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in geometry.items()
                     if k != "ok"},
        "method": "open-open cylindrical pipe, f = c / (2 (L + 2 a r)); finger holes closed",
        "assumptions": [
            f"end-correction coefficient a in {list(END_COEFF_BAND)} (nominal {NOMINAL_END_COEFF})",
            f"air temperature in {list(TEMP_BAND_C)} C (nominal {NOMINAL_TEMP_C})",
            "bore length = longest bbox extent; radius = median largest interior loop over 9 stations",
            "not modelled: embouchure/edge tone, taper, wall compliance, open tone holes, humidity",
        ],
    }


# --- #980: vessel-flute Helmholtz estimate, bore continuity and taper --------------------------

VESSEL_FAMILY = "vessel_flute_helmholtz"
BORE_FAMILY = "pipe_bore"
CLOSING_RADIUS_MM = 6.0
VOXELS_ALONG_LONGEST = 120
VOXEL_PITCH_RANGE_MM = (0.75, 2.0)
AREA_SPREAD = 0.2
VOLUME_TOLERANCE = 0.15
DEFAULT_WALL_MM = 3.0
BORE_STATIONS = tuple(round(0.05 + 0.05 * i, 2) for i in range(19))
STEP_MIN_MM, STEP_REL = 1.0, 0.2
TAPER_MIN_MM, TAPER_REL = 0.5, 0.05
BORE_ID_TOLERANCE = 0.1


def _explain(check: str, *, measured, threshold, unit: str, requires: str, body_id: str | None,
             detail: str, **extra: Any) -> dict[str, Any]:
    """One explained advisory failure, in the #903 shape the gate uses for sub-scores."""
    return {"check": check, "measured": measured, "threshold": threshold, "unit": unit,
            "requires": requires, "body_id": body_id, "detail": detail, **extra}


def _positive(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def declared_window_area_mm2(constraints: Mapping[str, Any]) -> float | None:
    area = _positive(constraints.get("voicing_window_area_mm2"))
    if area:
        return area
    window = constraints.get("voicing_window_mm")
    if isinstance(window, (list, tuple)) and len(window) == 2 and all(_positive(v) for v in window):
        return float(window[0]) * float(window[1])
    return None


def vessel_reason(spec: Mapping[str, Any]) -> str | None:
    """``None`` when the spec is a modelled vessel flute, else why it is not."""
    if spec.get("task_kind") != "single_part_vessel":
        return f"task_kind {spec.get('task_kind')!r} is not a vessel"
    constraints = spec.get("constraints") or {}
    if str(constraints.get("acoustic_model") or "") != "helmholtz_resonator":
        return "vessel does not declare acoustic_model 'helmholtz_resonator'"
    if parse_target_hz(constraints) is None:
        return "no target pitch in Hz is declared"
    if declared_window_area_mm2(constraints) is None:
        return "no voicing window (voicing_window_mm or voicing_window_area_mm2) is declared"
    return None


def bore_reason(spec: Mapping[str, Any]) -> str | None:
    """``None`` when the spec declares a bore diameter to profile, else why not."""
    if _positive((spec.get("constraints") or {}).get("bore_id_mm")) is None:
        return "no bore_id_mm is declared"
    return None


def _voxel_pitch(mesh: trimesh.Trimesh) -> float:
    pitch = float(max(mesh.extents)) / VOXELS_ALONG_LONGEST
    return min(max(pitch, VOXEL_PITCH_RANGE_MM[0]), VOXEL_PITCH_RANGE_MM[1])


def measure_cavity(mesh: trimesh.Trimesh, *, closing_radius_mm: float = CLOSING_RADIUS_MM,
                   pitch_mm: float | None = None) -> dict[str, Any]:
    """Air volume a vessel encloses once its openings are closed, or ``{"ok": False, ...}``.

    Openings narrower than ``2 * closing_radius_mm`` are bridged by a morphological
    closing of the voxelized surface (Euclidean distance transforms, so the result is
    deterministic and fast); the cavity is what the closed shell encloses minus the shell.
    The uncertainty is half a voxel over the cavity's surface.
    """
    from scipy import ndimage

    problem = measure.mesh_problem(mesh)
    if problem:
        return {"ok": False, "error": problem}
    pitch = float(pitch_mm or _voxel_pitch(mesh))
    voxels = mesh.voxelized(pitch)
    surface = voxels.matrix.astype(bool)
    pad = int(math.ceil(closing_radius_mm / pitch)) + 2
    grid = np.pad(surface, pad)
    radius_vox = closing_radius_mm / pitch
    dilated = ndimage.distance_transform_edt(~grid) <= radius_vox
    closed = (ndimage.distance_transform_edt(dilated) > radius_vox) | grid
    filled = ndimage.binary_fill_holes(closed)
    cavity = filled & ~closed
    labels, count = ndimage.label(cavity)
    # An enclosed region is air only if its deepest voxel is outside the material: the
    # inside of a solid part (or of a wall too thick to close) is enclosed by its surface too.
    depth = ndimage.distance_transform_edt(cavity)
    sizes = np.bincount(labels.ravel())[1:] if count else np.array([], dtype=int)
    main = None
    air_regions = 0
    for label in np.argsort(-sizes, kind="stable") + 1:
        deepest = np.array(ndimage.maximum_position(depth, labels, int(label)))
        point = voxels.indices_to_points(np.array([deepest - pad]))
        if not bool(mesh.contains(point)[0]):
            air_regions += 1
            if main is None:
                main = labels == label
    if main is None:
        return {"ok": False, "error": "no enclosed cavity found after closing openings "
                                      f"up to {2 * closing_radius_mm:g} mm"}
    count = air_regions
    faces = sum(int(np.count_nonzero(main != np.roll(main, 1, axis=a))) for a in range(3))
    surface_mm2 = faces * pitch ** 2
    volume = float(main.sum()) * pitch ** 3 + 0.5 * pitch * surface_mm2
    spread = 0.5 * pitch * surface_mm2
    return {"ok": True, "volume_mm3": volume, "volume_range_mm3": [volume - spread, volume + spread],
            "cavity_surface_mm2": surface_mm2, "voxel_pitch_mm": pitch,
            "closing_radius_mm": closing_radius_mm, "cavities_found": int(count)}


def advise_vessel(spec: Mapping[str, Any], mesh: trimesh.Trimesh) -> dict[str, Any]:
    """Helmholtz advisory for a vessel flute (#980). Never affects scoring."""
    base: dict[str, Any] = {"label": LABEL, "affects_scoring": False, "family": VESSEL_FAMILY}
    constraints = spec.get("constraints") or {}
    target = parse_target_hz(constraints)
    area = declared_window_area_mm2(constraints)
    wall = _positive(constraints.get("wall_thickness_mm")) or _positive(constraints.get("wall_mm"))
    wall_assumed = wall is None
    wall = wall or DEFAULT_WALL_MM
    cavity = measure_cavity(mesh)
    if not cavity["ok"]:
        return {**base, "status": "not measurable", "target_hz": target, "error": cavity["error"],
                "failures": [_explain("vessel_cavity", measured=None, threshold=None, unit="mm^3",
                                      requires="an enclosed cavity", body_id="body",
                                      detail=cavity["error"])]}
    volume = cavity["volume_mm3"]
    v_lo, v_hi = cavity["volume_range_mm3"]
    r_eq = math.sqrt(area / math.pi)

    def hz(v, a, *, temp_c=NOMINAL_TEMP_C, end_coeff=0.85):
        return helmholtz_hz(v, math.sqrt(a / math.pi), wall, temp_c=temp_c, end_coeff=end_coeff)

    estimate = hz(volume, area)
    low = hz(v_hi, area * (1 - AREA_SPREAD), temp_c=TEMP_BAND_C[0], end_coeff=END_COEFF_BAND[1])
    high = hz(v_lo, area * (1 + AREA_SPREAD), temp_c=TEMP_BAND_C[1], end_coeff=END_COEFF_BAND[0])
    band_cents = [cents(low, target), cents(high, target)]
    pitch_ok = band_cents[0] - TOLERANCE_CENTS <= 0.0 <= band_cents[1] + TOLERANCE_CENTS
    failures = []
    if not pitch_ok:
        failures.append(_explain(
            "helmholtz_pitch", measured=round(estimate, 2), threshold=target, unit="Hz",
            requires=f"target inside the estimate band {[round(low, 1), round(high, 1)]} Hz widened by "
                     f"{TOLERANCE_CENTS:g} cents", body_id="body",
            detail=f"Helmholtz estimate {estimate:.1f} Hz is {cents(estimate, target):+.0f} cents from the "
                   f"{target:g} Hz target (cavity {volume / 1000:.1f} cm^3, window {area:g} mm^2, "
                   f"wall {wall:g} mm, finger holes closed)"))
    declared_cm3 = _positive(constraints.get("chamber_volume_cm3"))
    volume_check = None
    spec_estimate = None
    if declared_cm3:
        declared = declared_cm3 * 1000.0
        rel = volume / declared - 1.0
        volume_ok = (v_lo <= declared * (1 + VOLUME_TOLERANCE)) and (v_hi >= declared * (1 - VOLUME_TOLERANCE))
        volume_check = {"status": "consistent" if volume_ok else "inconsistent",
                        "declared_cm3": declared_cm3, "measured_cm3": round(volume / 1000.0, 2),
                        "relative_error": round(rel, 4), "tolerance": VOLUME_TOLERANCE}
        if not volume_ok:
            failures.append(_explain(
                "cavity_volume", measured=round(volume / 1000.0, 2), threshold=declared_cm3, unit="cm^3",
                requires=f"measured within {VOLUME_TOLERANCE:.0%} of the declared chamber volume", body_id="body",
                detail=f"enclosed cavity {volume / 1000:.1f} cm^3 ({rel:+.0%}) vs declared {declared_cm3:g} cm^3"))
        spec_estimate = hz(declared, area)
    notes = []
    if spec_estimate is not None and abs(cents(spec_estimate, target)) > TOLERANCE_CENTS:
        notes.append(f"the spec's own declared chamber volume and window predict {spec_estimate:.1f} Hz "
                     f"({cents(spec_estimate, target):+.0f} cents from the target): the target and the "
                     "declared geometry disagree, so a candidate built to the declared geometry reads "
                     "inconsistent for reasons outside its control")
    status = "inconsistent" if failures else "consistent"
    return {
        **base,
        "status": status,
        "target_hz": target,
        "estimate_hz": round(estimate, 2),
        "band_hz": [round(low, 2), round(high, 2)],
        "error_cents": round(cents(estimate, target), 1),
        "band_cents": [round(c, 1) for c in band_cents],
        "tolerance_cents": TOLERANCE_CENTS,
        "pitch_status": "consistent" if pitch_ok else "inconsistent",
        "volume_check": volume_check,
        "spec_estimate_hz": None if spec_estimate is None else round(spec_estimate, 2),
        "notes": notes,
        "failures": failures,
        "measured": {"cavity_volume_cm3": round(volume / 1000.0, 3),
                     "cavity_volume_range_cm3": [round(v_lo / 1000.0, 3), round(v_hi / 1000.0, 3)],
                     "voxel_pitch_mm": round(cavity["voxel_pitch_mm"], 3),
                     "closing_radius_mm": cavity["closing_radius_mm"],
                     "cavities_found": cavity["cavities_found"]},
        "declared": {"window_area_mm2": area, "window_equivalent_radius_mm": round(r_eq, 3),
                     "neck_length_mm": wall, "neck_length_assumed": wall_assumed},
        "method": "Helmholtz resonator, f = c/(2 pi) sqrt(A / (V (t + 2 a r_eq))); finger holes closed",
        "assumptions": [
            f"end-correction coefficient a in {list(END_COEFF_BAND)} (nominal 0.85)",
            f"air temperature in {list(TEMP_BAND_C)} C (nominal {NOMINAL_TEMP_C})",
            f"window area = declared area +/- {AREA_SPREAD:.0%}; neck = "
            + ("assumed " if wall_assumed else "declared ") + f"wall {wall:g} mm",
            f"cavity = voxel enclosure after closing openings up to {2 * CLOSING_RADIUS_MM:g} mm, "
            "+/- half a voxel over its surface",
            "not modelled: windway/edge tone, open finger holes, wall compliance, humidity",
        ],
    }


def _largest_body(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    bodies = [b for b in mesh.split(only_watertight=False) if len(b.faces)]
    if len(bodies) <= 1:
        return mesh
    return max(bodies, key=lambda b: abs(float(b.volume)) if b.is_watertight else 0.0)


def measure_bore_profile(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """Classified cross-sections along the pipe axis (#980), or ``{"ok": False, ...}``."""
    from shapely.geometry import Polygon

    problem = measure.mesh_problem(mesh)
    if problem:
        return {"ok": False, "error": problem}
    lo, hi = mesh.bounds
    extents = hi - lo
    axis = int(np.argmax(extents))
    normal = np.zeros(3)
    normal[axis] = 1.0
    raw = []
    for fraction in BORE_STATIONS:
        origin = (lo + hi) / 2.0
        origin[axis] = lo[axis] + fraction * extents[axis]
        section = mesh.section(plane_origin=origin, plane_normal=normal)
        polygons, to_3d = [], np.eye(4)
        if section is not None:
            path_2d, to_3d = section.to_2D()
            polygons = list(path_2d.polygons_full)
        rings = [Polygon(ring) for polygon in polygons for ring in polygon.interiors]
        largest = max(rings, key=lambda ring: abs(ring.area)) if rings else None
        center = None
        if largest is not None:
            c = largest.centroid
            center = (to_3d @ np.array([c.x, c.y, 0.0, 1.0]))[:3]
        raw.append({"fraction": fraction, "z_mm": float(fraction * extents[axis]),
                    "material_mm2": float(sum(p.area for p in polygons)),
                    "bore_mm2": abs(largest.area) if largest is not None else None, "center": center})
    bore = [s for s in raw if s["bore_mm2"]]
    if len(bore) < MIN_BORE_STATIONS:
        return {"ok": False, "error": f"no through bore found ({len(bore)} of {len(BORE_STATIONS)} stations)"}
    ref_bore = float(np.median([s["bore_mm2"] for s in bore]))
    ref_ring = float(np.median([s["material_mm2"] for s in bore]))
    stations, centers = [], []
    for s in raw:
        if s["bore_mm2"]:
            kind, radius = "bore", math.sqrt(s["bore_mm2"] / math.pi)
        elif s["material_mm2"] <= 1e-9:
            kind, radius = "missing", None
        elif s["material_mm2"] >= ref_ring + 0.5 * ref_bore:
            kind, radius = "blocked", None
        else:
            kind, radius = "side_hole", None
        stations.append({"fraction": s["fraction"], "z_mm": round(s["z_mm"], 2), "kind": kind,
                         "radius_mm": None if radius is None else round(radius, 3)})
        centers.append(s["center"] if kind == "bore" else None)
    zs = np.array([s["z_mm"] for s in stations if s["kind"] == "bore"])
    rs = np.array([s["radius_mm"] for s in stations if s["kind"] == "bore"])
    slope = float(np.polyfit(zs, rs, 1)[0]) if len(zs) >= 2 else 0.0
    return {"ok": True, "axis": "xyz"[axis], "axis_index": axis, "bounds": (lo, hi),
            "centers": centers, "length_mm": float(extents[axis]), "stations": stations,
            "median_radius_mm": float(np.median(rs)), "taper_slope_mm_per_mm": slope,
            "taper_change_mm": slope * float(extents[axis])}


def _bore_ring(mesh: trimesh.Trimesh, axis: int, coord: float) -> tuple[np.ndarray, float] | None:
    """``(centre, equivalent radius)`` of the largest interior loop of the cross-section at
    ``coord`` along ``axis``, or ``None`` where the section has no bore loop."""
    from shapely.geometry import Polygon

    lo, hi = mesh.bounds
    origin = (lo + hi) / 2.0
    origin[axis] = coord
    normal = np.zeros(3)
    normal[axis] = 1.0
    section = mesh.section(plane_origin=origin, plane_normal=normal)
    if section is None:
        return None
    path_2d, to_3d = section.to_2D()
    rings = [Polygon(ring) for polygon in path_2d.polygons_full for ring in polygon.interiors]
    if not rings:
        return None
    largest = max(rings, key=lambda ring: abs(ring.area))
    c = largest.centroid
    return (to_3d @ np.array([c.x, c.y, 0.0, 1.0]))[:3], math.sqrt(abs(largest.area) / math.pi)


#: How far inside each end of the body the end probes fit the bore (#994 review): the end
#: probes run from the outermost bore station to the bore measured here, so a taper that
#: keeps narrowing (or a bell that keeps flaring) to the end is followed, not cut through.
END_FIT_INSET_MM = 0.5
#: A radius change between bore stations is a step only if it is still larger than the step
#: tolerance over an axial span this short (#994 review). Larger station-to-station changes
#: are bisected with extra cross-sections, so a smooth flare spread over the span is not a step.
STEP_RESOLUTION_MM = 1.0


def _end_fit(mesh: trimesh.Trimesh, axis: int, stations: list, centers: list, bore_idx: list,
             end: str) -> tuple[np.ndarray, float, float]:
    """``(centre, radius, axis coordinate)`` the end probes aim at, at ``END_FIT_INSET_MM``
    inside the ``"lower"`` or ``"upper"`` end. The measured bore there is used when it follows
    the trend of the two outermost bore stations (or is wider); a bore much narrower than that
    trend is a lip closing the end, so the probes keep the trend radius and hit it. With no bore
    at the end (a cap), the outermost station's radius is kept, as before."""
    lo, hi = mesh.bounds
    outer = bore_idx[:2] if end == "lower" else bore_idx[::-1][:2]
    first = outer[0]
    c0, r0 = np.asarray(centers[first], float), float(stations[first]["radius_mm"])
    coord = lo[axis] + END_FIT_INSET_MM if end == "lower" else hi[axis] - END_FIT_INSET_MM
    trend = r0
    if len(outer) == 2:
        second = outer[1]
        z0, z1 = float(c0[axis]), float(centers[second][axis])
        if abs(z1 - z0) > 1e-9:
            trend = r0 + (float(stations[second]["radius_mm"]) - r0) / (z1 - z0) * (coord - z0)
    trend = max(trend, 0.0)
    ring = _bore_ring(mesh, axis, coord)
    at_end = c0.copy()
    at_end[axis] = coord
    if ring is None:
        return at_end, r0, coord
    centre, radius = ring
    if radius >= trend - max(STEP_MIN_MM, STEP_REL * trend):
        return centre, radius, coord
    return at_end, trend, coord


#: Radius (fraction of the bore's equivalent radius) of the outer probe ring (#994 review):
#: near the wall, so a lip or ridge that narrows the bore between stations is hit, yet inside
#: any round or square bore (a square's inscribed radius is 0.886 of its equivalent radius).
OUTER_PROBE_FRACTION = 0.8
PROBE_OFFSETS = (
    ((0.0, 0.0), (0.5, 0.0), (-0.5, 0.0), (0.0, 0.5), (0.0, -0.5))
    + tuple((round(OUTER_PROBE_FRACTION * math.cos(k * math.pi / 4), 12),
             round(OUTER_PROBE_FRACTION * math.sin(k * math.pi / 4), 12)) for k in range(8))
)
GAP_TOLERANCE_MM = 0.5
BODY_LENGTH_TOLERANCE = 0.1
_CLOSED_END_RE = re.compile(r"\b(closed|stopped|capped)\b")


def _declared_open_ends(spec: Mapping[str, Any]) -> bool:
    """Whether the bore must be open at both ends: unless the spec declares a closed or
    stopped end, a pipe bore is taken as open at both ends."""
    constraints = spec.get("constraints") or {}
    text = f"{constraints.get('bore') or ''} {constraints.get('acoustic_model') or ''}".lower()
    return not _CLOSED_END_RE.search(text)


def probe_through_path(mesh: trimesh.Trimesh, profile: Mapping[str, Any], *,
                       open_ends: bool = True) -> list[dict[str, Any]]:
    """Obstructions on the air path through the bore (#980 review).

    Thirteen probe rays (the bore centre, four points at half the bore radius and an outer
    ring of eight at 0.8 of it, so a lip narrowing the bore near the wall is hit) run between
    every pair of neighbouring ``bore`` stations, and, when the ends must be open, from the
    first and last bore station out past the ends of the body. Any surface hit on a probe
    means material sits in the air passage between stations (a plug or a membrane that
    falls between two cross-sections) or closes an end (an end cap). Segments that span a
    station already classified ``blocked`` or ``missing`` are skipped: that fault is
    reported at its station.
    """
    axis = int(profile["axis_index"])
    lo, hi = profile["bounds"]
    stations, centers = profile["stations"], profile["centers"]
    e_axis = np.zeros(3)
    e_axis[axis] = 1.0
    e1 = np.zeros(3)
    e1[(axis + 1) % 3] = 1.0
    e2 = np.cross(e_axis, e1)
    bore_idx = [i for i, st in enumerate(stations) if st["kind"] == "bore" and centers[i] is not None]
    # (label, start_center, start_r, end_center, end_r, (frac_a, frac_b), extend_to): an end
    # probe continues along its line to the axis coordinate ``extend_to``, past the body.
    segments = []
    for a, b in zip(bore_idx, bore_idx[1:]):
        if any(stations[k]["kind"] in ("blocked", "missing") for k in range(a + 1, b)):
            continue
        segments.append(("interior", centers[a], stations[a]["radius_mm"], centers[b],
                         stations[b]["radius_mm"], (stations[a]["fraction"], stations[b]["fraction"]), None))
    if open_ends and bore_idx:
        first, last = bore_idx[0], bore_idx[-1]
        # #994 review: aim the end probes at the bore actually measured just inside each end
        # (a cone narrowing to its tip, a bell flaring to its rim), then on past the end.
        if not any(stations[k]["kind"] in ("blocked", "missing") for k in range(0, first)):
            c_end, r_end, _ = _end_fit(mesh, axis, stations, centers, bore_idx, "lower")
            segments.append(("end", centers[first], stations[first]["radius_mm"], c_end,
                             r_end, (stations[first]["fraction"], 0.0), lo[axis] - 1.0))
        if not any(stations[k]["kind"] in ("blocked", "missing") for k in range(last + 1, len(stations))):
            c_end, r_end, _ = _end_fit(mesh, axis, stations, centers, bore_idx, "upper")
            segments.append(("end", centers[last], stations[last]["radius_mm"], c_end,
                             r_end, (stations[last]["fraction"], 1.0), hi[axis] + 1.0))
    if not segments:
        return []
    origins, dirs, lengths, owner = [], [], [], []
    for si, (_, c0, r0, c1, r1, _, extend_to) in enumerate(segments):
        for u, v in PROBE_OFFSETS:
            start = np.asarray(c0, float) + r0 * (u * e1 + v * e2)
            end = np.asarray(c1, float) + r1 * (u * e1 + v * e2)
            if extend_to is not None and abs(end[axis] - start[axis]) > 1e-9:
                end = start + (end - start) * (extend_to - start[axis]) / (end[axis] - start[axis])
            vec = end - start
            length = float(np.linalg.norm(vec))
            if length <= 1e-9:
                continue
            origins.append(start)
            dirs.append(vec / length)
            lengths.append(length)
            owner.append(si)
    locations, index_ray, _ = mesh.ray.intersects_location(np.array(origins), np.array(dirs),
                                                            multiple_hits=True)
    hits: dict[int, list[float]] = {}
    rays_hit: dict[int, set[int]] = {}
    for loc, ri in zip(locations, index_ray):
        d = float(np.dot(loc - origins[ri], dirs[ri]))
        if 1e-6 < d < lengths[ri] - 1e-6:
            hits.setdefault(owner[ri], []).append(float(loc[axis] - lo[axis]))
            rays_hit.setdefault(owner[ri], set()).add(int(ri))
    found = []
    for si, positions in sorted(hits.items()):
        kind, _, _, _, _, (fa, fb), _ = segments[si]
        found.append({"kind": "closed_end" if kind == "end" else "obstruction",
                      "between": [fa, fb], "at_mm": round(min(positions) if fb >= fa else max(positions), 2),
                      "probes_hit": len(rays_hit[si]), "probes": len(PROBE_OFFSETS)})
    return found


def axial_gaps(mesh: trimesh.Trimesh, axis: int) -> list[dict[str, float]]:
    """Axial gaps between the bodies of a pipe (#980 review): spans along the axis that no
    body covers, between the first and last body. A pipe split into separated pieces has
    a gap; pieces that touch or overlap (a joint) do not."""
    bodies = [b for b in mesh.split(only_watertight=False) if len(b.faces)]
    if len(bodies) < 2:
        return []
    spans = sorted((float(b.bounds[0][axis]), float(b.bounds[1][axis])) for b in bodies)
    lo = float(mesh.bounds[0][axis])
    gaps, reach = [], spans[0][1]
    for start, end in spans[1:]:
        if start - reach > GAP_TOLERANCE_MM:
            gaps.append({"from_mm": round(reach - lo, 2), "to_mm": round(start - lo, 2),
                         "gap_mm": round(start - reach, 3)})
        reach = max(reach, end)
    return gaps


def _declares_cylindrical(spec: Mapping[str, Any]) -> bool:
    constraints = spec.get("constraints") or {}
    text = f"{constraints.get('bore') or ''} {spec.get('task_brief') or ''}".lower()
    if "conical" in text or "taper" in text:
        return False
    return "cylindrical" in text or _positive(constraints.get("bore_id_mm")) is not None


#: The step scan (#994 review round 3): bore cross-sections every ``STEP_SCAN_MM`` (coarser on
#: long bodies, at most ``MAX_STEP_SCAN`` sections) from ``END_FIT_INSET_MM`` inside one end to
#: the same inside the other, so steps in the end intervals and pairs of steps whose changes
#: cancel between two stations are both seen. Features narrower than the scan spacing can
#: still fall between samples.
STEP_SCAN_MM = 2.0
MAX_STEP_SCAN = 400


def _ring_radius(path) -> float | None:
    """Equivalent radius of the largest interior loop of a planar section, or ``None``."""
    from shapely.geometry import Polygon

    if path is None:
        return None
    rings = [Polygon(ring) for polygon in path.polygons_full for ring in polygon.interiors]
    if not rings:
        return None
    return math.sqrt(max(abs(ring.area) for ring in rings) / math.pi)


def _scan_radii(mesh: trimesh.Trimesh, axis: int, lo_axis: float, length: float) -> list[tuple[float, float]]:
    """``(z, bore radius)`` along the axis at the step-scan positions where the section has a bore."""
    if length <= 2 * END_FIT_INSET_MM:
        return []
    span = length - 2 * END_FIT_INSET_MM
    step = max(STEP_SCAN_MM, span / MAX_STEP_SCAN)
    heights = np.unique(np.r_[np.arange(END_FIT_INSET_MM, length - END_FIT_INSET_MM, step),
                              length - END_FIT_INSET_MM])
    origin = (mesh.bounds[0] + mesh.bounds[1]) / 2.0
    origin[axis] = lo_axis
    normal = np.zeros(3)
    normal[axis] = 1.0
    sections = mesh.section_multiplane(plane_origin=origin, plane_normal=normal, heights=heights)
    out = []
    for z, path in zip(heights, sections):
        radius = _ring_radius(path)
        if radius is not None:
            out.append((float(z), radius))
    return out


def _abrupt_step(mesh: trimesh.Trimesh, axis: int, lo_axis: float, za: float, ra: float,
                 zb: float, rb: float, limit: float) -> tuple[float, float, float] | None:
    """Where (``(z_from, z_to, radius change)``) a radius change of more than ``limit`` happens
    within ``STEP_RESOLUTION_MM``, found by bisecting ``za..zb`` with extra cross-sections;
    ``None`` when the change is spread out (a smooth taper or flare). A span that cannot be
    resolved (no bore loop near its middle) is reported as abrupt, the conservative answer.

    #994 review round 3: when the change is split by the bisection point, neither half may
    exceed ``limit`` on its own. The ``STEP_RESOLUTION_MM`` window across that point is then
    compared as a whole, so a sub-millimetre step that a section lands in is not lost."""
    if abs(rb - ra) <= limit:
        return None
    if zb - za <= STEP_RESOLUTION_MM:
        return za, zb, rb - ra
    span = zb - za
    for zm in (za + span / 2, za + span / 4, za + 3 * span / 4):
        ring = _bore_ring(mesh, axis, lo_axis + zm)
        if ring is None:
            continue
        rm = ring[1]
        found = (_abrupt_step(mesh, axis, lo_axis, za, ra, zm, rm, limit)
                 or _abrupt_step(mesh, axis, lo_axis, zm, rm, zb, rb, limit))
        if found:
            return found
        half = STEP_RESOLUTION_MM / 2.0
        left = _bore_ring(mesh, axis, lo_axis + max(za, zm - half))
        right = _bore_ring(mesh, axis, lo_axis + min(zb, zm + half))
        if left is not None and right is not None and abs(right[1] - left[1]) > limit:
            return max(za, zm - half), min(zb, zm + half), right[1] - left[1]
        return None
    return za, zb, rb - ra


def bore_report(spec: Mapping[str, Any], mesh: trimesh.Trimesh, *, body_id: str) -> dict[str, Any]:
    """Bore continuity and taper for one pipe body, with explained failures."""
    profile = measure_bore_profile(mesh)
    if not profile["ok"]:
        return {"status": "not measurable", "error": profile["error"],
                "failures": [_explain("bore_continuity", measured=None, threshold=None, unit="stations",
                                      requires="a through bore", body_id=body_id, detail=profile["error"])]}
    r_med = profile["median_radius_mm"]
    failures = []
    for s in profile["stations"]:
        if s["kind"] in ("blocked", "missing"):
            what = "solid section: the bore is blocked" if s["kind"] == "blocked" else \
                "no material: the body is broken"
            failures.append(_explain(
                "bore_continuity", measured=s["kind"], threshold="bore", unit="station kind",
                requires="every station is bore or side_hole", body_id=body_id,
                detail=f"station at {s['fraction']:.0%} of the length ({s['z_mm']:g} mm): {what}",
                station=s["fraction"]))
    for gap in axial_gaps(mesh, int(profile["axis_index"])):
        failures.append(_explain(
            "bore_continuity", measured=gap["gap_mm"], threshold=GAP_TOLERANCE_MM, unit="mm",
            requires="measured <= threshold (axial gap between the pieces of the body)", body_id=body_id,
            detail=f"the body is in separate pieces with a {gap['gap_mm']:g} mm gap from "
                   f"{gap['from_mm']:g} to {gap['to_mm']:g} mm along the axis"))
    open_ends = _declared_open_ends(spec)
    path = probe_through_path(mesh, profile, open_ends=open_ends)
    for blockage in path:
        fa, fb = blockage["between"]
        where = (f"between {min(fa, fb):.0%} and {max(fa, fb):.0%} of the length"
                 if blockage["kind"] == "obstruction" else
                 f"at the {'lower' if fb == 0.0 else 'upper'} end")
        failures.append(_explain(
            "bore_continuity", measured=blockage["kind"], threshold="open air path", unit="probe",
            requires="no material on the probe rays through the bore"
                     + (" and out of both open ends" if open_ends else ""),
            body_id=body_id,
            detail=f"material {where} (first hit {blockage['at_mm']:g} mm along the axis; "
                   f"{blockage['probes_hit']} of {blockage['probes']} probe rays hit): "
                   + ("the bore is obstructed" if blockage["kind"] == "obstruction" else
                      "a declared open end is closed")))
    step_limit = max(STEP_MIN_MM, STEP_REL * r_med)
    axis, lo_axis, length = int(profile["axis_index"]), float(profile["bounds"][0][profile["axis_index"]]), \
        profile["length_mm"]
    # #994 review: steps are found on a fine scan of the whole bore, both end intervals
    # included, by the largest change between neighbouring samples (not the net change between
    # stations, which two opposite steps cancel). A change over the tolerance is a step only if
    # it is still over it within STEP_RESOLUTION_MM; a smooth flare spreads it out.
    scan = _scan_radii(mesh, axis, lo_axis, length)
    last_to = -math.inf
    for (za, ra), (zb, rb) in zip(scan, scan[1:]):
        if abs(rb - ra) <= step_limit:
            continue
        abrupt = _abrupt_step(mesh, axis, lo_axis, za, ra, zb, rb, step_limit)
        if abrupt is None or abrupt[0] < last_to:
            continue
        last_to = abrupt[1]
        fraction = round(((abrupt[0] + abrupt[1]) / 2.0) / length, 3)
        failures.append(_explain(
            "bore_continuity", measured=round(abs(rb - ra), 3), threshold=round(step_limit, 3), unit="mm",
            requires="measured <= threshold (radius step along the bore)",
            body_id=body_id, station=fraction,
            detail=f"bore radius jumps {ra:.3g} -> {rb:.3g} mm between {za:.1f} and {zb:.1f} mm along "
                   f"the axis, abruptly (within {abrupt[0]:.1f}-{abrupt[1]:.1f} mm)"))
    taper_limit = max(TAPER_MIN_MM, TAPER_REL * r_med)
    change = profile["taper_change_mm"]
    cylindrical = _declares_cylindrical(spec)
    if cylindrical and abs(change) > taper_limit:
        failures.append(_explain(
            "bore_taper", measured=round(change, 3), threshold=round(taper_limit, 3), unit="mm",
            requires="|measured| <= threshold (radius change over the length of a cylindrical bore)",
            body_id=body_id,
            detail=f"declared cylindrical, but the fitted bore radius changes {change:+.2f} mm over "
                   f"{profile['length_mm']:.0f} mm (slope {profile['taper_slope_mm_per_mm']:+.4f} mm/mm)"))
    declared_length = _positive((spec.get("constraints") or {}).get("body_length_mm"))
    if declared_length and abs(profile["length_mm"] - declared_length) > BODY_LENGTH_TOLERANCE * declared_length:
        failures.append(_explain(
            "bore_length", measured=round(profile["length_mm"], 2), threshold=declared_length, unit="mm",
            requires=f"|measured - threshold| <= {BODY_LENGTH_TOLERANCE:.0%} of threshold", body_id=body_id,
            detail=f"the bore-carrying body spans {profile['length_mm']:.1f} mm along its axis vs the "
                   f"declared body length {declared_length:g} mm"))
    declared_id = _positive((spec.get("constraints") or {}).get("bore_id_mm"))
    if declared_id:
        measured_id = 2.0 * r_med
        if abs(measured_id - declared_id) > BORE_ID_TOLERANCE * declared_id:
            failures.append(_explain(
                "bore_diameter", measured=round(measured_id, 3), threshold=declared_id, unit="mm",
                requires=f"|measured - threshold| <= {BORE_ID_TOLERANCE:.0%} of threshold", body_id=body_id,
                detail=f"median bore diameter {measured_id:.2f} mm vs declared {declared_id:g} mm"))
    return {"status": "inconsistent" if failures else "consistent", "failures": failures,
            "declared_cylindrical": cylindrical, "length_mm": round(profile["length_mm"], 3),
            "median_radius_mm": round(r_med, 3), "taper_change_mm": round(change, 3),
            "taper_slope_mm_per_mm": round(profile["taper_slope_mm_per_mm"], 6),
            "stations": profile["stations"], "open_ends_required": open_ends, "through_path": path,
            "tolerances": {"step_mm": round(step_limit, 3), "taper_mm": round(taper_limit, 3),
                           "bore_id_rel": BORE_ID_TOLERANCE, "gap_mm": GAP_TOLERANCE_MM}}


def _with_bore(result: dict[str, Any], spec: Mapping[str, Any], mesh: trimesh.Trimesh,
               *, body_id: str) -> dict[str, Any]:
    bore = bore_report(spec, mesh, body_id=body_id)
    failures = list(result.get("failures") or []) + bore["failures"]
    status = result["status"]
    if bore["status"] == "inconsistent":
        status = "inconsistent"
    return {**result, "status": status, "pitch_status": result["status"], "bore": bore, "failures": failures}


def _has_bore(body: trimesh.Trimesh, axis: int) -> bool:
    """Whether a body's mid cross-section along ``axis`` has an interior loop (a tube piece)."""
    lo, hi = body.bounds
    origin = (lo + hi) / 2.0
    normal = np.zeros(3)
    normal[axis] = 1.0
    section = body.section(plane_origin=origin, plane_normal=normal)
    if section is None:
        return False
    path_2d, _ = section.to_2D()
    return any(len(polygon.interiors) for polygon in path_2d.polygons_full)


def bore_bodies(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int, int]:
    """The pieces of the bore-carrying body in an assembly (#980 review).

    The largest body fixes the bore axis and footprint. Every body whose footprint across
    that axis overlaps it and that is itself a tube piece (an interior loop at its own mid
    section) belongs to the same bore, so a body split into pieces keeps all of them and
    its full extent; solid parts seated in or on the bore (a reed, a cork) are left out.
    Returns the combined mesh, the number of pieces and the number of bodies left out.
    """
    bodies = [b for b in mesh.split(only_watertight=False) if len(b.faces)]
    if len(bodies) <= 1:
        return mesh, len(bodies), 0
    main = max(bodies, key=lambda b: abs(float(b.volume)) if b.is_watertight else 0.0)
    axis = int(np.argmax(main.bounds[1] - main.bounds[0]))
    across = [i for i in range(3) if i != axis]
    m_lo, m_hi = main.bounds
    picked = []
    for body in bodies:
        lo, hi = body.bounds
        overlap = all(min(hi[i], m_hi[i]) - max(lo[i], m_lo[i]) > 0.5 * min(hi[i] - lo[i], m_hi[i] - m_lo[i])
                      for i in across)
        if body is main or (overlap and _has_bore(body, axis)):
            picked.append(body)
    return trimesh.util.concatenate(picked), len(picked), len(bodies) - len(picked)


def advise_bore_only(spec: Mapping[str, Any], mesh: trimesh.Trimesh) -> dict[str, Any]:
    """Bore continuity/taper for a spec with a declared bore and no modelled pitch (#980)."""
    body, pieces, left_out = bore_bodies(mesh)
    bore = bore_report(spec, body, body_id="bore body")
    bore["pieces"], bore["other_bodies"] = pieces, left_out
    return {"label": LABEL, "affects_scoring": False, "family": BORE_FAMILY, "status": bore["status"],
            "pitch_status": NOT_MODELLED, "bore": bore, "failures": bore["failures"],
            "method": "bore profile on 19 cross-sections of every collinear tube piece (the bore body), "
                      "probe rays through the bore and its open ends, axial gaps between pieces; "
                      "pitch not modelled"}
