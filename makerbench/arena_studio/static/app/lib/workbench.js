// Pure helpers for the design workbench screen (#788 W4). No DOM, no fetch:
// everything here is unit-tested under node in tests/js/workbench_lib.test.mjs.

export const JOB_STATUS_TEXT = {
  queued: "Queued",
  running: "Compiling",
  succeeded: "Compiled",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Stopped before finishing",
};

export const FINISHED = new Set(["succeeded", "failed", "cancelled", "interrupted"]);

export function jobStatusText(status) {
  return JOB_STATUS_TEXT[status] || String(status || "Unknown");
}

export function isFinished(status) {
  return FINISHED.has(status);
}

export const EDITOR_KIND_TEXT = {
  human: "edited by hand",
  parameters: "parameters changed",
  model: "revised by a model",
};

export function editorKindText(kind) {
  return EDITOR_KIND_TEXT[kind] || String(kind || "unknown editor");
}

// Revisions arrive as a flat, seq-ordered list with parent ids. The tree keeps
// that order and adds a depth so lineage reads as an outline without any DOM.
export function revisionTree(revisions) {
  const rows = [...(revisions || [])].sort((a, b) => (a.seq || 0) - (b.seq || 0));
  const depthById = new Map();
  return rows.map((rev) => {
    const parentDepth = rev.parent_rev_id != null ? depthById.get(rev.parent_rev_id) : undefined;
    const depth = parentDepth === undefined ? 0 : parentDepth + 1;
    depthById.set(rev.rev_id, depth);
    return { ...rev, depth, branch: rev.parent_rev_id != null && parentDepth === undefined ? "detached" : "" };
  });
}

// The last revision by seq, which is where a new design's edits start from.
export function latestRevision(revisions) {
  const tree = revisionTree(revisions);
  return tree.length ? tree[tree.length - 1] : null;
}

// Unified diff rows from the API ("+", "-", " ", "@") as accessible lines: the
// symbol is spelled out for screen readers, and colour is never the only cue.
export function describeDiffRow(row) {
  const op = row?.op || " ";
  const text = row?.text || "";
  if (op === "@") return { kind: "hunk", marker: "@@", label: "hunk", text };
  if (op === "+") return { kind: "added", marker: "+", label: "added", text };
  if (op === "-") return { kind: "removed", marker: "−", label: "removed", text };
  return { kind: "same", marker: " ", label: "unchanged", text };
}

export function diffSummary(rows) {
  let added = 0;
  let removed = 0;
  for (const row of rows || []) {
    if (row?.op === "+") added += 1;
    else if (row?.op === "-") removed += 1;
  }
  if (!added && !removed) return "No source changes.";
  return `${added} added, ${removed} removed`;
}

// Objective checks from the workbench objective payload, as table rows. The
// API's honest states survive: undeclared gates say so instead of showing 0/0.
export function checkRows(objective) {
  const block = objective?.objective;
  if (!objective) return { state: "none", rows: [], note: "No compile yet." };
  if (objective.render_ok === false) {
    return { state: "failed", rows: [], note: objective.error || "The compile failed." };
  }
  if (!block || block.declared === false) {
    return { state: "undeclared", rows: [], note: block?.note || "This instrument has no declared gates." };
  }
  if (block.error) return { state: "error", rows: [], note: block.error };
  const checks = block.checks || block.gates || block.sub_scores || {};
  const rows = Object.entries(checks).map(([name, value]) => {
    const passed = value && typeof value === "object" ? value.passed : value;
    const ok = passed === true || passed === 1 || passed === 1.0;
    const bad = passed === false || passed === 0;
    return { name, result: ok ? "pass" : bad ? "fail" : "unknown", detail: value && typeof value === "object" ? value.detail || "" : "" };
  });
  const rate = typeof block.objective_pass_rate === "number" ? block.objective_pass_rate : null;
  return { state: "scored", rows, rate, note: rate === null ? "" : `Pass rate ${Math.round(rate * 100)}%` };
}

// "line 12" or "line 12:" references in compiler output become jump targets.
export function findLineReferences(text) {
  const out = [];
  const seen = new Set();
  const re = /\bline\s+(\d+)/gi;
  let match;
  while ((match = re.exec(String(text || ""))) !== null) {
    const line = Number(match[1]);
    if (line > 0 && !seen.has(line)) {
      seen.add(line);
      out.push(line);
    }
  }
  return out;
}

// Character offset of the first column of a 1-based line, for caret moves.
export function offsetOfLine(source, line) {
  const lines = String(source || "").split("\n");
  const index = Math.min(Math.max(line, 1), lines.length) - 1;
  let offset = 0;
  for (let i = 0; i < index; i += 1) offset += lines[i].length + 1;
  return offset;
}

export function parseLogEvent(data) {
  try {
    const value = JSON.parse(data);
    return typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    return String(data);
  }
}

export const LOG_LIMIT = 2000;

export function appendLines(lines, more, limit = LOG_LIMIT) {
  const next = lines.concat(more);
  return next.length > limit ? next.slice(next.length - limit) : next;
}

// What the header says about where an edit will save to.
export function saveTargetText(parentRev, latest) {
  if (!parentRev) return "This design has no revision yet.";
  if (!latest || latest.rev_id === parentRev.rev_id) return `Saves as revision ${parentRev.seq + 1} from ${parentRev.seq}.`;
  return `Saves as a branch of revision ${parentRev.seq} (the latest is ${latest.seq}).`;
}

export function originText(origin) {
  if (!origin) return "Unknown origin";
  if (origin.kind === "trial") return `Trial ${origin.trial_id} by ${origin.model_id} in run ${origin.run_id}`;
  if (origin.kind === "master") return `Master ${origin.file} of ${origin.instrument_id}`;
  if (origin.kind === "blank") return `Blank ${origin.instrument_id || "design"}`;
  return String(origin.kind);
}
