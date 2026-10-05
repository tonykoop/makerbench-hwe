import assert from "node:assert/strict";
import { test } from "node:test";

import {
  cameraStrings,
  camerasMatch,
  canCompare3d,
  checkGrid,
  checkState,
  fieldOfViewFor,
  leaderIndex,
  sharedFovLimits,
  verticalFor,
  toggleSelection,
} from "../../makerbench/arena_studio/static/app/lib/matchupCompare.js";

const A = { trial_id: "a", gates: { renders: 1, watertight: 1, min_wall: 0, topology: 0 }, mesh_url: "/m/a" };
const B = { trial_id: "b", gates: { renders: 1, min_wall: 1, watertight: null, zeta_extra: 1 }, mesh_url: "/m/b" };

test("check state: pass, fail, not recorded, not checked", () => {
  assert.equal(checkState(A.gates, "renders"), "pass");
  assert.equal(checkState(A.gates, "min_wall"), "fail");
  assert.equal(checkState(B.gates, "watertight"), "unknown");
  assert.equal(checkState(B.gates, "topology"), "absent");
  assert.equal(checkState(null, "renders"), "absent");
  assert.equal(checkState({ renders: "1" }, "renders"), "unknown");
});

test("#1011: a borderline min_wall is its own state, never a pass or fail", () => {
  assert.equal(checkState({ min_wall: "borderline" }, "min_wall"), "borderline");
  const [row] = checkGrid([{ gates: { min_wall: "borderline" } }, { gates: { min_wall: 1 } }])
    .filter((r) => r.check === "min_wall");
  assert.deepEqual(row.cells.map((c) => c.state), ["borderline", "pass"]);
  assert.equal(row.cells[0].text, "Borderline");
  assert.equal(row.differs, false); // only decided cells (pass/fail) can differ
});

test("the grid aligns every check across both entrants in one fixed order", () => {
  const rows = checkGrid([A, B]);
  assert.deepEqual(rows.map((row) => row.check), ["renders", "watertight", "min_wall", "topology", "zeta_extra"]);
  const byCheck = Object.fromEntries(rows.map((row) => [row.check, row]));
  assert.deepEqual(byCheck.renders.cells.map((cell) => cell.state), ["pass", "pass"]);
  assert.deepEqual(byCheck.min_wall.cells.map((cell) => cell.text), ["Fail", "Pass"]);
  assert.equal(byCheck.min_wall.differs, true);
  assert.equal(byCheck.renders.differs, false);
  // Unknown or unchecked never counts as a difference.
  assert.deepEqual(byCheck.watertight.cells.map((cell) => cell.text), ["Pass", "Not recorded"]);
  assert.equal(byCheck.watertight.differs, false);
  assert.deepEqual(byCheck.topology.cells.map((cell) => cell.text), ["Fail", "Not checked"]);
  assert.equal(byCheck.min_wall.label, "min wall");
  // Order follows the trials passed in.
  assert.deepEqual(checkGrid([B, A])[2].cells.map((cell) => cell.text), ["Pass", "Fail"]);
  assert.deepEqual(checkGrid([]), []);
});

test("selection holds two trials; a third replaces the oldest", () => {
  let selected = [];
  selected = toggleSelection(selected, "a");
  selected = toggleSelection(selected, "b");
  assert.deepEqual(selected, ["a", "b"]);
  assert.deepEqual(toggleSelection(selected, "c"), ["b", "c"]);
  assert.deepEqual(toggleSelection(selected, "a"), ["b"]);
});

test("3D needs WebGL and both meshes", () => {
  assert.equal(canCompare3d([A, B], true), true);
  assert.equal(canCompare3d([A, B], false), false);
  assert.equal(canCompare3d([A, { ...B, mesh_url: null }], true), false);
  assert.equal(canCompare3d([A], true), false);
});

test("camera strings and the leader for the first sync", () => {
  assert.deepEqual(cameraStrings({ theta: 0.5, phi: 1.2, radius: 30 }, { x: 1, y: -2, z: 0 }, 28), {
    orbit: "0.5rad 1.2rad 30m",
    target: "1m -2m 0m",
    fov: "28deg",
  });
  assert.equal(cameraStrings({ theta: NaN, phi: 1, radius: 1 }, { x: 0, y: 0, z: 0 }, 30), null);
  assert.equal(leaderIndex([12, 30]), 1);
  assert.equal(leaderIndex([NaN, 4]), 1);
  assert.equal(leaderIndex([]), -1);
  const cam = { theta: 1, phi: 1, radius: 20, x: 0, y: 0, z: 0, fov: 30 };
  assert.equal(camerasMatch(cam, { ...cam, theta: 1 + 1e-9 }), true);
  assert.equal(camerasMatch(cam, { ...cam, radius: 21 }), false);
  assert.equal(camerasMatch(cam, null), false);
});

test("field of view setting undoes model-viewer's per-model widening", () => {
  assert.ok(Math.abs(fieldOfViewFor(30, 0.5, 1) - 30) < 1e-9); // model narrower than the view: no widening
  const setting = fieldOfViewFor(40, 2, 1); // widened 2x
  const rendered = (2 * Math.atan(Math.tan((setting / 2) * (Math.PI / 180)) * 2) * 180) / Math.PI;
  assert.ok(Math.abs(rendered - 40) < 1e-9);
  assert.ok(setting < 40);
  assert.equal(fieldOfViewFor(40, NaN, 1), null);
  assert.equal(fieldOfViewFor(40, 1, 0), null);
  assert.equal(fieldOfViewFor(0, 1, 1), null);
});

test("shared FOV limits: both views can reach the same rendered range (#998 review)", () => {
  const wide = { idealAspect: 2, aspect: 0.7 }; // widened about 2.9x
  const plain = { idealAspect: 0.5, aspect: 0.7 }; // not widened
  assert.ok(Math.abs(verticalFor(90, plain.idealAspect, plain.aspect) - 90) < 1e-9);
  assert.ok(verticalFor(90, wide.idealAspect, wide.aspect) > 90);
  const shared = sharedFovLimits([wide, plain]);
  // The top is the overlap: the plain view caps it, so the wide view's own
  // maximum setting comes down until it renders the same top.
  assert.ok(Math.abs(shared.max - 90) < 1e-9);
  assert.equal(shared.min, 1, "model-viewer applies the minimum unwidened");
  shared.settings.forEach((own, index) => {
    const frame = [wide, plain][index];
    assert.ok(Math.abs(verticalFor(own.max, frame.idealAspect, frame.aspect) - shared.max) < 1e-9);
    assert.equal(own.min, 1);
    assert.ok(own.max <= 90 + 1e-9);
  });
  assert.ok(shared.settings[0].max < 90, "the wide view's maximum setting is lowered");
  assert.equal(sharedFovLimits([wide, { idealAspect: NaN, aspect: 1 }]), null);
  assert.equal(sharedFovLimits([]), null);
});
