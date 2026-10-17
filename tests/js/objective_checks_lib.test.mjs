import assert from "node:assert/strict";
import test from "node:test";

import { objectiveRate, retainedChecks } from "../../makerbench/arena_studio/static/app/lib/objectiveChecks.js";

test("retained checks drop the retired wall check", () => {
  assert.deepEqual(retainedChecks({ renders: 1, min_wall: 0 }), { renders: 1 });
  assert.deepEqual(retainedChecks(null), {});
});

test("the rate is rescored from retained checks, else the stored number", () => {
  assert.equal(objectiveRate({ objective_pass_rate: 5 / 6, sub_scores: { a: 1, b: 1, min_wall: 0 } }), 1);
  assert.equal(objectiveRate({ objective_pass_rate: 0.4, gates: { a: 1, b: 0 } }), 0.5);
  assert.equal(objectiveRate({ objective_pass_rate: 0.4 }), 0.4);
  assert.equal(objectiveRate({ objective_pass_rate: 0.4, gates: { a: 1, b: null } }), 0.4);
  assert.equal(objectiveRate({}), null);
});
