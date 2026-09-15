// Unit tests for the DoE matrix screen's pure rules.
// Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  budgetView,
  ceilingsPayload,
  cellCount,
  doeBlockers,
  formatDuration,
  formatUsd,
  MAX_PREVIEW_CELLS,
  missingCeilings,
  parseSeeds,
  predictedSkips,
  previewQuery,
  skipReasonText,
  unknownCostModels,
} from "../../makerbench/arena_studio/static/app/lib/doe.js";

const matrix = (overrides = {}) => ({
  instruments: ["ocarina"],
  models: ["claude-code-opus-5", "codex-gpt-5.6"],
  levels: ["L1"],
  tiers: ["blind"],
  seeds: [0],
  ...overrides,
});
const ready = (data) => ({ status: "ready", data });

test("seeds parse as unique whole numbers", () => {
  assert.deepEqual(parseSeeds(" 2, 0 1,2 "), { seeds: [0, 1, 2], invalid: [] });
  assert.deepEqual(parseSeeds("1, -3, x, 1.5"), { seeds: [1], invalid: ["-3", "x", "1.5"] });
});

test("the preview query needs every dimension and a sane size", () => {
  assert.equal(cellCount(matrix({ seeds: [0, 1] })), 4);
  const query = new URLSearchParams(previewQuery(matrix({ seeds: [0, 1] })));
  assert.equal(query.get("models"), "claude-code-opus-5,codex-gpt-5.6");
  assert.equal(query.get("context_tiers"), "blind");
  assert.equal(query.get("seeds"), "0,1");
  assert.equal(previewQuery(matrix({ levels: [] })), null);
  const huge = matrix({ seeds: Array.from({ length: MAX_PREVIEW_CELLS }, (_, i) => i) });
  assert.equal(previewQuery(huge), null);
});

const cell = (instrument_id, model_id, seed, cost_usd) => ({
  instrument_id,
  model_id,
  seed,
  context_tier: "blind",
  level: "L1",
  estimate: { cost_usd, cost_source: cost_usd === null ? "unknown" : "telemetry_average:x" },
});

test("budget what-if groups cells into nightly jobs and never counts unknown cost as affordable", () => {
  const cells = [
    cell("ocarina", "a", 0, 1.5),
    cell("ocarina", "b", 0, 2),
    cell("kora", "a", 0, 1.5),
    cell("kora", "paid", 0, null),
    cell("ocarina", "a", 1, 0),
    cell("ocarina", "b", 1, 0),
  ];
  const at3 = budgetView(cells, 3);
  assert.equal(at3.jobs.length, 3);
  assert.deepEqual([at3.within, at3.over, at3.unknown], [1, 1, 1]);
  const kora = at3.jobs.find((job) => job.instrument === "kora");
  assert.equal(kora.status, "unknown");
  assert.deepEqual(kora.unknownModels, ["paid"]);
  // Raising the budget moves the over-budget job, never the unknown one.
  const at1000 = budgetView(cells, 1000);
  assert.deepEqual([at1000.within, at1000.over, at1000.unknown], [2, 0, 1]);
  assert.deepEqual([budgetView(cells, 0).within, budgetView(cells, 0).over], [1, 1]);
});

test("unknown-cost models need a positive ceiling", () => {
  const cells = [cell("ocarina", "paid-b", 0, null), cell("ocarina", "free", 0, 0), cell("kora", "paid-a", 0, null)];
  const unknown = unknownCostModels(cells);
  assert.deepEqual(unknown, ["paid-a", "paid-b"]);
  assert.deepEqual(missingCeilings(unknown, { "paid-a": "0", "paid-b": "abc" }), ["paid-a", "paid-b"]);
  assert.deepEqual(missingCeilings(unknown, { "paid-a": "0.75", "paid-b": "2" }), []);
  assert.deepEqual(ceilingsPayload(unknown, { "paid-a": "0.75", "paid-b": "" }), { "paid-a": 0.75 });
  assert.equal(ceilingsPayload(unknown, {}), undefined);
});

test("predicted skips follow the server's order", () => {
  const references = {
    ocarina: ready({ approved: true, has_image: true }),
    kora: ready({ approved: false, has_image: true }),
    guzheng: ready({ approved: false, has_image: false }),
    erhu: { status: "error" },
  };
  assert.deepEqual(predictedSkips(["ocarina", "kora", "guzheng", "erhu", "pipa"], references, 2), [
    { instrument: "kora", reason: "reference_image_not_approved" },
    { instrument: "guzheng", reason: "reference_image_not_approved" },
    { instrument: "erhu", reason: "check_failed" },
    { instrument: "pipa", reason: "checking" },
  ]);
  assert.deepEqual(predictedSkips(["ocarina"], references, 1), [
    { instrument: "ocarina", reason: "fewer_than_two_entrants" },
  ]);
  assert.equal(skipReasonText("no_reference_image_on_disk"), "No reference image on disk");
});

test("write blockers", () => {
  const base = { invalidSeeds: [], runId: "doe-night", budget: "5", preview: "ready", unknownModels: [], ceilings: {} };
  assert.deepEqual(doeBlockers({ ...base, matrix: matrix() }), []);
  assert.match(doeBlockers({ ...base, matrix: matrix({ models: ["one"] }) })[0], /two entrants/);
  assert.deepEqual(doeBlockers({ ...base, matrix: matrix({ models: ["one"], levels: ["L1", "L2"] }) }), []);
  assert.match(doeBlockers({ ...base, runId: "", matrix: matrix() })[0], /Name the run/);
  assert.match(doeBlockers({ ...base, runId: "../x", matrix: matrix() })[0], /letters, digits/);
  assert.match(doeBlockers({ ...base, budget: "-1", matrix: matrix() })[0], /zero or more/);
  assert.deepEqual(doeBlockers({ ...base, preview: "loading", matrix: matrix() }), ["Waiting for the cost preview…"]);
  const unknown = doeBlockers({ ...base, unknownModels: ["openrouter-x"], matrix: matrix() });
  assert.match(unknown[0], /cost ceiling for openrouter-x/);
  assert.deepEqual(doeBlockers({ ...base, unknownModels: ["openrouter-x"], ceilings: { "openrouter-x": "1" }, matrix: matrix() }), []);
});

test("money and time formatting never invent a number", () => {
  assert.equal(formatUsd(0), "$0.00");
  assert.equal(formatUsd(null), "Unknown");
  assert.equal(formatDuration(45), "45 s");
  assert.equal(formatDuration(600), "10 min");
  assert.equal(formatDuration(9000), "2.5 h");
  assert.equal(formatDuration(undefined), "Unknown");
});
