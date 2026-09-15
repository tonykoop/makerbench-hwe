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

:func:`helmholtz_hz` (vessel flutes) is provided and tested against an analytic
fixture, but no registry family is wired to it yet.
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
        return {**base, "status": NOT_MODELLED, "reason": reason}
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
