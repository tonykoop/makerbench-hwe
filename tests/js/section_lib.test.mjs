import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clampOffset,
  SECTION_OFF,
  sectionActive,
  sectionController,
  sectionPlane,
} from "../../makerbench/arena_studio/static/app/lib/section.js";

const BOX = { min: [-10, 0, 5], max: [10, 40, 25] };

// Three keeps a point when dot(normal, p) + constant >= 0.
function kept(plane, point) {
  const { x, y, z } = plane.normal;
  return x * point[0] + y * point[1] + z * point[2] + plane.constant >= 0;
}

test("off, unknown or missing axes produce no plane", () => {
  assert.equal(sectionActive(SECTION_OFF), false);
  assert.equal(sectionActive(null), false);
  assert.equal(sectionPlane(BOX, SECTION_OFF), null);
  assert.equal(sectionPlane(BOX, { axis: "w", offset: 50 }), null);
});

test("offset clamps to an integer percentage", () => {
  assert.equal(clampOffset(-4), 0);
  assert.equal(clampOffset(140), 100);
  assert.equal(clampOffset("33.6"), 34);
  assert.equal(clampOffset("nope"), 50);
});

test("the default plane keeps the side below the cut along the chosen axis", () => {
  const plane = sectionPlane(BOX, { axis: "z", offset: 50, flip: false });
  assert.deepEqual(plane.normal, { x: 0, y: 0, z: -1 });
  assert.ok(Math.abs(plane.position - 10) < 1e-3);
  assert.equal(plane.extent, 20);
  assert.ok(kept(plane, [0, 0, 14]));
  assert.ok(!kept(plane, [0, 0, 16]));
});

test("flip keeps the other side", () => {
  const plane = sectionPlane(BOX, { axis: "y", offset: 25, flip: true });
  assert.deepEqual(plane.normal, { x: 0, y: 1, z: 0 });
  assert.ok(kept(plane, [0, 11, 0]));
  assert.ok(!kept(plane, [0, 9, 0]));
});

test("0% removes the whole model and 100% keeps all of it", () => {
  const none = sectionPlane(BOX, { axis: "x", offset: 0 });
  const all = sectionPlane(BOX, { axis: "x", offset: 100 });
  for (const point of [[-10, 0, 5], [10, 40, 25], [0, 20, 15]]) {
    assert.ok(!kept(none, point));
    assert.ok(kept(all, point));
  }
});

function fakeViewer({ clippable = true } = {}) {
  const material = { isMaterial: true, side: 0, clipShadows: false, clippingPlanes: null };
  if (!clippable) delete material.clippingPlanes;
  let renders = 0;
  class Box3 {
    setFromObject() {
      this.min = { x: -1, y: -2, z: 0 };
      this.max = { x: 1, y: 2, z: 10 };
      return this;
    }
  }
  const model = { traverse: (visit) => [{ isMesh: true, material }, { isMesh: false }].forEach(visit) };
  const scene = { model, boundingBox: new Box3(), queueRender: () => renders++ };
  const renderer = { threeRenderer: { localClippingEnabled: false } };
  const proto = { get [Symbol("renderer")]() { return renderer; } };
  const element = Object.create(proto);
  element[Symbol("scene")] = scene;
  return { element, material, renderer, renders: () => renders };
}

test("the controller clips every material, updates in place and restores the originals", () => {
  const viewer = fakeViewer();
  const control = sectionController(viewer.element);
  const state = control.set({ axis: "z", offset: 30, flip: false });
  assert.equal(viewer.renderer.threeRenderer.localClippingEnabled, true);
  assert.equal(viewer.material.clippingPlanes.length, 1);
  assert.equal(viewer.material.side, 2);
  assert.equal(viewer.material.needsUpdate, true);
  assert.ok(Math.abs(state.position - 3) < 1e-2);
  const plane = viewer.material.clippingPlanes[0];
  control.set({ axis: "z", offset: 80, flip: false });
  assert.equal(viewer.material.clippingPlanes[0], plane, "the shared plane updates in place");
  assert.ok(Math.abs(plane.constant - 8) < 1e-2);
  control.restore();
  assert.equal(viewer.material.clippingPlanes, null);
  assert.equal(viewer.material.side, 0);
  assert.equal(viewer.material.clipShadows, false);
  assert.ok(viewer.renders() >= 3);
});

test("a missing bridge or an unclippable material fails before any change", () => {
  assert.throws(() => sectionController({}));
  const viewer = fakeViewer({ clippable: false });
  assert.throws(() => sectionController(viewer.element));
  assert.equal(viewer.renderer.threeRenderer.localClippingEnabled, false);
});
