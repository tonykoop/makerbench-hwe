// Build-check text follows the checks a run recorded: 5 now (wall thickness was removed from
// arena scoring on 2026-10-16), 6 for older recorded runs.
// Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import { gateLabel, resultText } from "../../makerbench/arena_studio/static/app/lib/gateText.js";

const FIVE = { renders: 1, watertight: 1, nonzero_volume: 1, fits_envelope: 1, body_count: 1 };

test("gate label: pass, fail, not recorded", () => {
  assert.equal(gateLabel(1), "Pass");
  assert.equal(gateLabel(0), "Fail");
  assert.equal(gateLabel(null), "Not recorded");
  assert.equal(gateLabel(undefined), "Not recorded");
  assert.equal(gateLabel("borderline"), "Not recorded"); // a retired verdict never reads as a result
});

test("current runs record five checks", () => {
  assert.equal(resultText({ objective_pass_rate: 1, n_objective_trials: 3, gates: FIVE, trials: [] }),
    "Passed all 5 build checks in 3 of 3 runs.");
  const one = { ...FIVE, body_count: 0 };
  assert.equal(resultText({ objective_pass_rate: 0.8, n_objective_trials: 1, gates: one, trials: [{ gates: one }] }),
    "Passed 4 of 5 build checks in this run.");
  assert.equal(resultText({ objective_pass_rate: 0.8, n_objective_trials: 1 }), "Passed 4 of 5 build checks in this run.");
  assert.equal(resultText({ objective_pass_rate: null }), "Build checks were not completed.");
});

test("older recorded runs: the retired wall check is ignored and the rate rescored", () => {
  const six = { ...FIVE, min_wall: 1 };
  assert.equal(resultText({ objective_pass_rate: 1, n_objective_trials: 2, gates: six, trials: [{ gates: six }] }),
    "Passed all 5 build checks in 2 of 2 runs.");
  const failing = { ...FIVE, min_wall: 0 };   // stored 5/6 counted the old wall failure
  assert.equal(resultText({ objective_pass_rate: 5 / 6, n_objective_trials: 1, gates: failing, trials: [{ gates: failing }] }),
    "Passed all 5 build checks in 1 of 1 run.");
});

test("several runs: average with the run count", () => {
  const mean = { renders: 1, watertight: 1, nonzero_volume: 1, fits_envelope: 1, body_count: 0.5 };
  const row = { objective_pass_rate: 0.9, n_objective_trials: 2, gates: mean,
    trials: [{ gates: FIVE }, { gates: { ...FIVE, body_count: 0 } }] };
  assert.equal(resultText(row), "Build-check average: 90.00% across 2 runs.");
});
