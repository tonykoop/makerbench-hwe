import assert from "node:assert/strict";
import { test } from "node:test";

import {
  appendLines,
  checkRows,
  describeDiffRow,
  diffSummary,
  editorKindText,
  findLineReferences,
  isFinished,
  jobStatusText,
  latestRevision,
  offsetOfLine,
  originText,
  parseLogEvent,
  revisionTree,
  saveTargetText,
} from "../../makerbench/arena_studio/static/app/lib/workbench.js";

test("job status text is honest for every status and unknowns", () => {
  assert.equal(jobStatusText("running"), "Compiling");
  assert.equal(jobStatusText("interrupted"), "Stopped before finishing");
  assert.equal(jobStatusText("weird"), "weird");
  assert.equal(jobStatusText(undefined), "Unknown");
  assert.ok(isFinished("failed") && isFinished("cancelled") && !isFinished("queued") && !isFinished("running"));
  assert.equal(editorKindText("parameters"), "parameters changed");
});

test("revision tree keeps seq order and derives depth from parents", () => {
  const tree = revisionTree([
    { rev_id: "r-c", seq: 3, parent_rev_id: "r-a" },
    { rev_id: "r-a", seq: 1, parent_rev_id: null },
    { rev_id: "r-b", seq: 2, parent_rev_id: "r-a" },
    { rev_id: "r-d", seq: 4, parent_rev_id: "r-c" },
    { rev_id: "r-x", seq: 5, parent_rev_id: "r-missing" },
  ]);
  assert.deepEqual(tree.map((r) => [r.rev_id, r.depth, r.branch]), [
    ["r-a", 0, ""], ["r-b", 1, ""], ["r-c", 1, ""], ["r-d", 2, ""], ["r-x", 0, "detached"],
  ]);
  assert.equal(latestRevision(tree).rev_id, "r-x");
  assert.equal(latestRevision([]), null);
});

test("diff rows are described with a spelled-out label, never colour alone", () => {
  assert.deepEqual(describeDiffRow({ op: "+", text: "a = 1;" }), { kind: "added", marker: "+", label: "added", text: "a = 1;" });
  assert.equal(describeDiffRow({ op: "-", text: "x" }).marker, "−");
  assert.equal(describeDiffRow({ op: "@", text: "@@ -1 +1 @@" }).kind, "hunk");
  assert.equal(describeDiffRow({ op: " ", text: "same" }).label, "unchanged");
  assert.equal(describeDiffRow(null).kind, "same");
  assert.equal(diffSummary([{ op: "+" }, { op: "+" }, { op: "-" }, { op: " " }]), "2 added, 1 removed");
  assert.equal(diffSummary([]), "No source changes.");
});

test("check rows keep the API's honest states", () => {
  assert.equal(checkRows(null).state, "none");
  assert.equal(checkRows({ render_ok: false, error: "boom" }).note, "boom");
  const undeclared = checkRows({ render_ok: true, objective: { declared: false, note: "gates not declared" } });
  assert.equal(undeclared.state, "undeclared");
  assert.equal(undeclared.note, "gates not declared");
  const scored = checkRows({ render_ok: true, objective: { declared: true, objective_pass_rate: 0.5, checks: { renders: { passed: true }, min_wall: { passed: false, detail: "1.2 mm" }, odd: { passed: null } } } });
  assert.equal(scored.state, "scored");
  assert.deepEqual(scored.rows.map((r) => [r.name, r.result]), [["renders", "pass"], ["min_wall", "fail"], ["odd", "unknown"]]);
  assert.equal(scored.rows[1].detail, "1.2 mm");
  assert.equal(scored.note, "Pass rate 50%");
  const legacy = checkRows({ render_ok: true, objective: { declared: true, sub_scores: { watertight: 1.0, min_wall: 0.0 } } });
  assert.deepEqual(legacy.rows.map((r) => r.result), ["pass", "fail"]);
});

test("line references and offsets", () => {
  assert.deepEqual(findLineReferences("ERROR: Parser error in line 3\nWARNING: line 3, line 10: unused"), [3, 10]);
  assert.deepEqual(findLineReferences(""), []);
  const src = "a = 1;\nbb = 2;\nccc = 3;";
  assert.equal(offsetOfLine(src, 1), 0);
  assert.equal(offsetOfLine(src, 2), 7);
  assert.equal(offsetOfLine(src, 3), 15);
  assert.equal(offsetOfLine(src, 99), 15);
  assert.equal(offsetOfLine(src, 0), 0);
});

test("log helpers", () => {
  assert.equal(parseLogEvent('"hello"'), "hello");
  assert.equal(parseLogEvent("not json"), "not json");
  assert.equal(parseLogEvent('{"a":1}'), '{"a":1}');
  assert.deepEqual(appendLines(["a"], ["b", "c"], 2), ["b", "c"]);
});

test("save target and origin text", () => {
  assert.equal(saveTargetText(null, null), "This design has no revision yet.");
  assert.equal(saveTargetText({ rev_id: "r-b", seq: 2 }, { rev_id: "r-b", seq: 2 }), "Saves as revision 3 from 2.");
  assert.equal(saveTargetText({ rev_id: "r-a", seq: 1 }, { rev_id: "r-b", seq: 2 }), "Saves as a branch of revision 1 (the latest is 2).");
  assert.equal(originText({ kind: "trial", trial_id: "t1", model_id: "m", run_id: "r" }), "Trial t1 by m in run r");
  assert.equal(originText({ kind: "master", file: "uke.scad", instrument_id: "ukulele" }), "Master uke.scad of ukulele");
  assert.equal(originText({ kind: "blank" }), "Blank design");
  assert.equal(originText(null), "Unknown origin");
});
