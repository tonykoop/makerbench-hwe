// Unit tests for the Agreement analytics and Compare screens' pure rules.
// Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  exportTargets,
  findOutliers,
  formatElo,
  formatPassRate,
  formatRank,
  formatRho,
  interpretationText,
  isProvisional,
  pointLabel,
  ratingScale,
  runInstruments,
  scatterPoints,
  sharedEntrants,
} from "../../makerbench/arena_studio/static/app/lib/analytics.js";

test("an Elo from fewer than five games is provisional (Q5)", () => {
  assert.equal(isProvisional({ games: 0 }), true);
  assert.equal(isProvisional({ games: 4 }), true);
  assert.equal(isProvisional({ games: 5 }), false);
  assert.equal(isProvisional({}), true);
});

test("formatting never invents a number", () => {
  assert.equal(formatRho(0.5), "ρ = 0.50");
  assert.equal(formatRho(null), "ρ not available");
  assert.equal(formatElo(1516.4), "1516");
  assert.equal(formatElo(undefined), "Unknown");
  assert.equal(formatPassRate(0.755), "76%");
  assert.equal(formatPassRate(null), "Unknown");
  assert.equal(formatRank(2), "#2");
  assert.equal(formatRank(1.5), "#1.5");
  assert.equal(interpretationText("strong_alignment").startsWith("People"), true);
  assert.equal(interpretationText("something_new"), "No reading");
});

test("outliers use the server's rank-gap threshold", () => {
  const rankings = [
    { entrant: "a", rank_delta: 0 },
    { entrant: "b", rank_delta: -2 },
    { entrant: "c", rank_delta: 3 },
    { entrant: "d", rank_delta: null },
  ];
  assert.deepEqual(findOutliers(rankings).map((row) => row.entrant), ["b", "c"]);
});

test("one rating scale covers every interval", () => {
  const scale = ratingScale([
    { rating: 1500, ci_low: 1450, ci_high: 1550 },
    { rating: 1480, ci_low: null, ci_high: null },
  ]);
  assert.equal(scale.lo, 1440);
  assert.equal(scale.hi, 1560);
  assert.equal(scale.x(1500), 50);
  assert.equal(ratingScale([]), null);
});

test("scatter points need both scores and put the best Elo on top", () => {
  const { points, eloRange } = scatterPoints([
    { entrant: "top", subjective_elo: 1600, objective_pass_rate: 0.5 },
    { entrant: "low", subjective_elo: 1400, objective_pass_rate: 1 },
    { entrant: "no-checks", subjective_elo: 1500, objective_pass_rate: null },
  ]);
  assert.equal(points.length, 2);
  assert.deepEqual(eloRange, [1380, 1620]);
  const top = points.find((point) => point.entrant === "top");
  const low = points.find((point) => point.entrant === "low");
  assert.ok(top.y < low.y);
  assert.equal(low.x, 100);
  assert.equal(pointLabel(top), "top: Elo 1600, pass rate 50%");
  assert.deepEqual(scatterPoints([]), { points: [], eloRange: null });
});

test("export targets mirror the server's safe-id rule", () => {
  const { targets, skipped } = exportTargets(["ocarina", "kora", "ocarina", "../escape", "", null]);
  assert.deepEqual(targets.map((target) => target.path), [
    "instruments/ocarina/winner.scad",
    "instruments/kora/winner.scad",
  ]);
  assert.deepEqual(skipped, ["../escape"]);
});

test("export instruments come from the run's trials when present", () => {
  assert.deepEqual(runInstruments({ trials: [{ instrument_id: "kora" }], instruments: ["x"] }), ["kora"]);
  assert.deepEqual(runInstruments({ instruments: ["x"] }), ["x"]);
  assert.deepEqual(runInstruments(null), []);
});

test("shared entrants across two leaderboards", () => {
  assert.deepEqual(
    sharedEntrants(
      { leaderboard: [{ entrant: "b" }, { entrant: "a" }] },
      { leaderboard: [{ entrant: "a" }, { entrant: "c" }, { entrant: "b" }] },
    ),
    ["a", "b"],
  );
  assert.deepEqual(sharedEntrants(null, { leaderboard: [] }), []);
});
