import assert from "node:assert/strict";
import { test } from "node:test";

import {
  curationChanges,
  curationRows,
  exportSummary,
  pickText,
  shortRev,
  writtenText,
} from "../../makerbench/arena_studio/static/app/lib/curate.js";

const R1 = "r-1111111111abcdef";
const R2 = "r-2222222222abcdef";

test("curation rows read the append-only log verbatim, oldest first", () => {
  const rows = curationRows([
    { rev_id: R1, pick: true, title: "Best", note: null, voter: "tony", created_at: "2026-09-15T10:00:00Z" },
    { rev_id: R1, pick: false, title: null, note: "", voter: "tony", created_at: "2026-09-15T10:01:00Z" },
    { rev_id: null, pick: null, title: null, note: "<img onerror=x>", voter: "tony", created_at: "2026-09-15T10:02:00Z" },
    { rev_id: null, pick: null, title: null, note: null },
  ]);
  assert.deepEqual(rows.map((r) => r.text), [
    `picked ${shortRev(R1)}; title set to “Best”`,
    `unpicked ${shortRev(R1)}; note cleared`,
    "note set to “<img onerror=x>”",
    "no change recorded",
  ]);
  assert.equal(rows[3].voter, "unknown");
  assert.deepEqual(curationRows(null), []);
});

test("save sends only what differs from the current state", () => {
  const state = { pick: R1, title: "Best", note: "keep" };
  assert.deepEqual(curationChanges(state, { title: "Best", note: "keep", pick: true }, R1), {});
  assert.deepEqual(curationChanges(state, { title: "Better", note: "keep", pick: true }, R1), { title: "Better" });
  assert.deepEqual(curationChanges(state, { title: "Best", note: "", pick: true }, R1), { note: "" });
  assert.deepEqual(curationChanges(state, { title: "Best", note: "keep", pick: false }, R1), { rev_id: R1, pick: false });
  // picking the current revision when another one is picked
  assert.deepEqual(curationChanges(state, { title: "Best", note: "keep", pick: true }, R2), { rev_id: R2, pick: true });
  // no current revision: the pick cannot change
  assert.deepEqual(curationChanges({ pick: null, title: null, note: null }, { title: "", note: "", pick: true }, null), {});
});

test("pick text names the picked revision honestly", () => {
  assert.equal(pickText({ pick: null }, R1, []), "No revision is picked for the catalog yet.");
  assert.equal(pickText({ pick: R1 }, R1, []), "This revision is the catalog pick.");
  assert.equal(pickText({ pick: R2 }, R1, [{ rev_id: R2, seq: 4 }]), "Revision 4 is the catalog pick.");
  assert.equal(pickText({ pick: R2 }, R1, []), `Another revision (${shortRev(R2)}) is the catalog pick.`);
});

test("export summary and result text", () => {
  const preview = { target: "strings/boxolin/arena/workbench/d-1/r-1", files: [{ name: "a", exists: false }, { name: "b", exists: false }] };
  assert.equal(exportSummary(preview), "2 files to strings/boxolin/arena/workbench/d-1/r-1/. The target does not exist yet.");
  assert.match(exportSummary({ ...preview, files: [{ exists: true }, { exists: false }] }), /1 already exist; exporting again asks before replacing/);
  assert.match(exportSummary({ ...preview, files: [{ exists: true }, { exists: true }] }), /All of them already exist/);
  assert.equal(exportSummary({ files: [] }), "Nothing to export.");
  assert.equal(writtenText({ target: "x/y", written: ["x/y/a", "x/y/b"], replaced: [] }), "Wrote 2 files to x/y/. Commit it in the instrument repo when you're ready.");
  assert.equal(writtenText({ target: "x/y", written: ["x/y/a"], replaced: ["a"] }), "Wrote 1 files to x/y/, replacing 1. Commit it in the instrument repo when you're ready.");
});
