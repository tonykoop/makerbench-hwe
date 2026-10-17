// Unit tests for the Control Room Morning Review's pure rules (#1105).
// Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  costText,
  dropOn,
  initialState,
  loadDraft,
  minutesText,
  move,
  payload,
  saveDraft,
  setNote,
  toggleRevise,
  trayCounts,
  turnKind,
} from "../../makerbench/arena_studio/static/app/lib/review.js";

const VIEW = {
  designs: [{ trial_id: "a" }, { trial_id: "b" }, { trial_id: "c" }],
  grader_order: ["b", "a", "c"],
  saved_review: null,
};

test("starts from the grader's order, then a saved review, then a local draft", () => {
  assert.deepEqual(initialState(VIEW).order, ["b", "a", "c"]);
  const saved = { ...VIEW, saved_review: { ranking: ["c", "b", "a"], notes: { c: "x" }, revision_requests: [{ trial_id: "c" }] } };
  assert.deepEqual(initialState(saved), { order: ["c", "b", "a"], notes: { c: "x" }, revise: { c: true } });
  const draft = { order: ["a", "c", "b"], notes: {}, revise: {} };
  assert.deepEqual(initialState(saved, draft).order, ["a", "c", "b"]);
  // A draft or saved ranking that no longer matches the night's designs is ignored.
  assert.deepEqual(initialState(VIEW, { order: ["a", "b"] }).order, ["b", "a", "c"]);
});

test("moves and drops keep every design exactly once", () => {
  assert.deepEqual(move(["a", "b", "c"], "c", -1), ["a", "c", "b"]);
  assert.deepEqual(move(["a", "b", "c"], "a", -1), ["a", "b", "c"]);
  assert.deepEqual(dropOn(["a", "b", "c"], "a", "c"), ["b", "c", "a"]);
  assert.deepEqual(dropOn(["a", "b", "c"], "c", "a"), ["c", "a", "b"]);
  assert.deepEqual(dropOn(["a", "b", "c"], "b", "b"), ["a", "b", "c"]);
});

test("revision needs a note, and clearing the note withdraws it", () => {
  let state = initialState(VIEW);
  assert.match(toggleRevise(state, "a").error, /Write a note/);
  state = setNote(state, "a", "pegs too small");
  state = toggleRevise(state, "a").state;
  assert.equal(state.revise.a, true);
  state = setNote(state, "a", "  ");
  assert.equal(state.revise.a, false);
});

test("the payload lists revisions in ranking order and drops blank notes", () => {
  let state = { order: ["c", "a", "b"], notes: { a: "lean back", b: " ", c: "taller" }, revise: { a: true, c: true } };
  assert.deepEqual(payload(state, { reviewer: "tony", extraTurns: 3 }), {
    ranking: ["c", "a", "b"],
    notes: { a: "lean back", c: "taller" },
    revise: ["c", "a"],
    extra_turns: 3,
    reviewer: "tony",
  });
  assert.deepEqual(trayCounts(state), { notes: 2, revise: 2 });
});

test("cost, time and turn strip read plainly", () => {
  assert.equal(costText({ kind: "usd", usd: 0.312 }), "$0.31");
  assert.equal(costText({ kind: "subscription" }), "subscription");
  assert.equal(costText({ kind: "unknown" }), "not recorded");
  assert.equal(minutesText(2280), "38 min");
  assert.equal(minutesText(null), "not recorded");
  assert.equal(turnKind({ passed: 5, total: 5 }), "pass");
  assert.equal(turnKind({ passed: 3, total: 5 }), "partial");
  assert.equal(turnKind({ passed: 2, total: 5 }), "fail");
});

test("drafts survive broken storage", () => {
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(loadDraft("r", broken), null);
  saveDraft("r", { order: [] }, broken);
  const map = new Map();
  const ok = { getItem: (k) => map.get(k) ?? null, setItem: (k, v) => map.set(k, v) };
  saveDraft("r", { order: ["a"] }, ok);
  assert.deepEqual(loadDraft("r", ok), { order: ["a"] });
});
