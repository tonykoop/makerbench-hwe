// Pure helpers for the workbench Curate tab (#788 W7). No DOM, no fetch:
// unit-tested under node in tests/js/curate_lib.test.mjs.

// The append-only curation log as readable rows, oldest first, exactly as
// stored: every row says what it set, never what it "means" now.
export function curationRows(history) {
  return (history || []).map((row, index) => {
    const parts = [];
    if (row.pick === true && row.rev_id) parts.push(`picked ${shortRev(row.rev_id)}`);
    else if (row.pick === false && row.rev_id) parts.push(`unpicked ${shortRev(row.rev_id)}`);
    if (row.title != null) parts.push(`title set to “${row.title}”`);
    if (row.note != null) parts.push(row.note === "" ? "note cleared" : `note set to “${row.note}”`);
    return {
      key: `${index}:${row.created_at || ""}`,
      created_at: row.created_at || null,
      voter: row.voter || "unknown",
      text: parts.length ? parts.join("; ") : "no change recorded",
    };
  });
}

export function shortRev(revId) {
  return String(revId || "").slice(0, 10);
}

// What Save would send: only the fields that differ from the current state.
export function curationChanges(state, form, currentRevId) {
  const body = {};
  const title = String(form?.title ?? "");
  const note = String(form?.note ?? "");
  if (title !== String(state?.title ?? "")) body.title = title;
  if (note !== String(state?.note ?? "")) body.note = note;
  const picked = state?.pick != null && state.pick === currentRevId;
  if (Boolean(form?.pick) !== picked && currentRevId) {
    body.rev_id = currentRevId;
    body.pick = Boolean(form.pick);
  }
  return body;
}

export function pickText(state, currentRevId, revisions) {
  if (!state?.pick) return "No revision is picked for the catalog yet.";
  if (state.pick === currentRevId) return "This revision is the catalog pick.";
  const rev = (revisions || []).find((r) => r.rev_id === state.pick);
  return rev ? `Revision ${rev.seq} is the catalog pick.` : `Another revision (${shortRev(state.pick)}) is the catalog pick.`;
}

// The export preview as one sentence plus the per-file rows.
export function exportSummary(preview) {
  const files = preview?.files || [];
  const existing = files.filter((f) => f.exists).length;
  if (!files.length) return "Nothing to export.";
  const head = `${files.length} files to ${preview.target}/`;
  if (!existing) return `${head}. The target does not exist yet.`;
  return `${head}. ${existing === files.length ? "All of them already exist" : `${existing} already exist`}; exporting again asks before replacing.`;
}

export function writtenText(result) {
  const count = (result?.written || []).length;
  const replaced = (result?.replaced || []).length;
  const base = `Wrote ${count} files to ${result?.target}/`;
  return `${replaced ? `${base}, replacing ${replaced}.` : `${base}.`} Commit it in the instrument repo when you're ready.`;
}
