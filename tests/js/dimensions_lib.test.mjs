import assert from "node:assert/strict";
import { test } from "node:test";

import {
  addPick,
  distanceMm,
  formatExtents,
  formatMm,
  formatVolume,
  hotspotPosition,
  isClick,
  midpoint,
  overlayRows,
  pickText,
  toPoint,
  wallHotspot,
} from "../../makerbench/arena_studio/static/app/lib/dimensions.js";

const PAYLOAD = {
  ok: true,
  measurements: [
    { metric: "volume", ok: true, value: 3904, unit: "mm3" },
    { metric: "bbox", ok: true, value: [20, 20, 20], unit: "mm", details: { min: [-10, -10, -10], max: [10, 10, 10] } },
    { metric: "min_wall_thickness", ok: true, value: 1.999, unit: "mm" },
  ],
  min_wall_location: { from: [10, 1, 2], to: [8, 1, 2] },
  units: "mm",
};

test("points come from arrays or {x,y,z} and reject anything else", () => {
  assert.deepEqual(toPoint([1, 2, 3]), { x: 1, y: 2, z: 3 });
  assert.deepEqual(toPoint({ x: 1, y: 2, z: 3, extra: 9 }), { x: 1, y: 2, z: 3 });
  for (const bad of [null, [1, 2], [1, 2, NaN], { x: 1, y: 2 }, "1 2 3"]) assert.equal(toPoint(bad), null);
});

test("distance and midpoint", () => {
  assert.equal(distanceMm([0, 0, 0], { x: 3, y: 4, z: 12 }), 13);
  assert.equal(distanceMm([0, 0, 0], null), null);
  assert.deepEqual(midpoint([0, 0, 0], [2, 4, 6]), { x: 1, y: 2, z: 3 });
});

test("formats keep thin walls readable and big parts plain", () => {
  assert.equal(formatMm(1.999), "2.00 mm");
  assert.equal(formatMm(0.456), "0.46 mm");
  assert.equal(formatMm(123.456), "123.5 mm");
  assert.equal(formatMm(Infinity), "—");
  assert.equal(formatVolume(3904), "3.90 cm³");
  assert.equal(formatVolume(250000), "250.0 cm³");
  assert.equal(formatVolume(512.25), "512.3 mm³");
  assert.equal(formatVolume(null), "—");
  assert.equal(formatExtents([20, 20, 5.5]), "20.00 × 20.00 × 5.50 mm");
  assert.equal(formatExtents([120, 40, 30]), "120.0 × 40.0 × 30.0 mm");
  assert.equal(formatExtents([1, 2]), "—");
});

test("hotspot positions use model units with model-viewer's unit keyword", () => {
  assert.equal(hotspotPosition({ x: 1.5, y: -2, z: 0 }), "1.5m -2m 0m");
  assert.equal(hotspotPosition(null), null);
});

test("overlay rows read the gate metrics and say where the wall is", () => {
  const rows = overlayRows(PAYLOAD);
  assert.deepEqual(rows.map((row) => row.key), ["bbox", "volume", "wall"]);
  assert.equal(rows[0].text, "20.0 × 20.0 × 20.0 mm");
  assert.equal(rows[1].text, "3.90 cm³");
  assert.equal(rows[2].text, "2.00 mm");
  assert.equal(rows[2].note, "marked on the model");
  assert.deepEqual(wallHotspot(PAYLOAD), { position: "9m 1m 2m", point: { x: 9, y: 1, z: 2 } });
});

test("failures are honest and never invent a marker", () => {
  const payload = {
    ok: false,
    measurements: [
      { metric: "bbox", ok: true, value: [5, 5, 2.5] },
      { metric: "volume", ok: false, error: "mesh is not watertight" },
      { metric: "min_wall_thickness", ok: false, error: "mesh is not watertight" },
    ],
    min_wall_location: null,
  };
  const rows = overlayRows(payload);
  assert.equal(rows[1].ok, false);
  assert.equal(rows[1].text, "unavailable: mesh is not watertight");
  assert.equal(rows[2].note, "");
  assert.equal(wallHotspot(payload), null);
  assert.equal(overlayRows({}).every((row) => row.text === "not measured"), true);
  const unlocated = { ...PAYLOAD, min_wall_location: null };
  assert.equal(overlayRows(unlocated)[2].note, "location not recovered");
});

test("two-point picking: a third pick starts over; bad hits are ignored", () => {
  let points = [];
  assert.equal(pickText(points), "Click a point on the model.");
  points = addPick(points, { x: 0, y: 0, z: 0 });
  assert.equal(pickText(points), "Click a second point.");
  points = addPick(points, null);
  assert.equal(points.length, 1);
  points = addPick(points, [0, 3, 4]);
  assert.equal(pickText(points), "Distance A–B: 5.00 mm");
  points = addPick(points, [1, 1, 1]);
  assert.deepEqual(points, [{ x: 1, y: 1, z: 1 }]);
});

test("a drag is an orbit, not a pick", () => {
  assert.equal(isClick({ x: 10, y: 10 }, { x: 13, y: 13 }), true);
  assert.equal(isClick({ x: 10, y: 10 }, { x: 30, y: 10 }), false);
  assert.equal(isClick(null, { x: 1, y: 1 }), false);
});
