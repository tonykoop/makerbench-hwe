// Unit tests for the Nightly cockpit and Morning review screens' pure rules.
// Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  budgetSummary,
  formatAge,
  jobNotes,
  leaseKind,
  leaseText,
  nightlyStatusText,
  shouldPoll,
} from "../../makerbench/arena_studio/static/app/lib/nightly.js";
import { morningVoteEndpoints, runVoteEndpoints } from "../../makerbench/arena_studio/static/app/lib/voteEndpoints.js";

test("lease states read plainly", () => {
  assert.equal(leaseText({ status: "ACTIVE" }), "A nightly run holds the lease");
  assert.match(leaseText({ status: "STALE" }), /^Stale/);
  assert.equal(leaseText({ status: "WEIRD" }), "Lease status: WEIRD");
  assert.equal(leaseKind({ status: "ACTIVE" }), "ok");
  assert.equal(leaseKind({ status: "ABSENT" }), "idle");
  assert.equal(leaseKind({ status: "UNREADABLE" }), "problem");
});

test("heartbeat ages", () => {
  assert.equal(formatAge(null), "No heartbeat recorded");
  assert.equal(formatAge(42.4), "42 s ago");
  assert.equal(formatAge(125), "2 min ago");
  assert.equal(formatAge(5400), "1.5 h ago");
  assert.equal(formatAge(3 * 86400), "3 days ago");
});

test("job status and budget", () => {
  assert.equal(nightlyStatusText("votable"), "Ready for morning review");
  assert.equal(nightlyStatusText("mystery"), "mystery");
  assert.deepEqual(budgetSummary({ budget: null, budget_usd: 5 }), { kind: "none", text: "Not started" });
  assert.deepEqual(budgetSummary({ budget_usd: 5, budget: { spent_usd: 1.5 } }), {
    kind: "ok",
    text: "$1.50 of $5.00 spent",
  });
  assert.equal(budgetSummary({ budget_usd: 5, budget: { spent_usd: 6 } }).kind, "over");
  assert.equal(budgetSummary({ budget_usd: 5, budget: { spent_usd: 5, halted_reason: "limit" } }).kind, "halted");
});

test("job notes explain stalls, halts and violations", () => {
  assert.deepEqual(jobNotes({ orphaned: false, budget: null }), []);
  const notes = jobNotes({
    orphaned: true,
    budget: {
      halted_reason: "budget exhausted",
      outcomes: [
        { entrant_id: "a", cost_usd: 1, violation: null },
        { entrant_id: "b", cost_usd: 4, violation: "exceeded max_cost_usd" },
      ],
    },
  });
  assert.equal(notes.length, 3);
  assert.match(notes[0], /stalled/);
  assert.equal(notes[1], "Halted: budget exhausted");
  assert.equal(notes[2], "b: exceeded max_cost_usd");
});

test("polling only while something is live", () => {
  assert.equal(shouldPoll({ lease: { status: "ABSENT" }, jobs: [{ status: "queued" }] }), false);
  assert.equal(shouldPoll({ lease: { status: "ABSENT" }, jobs: [{ status: "running" }] }), true);
  assert.equal(shouldPoll({ lease: { status: "ACTIVE" }, jobs: [] }), true);
  assert.equal(shouldPoll(null), false);
});

test("vote endpoints: runs can undo, morning bundles cannot", () => {
  const run = runVoteEndpoints("round 10");
  assert.equal(run.queue("tony", 2), "/api/runs/round%2010/queue?voter=tony&skip=2");
  assert.equal(run.undo, "/api/runs/round%2010/undo-vote");
  const morning = morningVoteEndpoints("sambuca/night");
  assert.equal(morning.queue("a b", 0), "/api/morning/sambuca%2Fnight/pair?voter=a%20b&skip=0");
  assert.equal(morning.vote, "/api/morning/sambuca%2Fnight/vote");
  assert.equal(morning.undo, null);
  assert.equal(morning.reveal("p1", "tony"), "/api/morning/sambuca%2Fnight/judge-panel?pair_id=p1&voter=tony");
});
