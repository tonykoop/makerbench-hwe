// #1011: build-check text never reads a "borderline" min_wall as a pass or a fail.
// Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import { gateLabel, resultText } from "../../makerbench/arena_studio/static/app/lib/gateText.js";

const SIX = { renders: 1, watertight: 1, nonzero_volume: 1, fits_envelope: 1, body_count: 1 };

test("runs screen gate label: pass, fail, borderline, not recorded", () => {
  assert.equal(gateLabel(1), "Pass");
  assert.equal(gateLabel(0), "Fail");
  assert.equal(gateLabel("borderline"), "Borderline"); // was "Fail" (value >= 1 on a string)
  assert.equal(gateLabel(null), "Not recorded");
  assert.equal(gateLabel(undefined), "Not recorded");
});

test("one run with 5 passes and 1 borderline is not 'passed all 6'", () => {
  const gates = { ...SIX, min_wall: "borderline" };
  const row = { objective_pass_rate: 1, n_objective_trials: 1, gates, trials: [{ seed: 0, gates }] };
  assert.equal(resultText(row), "Passed 5 of 5 decided build checks in this run (1 borderline check excluded).");
});

test("one run with a fail and a borderline counts decided checks only", () => {
  const gates = { ...SIX, body_count: 0, min_wall: "borderline" };
  const row = { objective_pass_rate: 0.8, n_objective_trials: 1, gates, trials: [{ seed: 0, gates }] };
  assert.equal(resultText(row), "Passed 4 of 5 decided build checks in this run (1 borderline check excluded).");
});

test("rows without a borderline check keep their exact text", () => {
  const all = { ...SIX, min_wall: 1 };
  assert.equal(resultText({ objective_pass_rate: 1, n_objective_trials: 3, gates: all, trials: [] }),
    "Passed all 6 build checks in 3 of 3 runs.");
  const one = { ...SIX, min_wall: 0 };
  assert.equal(resultText({ objective_pass_rate: 5 / 6, n_objective_trials: 1, gates: one, trials: [{ gates: one }] }),
    "Passed 5 of 6 build checks in this run.");
  assert.equal(resultText({ objective_pass_rate: 5 / 6, n_objective_trials: 1 }), "Passed 5 of 6 build checks in this run.");
  assert.equal(resultText({ objective_pass_rate: null }), "Build checks were not completed.");
});

test("several runs with a borderline check: average, never 'all 6', with the count", () => {
  const pass = { ...SIX, min_wall: 1 };
  const border = { ...SIX, min_wall: "borderline" };
  const row = { objective_pass_rate: 1, n_objective_trials: 2, gates: { ...SIX, min_wall: 1 },
    trials: [{ gates: pass }, { gates: border }] };
  assert.equal(resultText(row), "Build-check average: 100.00% across 2 runs (1 borderline check excluded).");
});
