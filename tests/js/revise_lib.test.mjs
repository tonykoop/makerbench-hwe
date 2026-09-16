import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FEEDBACK_LIMIT,
  canStart,
  confinementText,
  confirmText,
  entrantOptionText,
  feedbackBytes,
  modelProvenanceText,
} from "../../makerbench/arena_studio/static/app/lib/revise.js";

const STUB = { model_id: "stub", provider: "stub", label: "Stub (no model call)", live: false, available: true, allowed: true, reason: null, note: "deterministic placeholder design; spends nothing" };
const CLAUDE_OFF = { model_id: "claude-default", provider: "claude", label: "Claude Code", live: true, available: true, allowed: false, reason: "live revisions need the server started with --allow-live", note: "read-only tools, confined to the workspace" };
const CLAUDE_ON = { ...CLAUDE_OFF, allowed: true, reason: null };
const CODEX_MISSING = { model_id: "codex-default", provider: "codex", label: "Codex", live: true, available: false, allowed: false, reason: "unavailable: codex is not installed", note: "runs inside the entrant sandbox" };

test("option text carries the honest availability state", () => {
  assert.equal(entrantOptionText(STUB), "Stub (no model call) — deterministic placeholder design; spends nothing");
  assert.equal(entrantOptionText(CLAUDE_OFF), "Claude Code — live revisions need the server started with --allow-live");
  assert.equal(entrantOptionText(CLAUDE_ON), "Claude Code — read-only tools, confined to the workspace");
  assert.equal(entrantOptionText(CODEX_MISSING), "Codex — unavailable: codex is not installed");
  assert.equal(entrantOptionText(null), "");
});

test("the confirm names the subscription call, and the stub says nothing is called", () => {
  assert.match(confirmText(CLAUDE_ON), /^This calls Claude Code on your subscription \(read-only tools, confined to the workspace\)/);
  assert.equal(confirmText(STUB), "This runs Stub (no model call): no model is called and nothing is spent.");
});

test("start is gated on a revision, an allowed entrant, feedback and its size", () => {
  assert.deepEqual(canStart({ entrant: STUB, feedback: "hollow", revId: "r-1", running: false }), { ok: true, reason: "" });
  assert.equal(canStart({ entrant: STUB, feedback: "hollow", revId: "r-1", running: true }).ok, false);
  assert.match(canStart({ entrant: STUB, feedback: "hollow", revId: null, running: false }).reason, /origin revision/);
  assert.match(canStart({ entrant: null, feedback: "hollow", revId: "r-1", running: false }).reason, /Choose/);
  assert.match(canStart({ entrant: CLAUDE_OFF, feedback: "hollow", revId: "r-1", running: false }).reason, /--allow-live/);
  assert.match(canStart({ entrant: CODEX_MISSING, feedback: "hollow", revId: "r-1", running: false }).reason, /not installed/);
  assert.match(canStart({ entrant: STUB, feedback: "   ", revId: "r-1", running: false }).reason, /what to change/);
  assert.match(canStart({ entrant: STUB, feedback: "x".repeat(FEEDBACK_LIMIT + 1), revId: "r-1", running: false }).reason, /longer than/);
  assert.equal(feedbackBytes("héllo"), 6);
});

test("confinement and provenance text never overstate", () => {
  assert.equal(confinementText("verified"), "ran inside the entrant sandbox (verified from the launch)");
  assert.equal(confinementText("not_applicable"), "no model ran (stub)");
  assert.equal(confinementText("unconfined"), "confinement not verified: the answer was discarded");
  assert.equal(confinementText("weird"), "confinement weird");
  assert.equal(modelProvenanceText({ kind: "model", model_id: "stub", provider: "stub", confinement: "not_applicable" }), "Revised by stub (stub); no model ran (stub).");
  assert.equal(modelProvenanceText({ kind: "human" }), "");
});
