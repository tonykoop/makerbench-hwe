// Measure overlay helpers (#975). Pure: no DOM, no fetch; unit-tested under
// node in tests/js/dimensions_lib.test.mjs. Model units are millimetres (the
// workbench GLB keeps the compiled STL's coordinates).

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function isPoint(point) {
  return Boolean(point) && finite(point.x) && finite(point.y) && finite(point.z);
}

export function toPoint(value) {
  if (Array.isArray(value) && value.length === 3 && value.every(finite)) {
    return { x: value[0], y: value[1], z: value[2] };
  }
  return isPoint(value) ? { x: value.x, y: value.y, z: value.z } : null;
}

export function distanceMm(a, b) {
  const p = toPoint(a);
  const q = toPoint(b);
  if (!p || !q) return null;
  return Math.hypot(p.x - q.x, p.y - q.y, p.z - q.z);
}

export function midpoint(a, b) {
  const p = toPoint(a);
  const q = toPoint(b);
  if (!p || !q) return null;
  return { x: (p.x + q.x) / 2, y: (p.y + q.y) / 2, z: (p.z + q.z) / 2 };
}

// Enough digits to read a thin wall, without false precision on big parts.
export function formatMm(value) {
  if (!finite(value)) return "—";
  const digits = Math.abs(value) < 10 ? 2 : 1;
  return `${value.toFixed(digits)} mm`;
}

export function formatVolume(mm3) {
  if (!finite(mm3)) return "—";
  if (Math.abs(mm3) >= 1000) {
    const cm3 = mm3 / 1000;
    return `${cm3.toFixed(Math.abs(cm3) < 100 ? 2 : 1)} cm³`;
  }
  return `${mm3.toFixed(1)} mm³`;
}

export function formatExtents(extents) {
  if (!Array.isArray(extents) || extents.length !== 3 || !extents.every(finite)) return "—";
  const digits = extents.some((value) => Math.abs(value) < 10) ? 2 : 1;
  return `${extents.map((value) => value.toFixed(digits)).join(" × ")} mm`;
}

// model-viewer reads hotspot positions in model units; the "m" suffix is its
// unit keyword, and one model unit here is one millimetre.
export function hotspotPosition(point) {
  const p = toPoint(point);
  if (!p) return null;
  return `${p.x}m ${p.y}m ${p.z}m`;
}

function row(measurements, metric) {
  return (measurements || []).find((item) => item?.metric === metric) || null;
}

function cell(item, format) {
  if (!item) return { ok: false, text: "not measured" };
  if (!item.ok) return { ok: false, text: `unavailable: ${item.error || "no value"}` };
  return { ok: true, text: format(item.value) };
}

// The three gate metrics, in reading order, with honest failure text.
export function overlayRows(payload) {
  const measurements = payload?.measurements;
  const box = row(measurements, "bbox");
  const wall = row(measurements, "min_wall_thickness");
  const located = Boolean(wall?.ok && payload?.min_wall_location);
  return [
    { key: "bbox", label: "Size (X × Y × Z)", ...cell(box, formatExtents) },
    { key: "volume", label: "Volume", ...cell(row(measurements, "volume"), formatVolume) },
    {
      key: "wall",
      label: "Min wall",
      ...cell(wall, formatMm),
      note: wall?.ok ? (located ? "marked on the model" : "location not recovered") : "",
    },
  ];
}

// Where the min-wall marker goes: the middle of the sampled wall.
export function wallHotspot(payload) {
  const location = payload?.min_wall_location;
  if (!location) return null;
  const centre = midpoint(location.from, location.to);
  return centre ? { position: hotspotPosition(centre), point: centre } : null;
}

// Two-point picking: a third pick starts a new measurement.
export function addPick(points, point) {
  const p = toPoint(point);
  if (!p) return points;
  return points.length >= 2 ? [p] : [...points, p];
}

export function pickText(points) {
  if (points.length === 0) return "Click a point on the model.";
  if (points.length === 1) return "Click a second point.";
  return `Distance A–B: ${formatMm(distanceMm(points[0], points[1]))}`;
}

// A press that moved more than this many pixels was an orbit drag, not a pick.
export const PICK_SLOP_PX = 5;

export function isClick(down, up) {
  if (!down || !up) return false;
  return Math.hypot(up.x - down.x, up.y - down.y) <= PICK_SLOP_PX;
}
