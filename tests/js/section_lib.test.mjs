import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clampOffset,
  SECTION_OFF,
  sectionActive,
  sectionController,
  sectionPlane,
  transformPlane,
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

// Minimal stand-ins for the Three classes the bridge borrows from the scene.
class Matrix4 {
  constructor() {
    this.elements = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  }
  copy(other) {
    this.elements = [...other.elements];
    this.inverse = other.inverse;
    return this;
  }
  invert() {
    this.elements = this.inverse ? [...this.inverse] : this.elements;
    return this;
  }
  multiplyMatrices() {
    return this; // meshes sit at the model origin in these fakes
  }
}

// Rotation about the vertical (Y) axis by `degrees`, column-major.
function yaw(degrees, translate = [0, 0, 0]) {
  const r = (degrees * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, ...translate, 1];
}

function fakeViewer({ clippable = true } = {}) {
  const material = { isMaterial: true, side: 0, clipShadows: false, clippingPlanes: null };
  if (!clippable) delete material.clippingPlanes;
  let renders = 0;
  class Box3 {
    makeEmpty() {
      this.min = { x: Infinity, y: Infinity, z: Infinity };
      this.max = { x: -Infinity, y: -Infinity, z: -Infinity };
      return this;
    }
    copy(other) {
      this.min = { ...other.min };
      this.max = { ...other.max };
      return this;
    }
    applyMatrix4() {
      return this;
    }
    union(other) {
      for (const k of ["x", "y", "z"]) {
        this.min[k] = Math.min(this.min[k], other.min[k]);
        this.max[k] = Math.max(this.max[k], other.max[k]);
      }
      return this;
    }
    isEmpty() {
      return this.max.x < this.min.x;
    }
  }
  const geometry = {
    boundingBox: null,
    computeBoundingBox() {
      this.boundingBox = { min: { x: -1, y: -2, z: 0 }, max: { x: 1, y: 2, z: 10 } };
    },
  };
  let beforeRender = 0;
  const mesh = { isMesh: true, material, geometry, matrixWorld: new Matrix4(), onBeforeRender: () => beforeRender++ };
  const original = mesh.onBeforeRender;
  const model = {
    matrixWorld: new Matrix4(),
    updateWorldMatrix() {},
    traverse: (visit) => [mesh, { isMesh: false }].forEach(visit),
  };
  const scene = { model, boundingBox: new Box3(), queueRender: () => renders++ };
  const renderer = { threeRenderer: { localClippingEnabled: false } };
  const proto = { get [Symbol("renderer")]() { return renderer; } };
  const element = Object.create(proto);
  element[Symbol("scene")] = scene;
  return {
    element, material, renderer, mesh, model, original,
    renders: () => renders, beforeRender: () => beforeRender,
  };
}

test("transformPlane carries a model-frame plane through rotation, translation and scale", () => {
  const local = { normal: { x: 1, y: 0, z: 0 }, constant: -3 }; // keeps x >= 3
  const same = transformPlane(local, yaw(0));
  assert.deepEqual(same.normal, { x: 1, y: 0, z: 0 });
  assert.ok(Math.abs(same.constant + 3) < 1e-9);
  // A quarter turn about Y maps model +X to world -Z; translation shifts it.
  const turned = transformPlane(local, yaw(90, [0, 0, 5]));
  assert.ok(Math.abs(turned.normal.z + 1) < 1e-9 && Math.abs(turned.normal.x) < 1e-9);
  assert.ok(kept(turned, [0, 0, 1]) && !kept(turned, [0, 0, 3]));
  // Non-uniform scale: x stretched 2x, so the cut at model x=3 sits at world x=6.
  const scaled = transformPlane(local, [2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  assert.ok(kept(scaled, [6.01, 0, 0]) && !kept(scaled, [5.99, 0, 0]));
  assert.equal(transformPlane(local, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]), null);
});

test("the cut follows the model while it auto-rotates (#985 review)", () => {
  const viewer = fakeViewer();
  const control = sectionController(viewer.element);
  control.set({ axis: "x", offset: 100, flip: false }); // 100% keeps the whole model
  const plane = viewer.material.clippingPlanes[0];
  for (const degrees of [0, 45, 90, 180, 270]) {
    // The vendor turns the model without any camera-change event; the
    // per-draw hook alone must re-project the plane.
    viewer.model.matrixWorld.elements = yaw(degrees);
    viewer.mesh.onBeforeRender();
    const turn = (p) => {
      const r = (degrees * Math.PI) / 180;
      return [Math.cos(r) * p[0] + Math.sin(r) * p[2], p[1], -Math.sin(r) * p[0] + Math.cos(r) * p[2]];
    };
    for (const corner of [[-1, -2, 0], [1, 2, 10], [1, -2, 10], [-1, 2, 0]]) {
      assert.ok(kept(plane, turn(corner)), `100% X keeps corner ${corner} at ${degrees} degrees`);
    }
    // A model-frame point past the cut stays cut at every angle.
    control.set({ axis: "x", offset: 50, flip: false });
    viewer.mesh.onBeforeRender();
    assert.ok(!kept(plane, turn([0.9, 0, 5])), `50% X removes model x>0 at ${degrees} degrees`);
    assert.ok(kept(plane, turn([-0.9, 0, 5])), `50% X keeps model x<0 at ${degrees} degrees`);
    control.set({ axis: "x", offset: 100, flip: false });
  }
  assert.ok(viewer.beforeRender() >= 5, "the mesh's own onBeforeRender still runs");
  control.restore();
  assert.equal(viewer.mesh.onBeforeRender, viewer.original, "restore removes the per-draw hook");
});

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
