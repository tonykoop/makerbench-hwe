// Unit tests for the vote stage's pure modules: keyboard mapping, flags and the
// client-side anonymity guard. Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import { identityLeaks, isBlindAssetUrl } from "../../makerbench/arena_studio/static/app/lib/anonymity.js";
import {
  DEFECT_FLAGS,
  emptyFlags,
  flagsPayload,
  isTypingTarget,
  toggleFlag,
  voteActionForKey,
} from "../../makerbench/arena_studio/static/app/lib/voteKeys.js";

const key = (k, extra = {}) => ({ key: k, code: "", target: { tagName: "BODY" }, ...extra });

test("vote keys", () => {
  for (const k of ["a", "A", "l"]) assert.deepEqual(voteActionForKey(key(k)), { type: "vote", winner: "left" });
  for (const k of ["b", "R"]) assert.deepEqual(voteActionForKey(key(k)), { type: "vote", winner: "right" });
  for (const k of ["t", "d"]) assert.deepEqual(voteActionForKey(key(k)), { type: "vote", winner: "draw" });
  assert.deepEqual(voteActionForKey(key("s")), { type: "skip" });
  assert.deepEqual(voteActionForKey(key("u")), { type: "undo" });
  assert.deepEqual(voteActionForKey(key("v")), { type: "toggle3d" });
  assert.deepEqual(voteActionForKey(key("?", { shiftKey: true })), { type: "help" });
  assert.equal(voteActionForKey(key("x")), null);
});

test("arrow keys never vote (they turn the focused turntable)", () => {
  assert.equal(voteActionForKey(key("ArrowLeft")), null);
  assert.equal(voteActionForKey(key("ArrowRight")), null);
});

test("digit flags use physical keys, with Shift for Candidate B", () => {
  assert.deepEqual(voteActionForKey(key("1", { code: "Digit1" })), {
    type: "flag",
    side: "left",
    flag: DEFECT_FLAGS[0].id,
  });
  // Shift+3 is "#" on US layouts: the code still identifies the key.
  assert.deepEqual(voteActionForKey(key("#", { code: "Digit3", shiftKey: true })), {
    type: "flag",
    side: "right",
    flag: DEFECT_FLAGS[2].id,
  });
  assert.equal(voteActionForKey(key("4", { code: "Digit4" })), null);
});

test("shortcuts never fire while typing, with modifiers, or when already handled", () => {
  assert.equal(voteActionForKey(key("a", { target: { tagName: "INPUT", type: "text" } })), null);
  assert.equal(voteActionForKey(key("a", { target: { tagName: "SELECT" } })), null);
  assert.equal(voteActionForKey(key("a", { target: { tagName: "DIV", isContentEditable: true } })), null);
  assert.equal(voteActionForKey(key("a", { ctrlKey: true })), null);
  assert.equal(voteActionForKey(key("a", { metaKey: true })), null);
  assert.equal(voteActionForKey(key("a", { defaultPrevented: true })), null);
  // A focused checkbox is not a text field: shortcuts still work.
  assert.equal(isTypingTarget({ tagName: "INPUT", type: "checkbox" }), false);
  assert.deepEqual(voteActionForKey(key("a", { target: { tagName: "INPUT", type: "checkbox" } })), {
    type: "vote",
    winner: "left",
  });
});

test("flags toggle per side and serialize for the API", () => {
  let flags = emptyFlags();
  assert.equal(flagsPayload(flags), null);
  flags = toggleFlag(flags, "left", "wrong_proportions");
  flags = toggleFlag(flags, "left", "missing_critical_components");
  flags = toggleFlag(flags, "right", "save_for_later");
  assert.deepEqual(flagsPayload(flags), {
    left: ["missing_critical_components", "wrong_proportions"],
    right: ["save_for_later"],
  });
  flags = toggleFlag(flags, "right", "save_for_later");
  assert.deepEqual(flagsPayload(flags), { left: ["missing_critical_components", "wrong_proportions"] });
});

const blindPair = {
  pair_id: "8c1f0e2a9b7d",
  meta: { instrument_id: "ocarina", seed: 0, rep: 0, round: 0 },
  left: {
    render_path: "/runs/round10/vote_pages/blind/8c1f0e2a9b7d-left.png",
    model3d_path: "/runs/round10/vote_pages/blind/8c1f0e2a9b7d-left.glb",
    frames: ["/runs/round10/vote_pages/blind/8c1f0e2a9b7d-left-f00.png"],
  },
  right: {
    render_path: "/api/morning/night-1/assets/blind/night-000-right.png",
    model3d_path: null,
    frames: null,
  },
};

test("a server-blinded pair passes the client anonymity guard", () => {
  assert.deepEqual(identityLeaks(blindPair), []);
});

test("identity fields anywhere in a pair are caught, without echoing values", () => {
  const leaky = structuredClone(blindPair);
  leaky.left.candidate_id = "claude-code-opus-5__ocarina__seed0";
  leaky.meta.model_id = "codex-gpt-5.6";
  const leaks = identityLeaks(leaky);
  assert.deepEqual(leaks.sort(), ["pair.left.candidate_id", "pair.meta.model_id"]);
  assert.ok(!leaks.join(" ").includes("claude"));
});

test("asset paths must be blind aliases", () => {
  assert.equal(isBlindAssetUrl("/runs/round10/vote_pages/blind/x-left.png"), true);
  assert.equal(isBlindAssetUrl("/runs/round10/vote_pages/claude-code-opus__ocarina/render.png"), false);
  assert.equal(isBlindAssetUrl("/runs/round10/vote_pages/blind/../../run_log.json"), false);
  assert.equal(isBlindAssetUrl("https://cdn.example/x.png"), false);
  const leaky = structuredClone(blindPair);
  leaky.right.frames = ["/runs/round10/vote_pages/codex-gpt__ocarina/f00.png"];
  assert.deepEqual(identityLeaks(leaky), ["pair.right.asset[2]"]);
});
