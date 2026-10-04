import assert from "node:assert/strict";
import { test } from "node:test";

import { wireframeController } from "../../makerbench/arena_studio/static/app/lib/wireframe.js";

function wrapper(materials, update = () => {}) {
  return { [Symbol("correlatedObjects")]: new Set(materials), [Symbol("onUpdate")]: update };
}

test("updates every backing material, invalidates rendering, and restores original values", () => {
  const first = { isMaterial: true, wireframe: false };
  const second = { isMaterial: true, wireframe: true };
  let updates = 0;
  const control = wireframeController({ materials: [wrapper([first, second], () => updates++)] });
  control.set(true);
  assert.equal(first.wireframe, true);
  assert.equal(second.wireframe, true);
  assert.equal(first.needsUpdate, true);
  control.set(false);
  assert.equal(first.wireframe, false);
  assert.equal(second.wireframe, false);
  control.restore();
  assert.equal(first.wireframe, false);
  assert.equal(second.wireframe, true);
  assert.equal(updates, 3);
});

test("unsupported, empty, or inactive-only material graphs fail before any override", () => {
  const first = { isMaterial: true, wireframe: false };
  for (const model of [null, { materials: [] }, { materials: [{}] },
    { materials: [wrapper([])] }, { materials: [wrapper([{}])] },
    { materials: [{ isActive: false }] },
    { materials: [wrapper([first]), {}] }]) {
    assert.throws(() => wireframeController(model));
    assert.equal(first.wireframe, false);
    assert.equal(first.needsUpdate, undefined);
  }
});

test("inactive lazy variants do not prevent a loaded material from toggling", () => {
  const material = { isMaterial: true, wireframe: false };
  const control = wireframeController({ materials: [{ isActive: false }, wrapper([material])] });
  control.set(true);
  assert.equal(material.wireframe, true);
});
