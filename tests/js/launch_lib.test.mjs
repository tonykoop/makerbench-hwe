// Unit tests for the Launch & preflight screen's pure rules.
// Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  appendLines,
  costBadge,
  describeReference,
  joinNames,
  launchBlockers,
  launchErrorMessage,
  lockText,
  parseLogEvent,
  parseModelList,
  RUN_ID_PATTERN,
} from "../../makerbench/arena_studio/static/app/lib/launch.js";

const ready = (data) => ({ status: "ready", data });

test("run ids follow the server's rule", () => {
  for (const ok of ["round-11", "r.1_a", "A"]) assert.ok(RUN_ID_PATTERN.test(ok), ok);
  for (const bad of ["", "-lead", "../x", "a/b", "a b", "x".repeat(129)]) {
    assert.ok(!RUN_ID_PATTERN.test(bad), bad);
  }
});

test("model lists split on commas and whitespace, without duplicates", () => {
  assert.deepEqual(parseModelList(" claude-code-opus-5,\ncodex-gpt-5.6  claude-code-opus-5 ,"), [
    "claude-code-opus-5",
    "codex-gpt-5.6",
  ]);
  assert.deepEqual(parseModelList(""), []);
});

test("reference states", () => {
  assert.equal(describeReference(undefined).kind, "unchecked");
  assert.equal(describeReference({ status: "loading" }).kind, "unchecked");
  assert.equal(describeReference({ status: "error" }).kind, "error");
  assert.equal(describeReference(ready({ approved: true, has_image: true })).kind, "approved");
  assert.equal(describeReference(ready({ approved: false, has_image: true })).kind, "review");
  assert.equal(describeReference(ready({ approved: false, has_image: false })).kind, "missing");
});

test("image-tier launches wait for every selected instrument's approval", () => {
  const base = { models: ["stub-a"], runId: "", seed: "0" };
  const references = {
    ocarina: ready({ approved: true, has_image: true }),
    kora: ready({ approved: false, has_image: true }),
  };
  assert.deepEqual(launchBlockers({ ...base, instruments: ["ocarina"], tier: "image", references }), []);
  const blocked = launchBlockers({ ...base, instruments: ["ocarina", "kora"], tier: "image", references });
  assert.equal(blocked.length, 1);
  assert.match(blocked[0], /kora/);
  assert.ok(!blocked[0].includes("ocarina"));
  // Unchecked instruments block too: approval is never assumed.
  assert.deepEqual(
    launchBlockers({ ...base, instruments: ["guzheng"], tier: "image", references }),
    ["Checking reference images…"],
  );
  // The blind tier has no reference gate.
  assert.deepEqual(launchBlockers({ ...base, instruments: ["kora"], tier: "blind", references }), []);
});

test("other launch blockers", () => {
  const blockers = launchBlockers({
    instruments: [],
    models: [],
    tier: "blind",
    references: {},
    runId: "../escape",
    seed: "1.5",
  });
  assert.equal(blockers.length, 4);
});

test("names are shortened past three", () => {
  assert.equal(joinNames(["a", "b"]), "a, b");
  assert.equal(joinNames(["a", "b", "c", "d", "e"]), "a, b, c and 2 more");
});

test("cost badges never guess", () => {
  assert.equal(costBadge({ cost_source: "subscription_zero_marginal", cost_usd: 0 }).label, "$0 subscription");
  assert.equal(
    costBadge({ cost_source: "telemetry_average:data/sessions.jsonl", cost_usd: 0.4213 }).label,
    "Metered, about $0.42 a trial",
  );
  assert.equal(costBadge({ cost_source: "unknown", cost_usd: null }).kind, "unknown");
  assert.equal(costBadge(undefined).kind, "unknown");
});

test("a 403 launch explains --allow-live", () => {
  assert.match(launchErrorMessage({ status: 403, message: "x" }), /--allow-live/);
  assert.equal(launchErrorMessage({ status: 500, message: "boom (HTTP 500)" }), "boom (HTTP 500)");
});

test("log events and the rolling window", () => {
  assert.equal(parseLogEvent('"=== ARENA PROCESS START r1 ==="'), "=== ARENA PROCESS START r1 ===");
  assert.equal(parseLogEvent("not json"), "not json");
  assert.deepEqual(appendLines(["a", "b"], ["c", "d"], 3), ["b", "c", "d"]);
});

test("lock descriptions", () => {
  assert.match(lockText({ status: "ABSENT" }), /^Free/);
  assert.match(lockText({ status: "ACTIVE", pid: 42 }), /process 42/);
  assert.match(lockText({ status: "STALE", pid: 7 }), /^Stale/);
  assert.equal(lockText(null), "Unknown");
});
