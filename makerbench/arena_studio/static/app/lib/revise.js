// Pure helpers for the workbench Revise tab (#788 W6). No DOM, no fetch:
// unit-tested under node in tests/js/revise_lib.test.mjs.

export const FEEDBACK_LIMIT = 8 * 1024;

// The picker's option text: the label plus the honest availability state,
// exactly as the API reports it.
export function entrantOptionText(entrant) {
  if (!entrant) return "";
  const state = entrant.allowed ? entrant.note || "" : entrant.reason || "unavailable";
  return state ? `${entrant.label} — ${state}` : entrant.label;
}

// What the in-page confirm says before anything runs (plan §6: "This calls
// <model> on your subscription"); the stub says so instead of claiming a call.
export function confirmText(entrant) {
  if (!entrant) return "";
  if (!entrant.live) return `This runs ${entrant.label}: no model is called and nothing is spent.`;
  return `This calls ${entrant.label} on your subscription (${entrant.note}). Up to 40 turns; you can cancel while it runs.`;
}

export function feedbackBytes(text) {
  return new TextEncoder().encode(String(text || "")).length;
}

// Can Start be pressed? Returns { ok, reason }.
export function canStart({ entrant, feedback, revId, running }) {
  if (running) return { ok: false, reason: "A job is already running." };
  if (!revId) return { ok: false, reason: "Save the origin revision first; a model revises a saved revision." };
  if (!entrant) return { ok: false, reason: "Choose an entrant." };
  if (!entrant.allowed) return { ok: false, reason: entrant.reason || `${entrant.label} is unavailable.` };
  if (!String(feedback || "").trim()) return { ok: false, reason: "Say what to change." };
  if (feedbackBytes(feedback) > FEEDBACK_LIMIT) return { ok: false, reason: `Feedback is longer than ${FEEDBACK_LIMIT} bytes.` };
  return { ok: true, reason: "" };
}

export const CONFINEMENT_TEXT = {
  verified: "ran inside the entrant sandbox (verified from the launch)",
  "restricted-tools": "ran with read-only tools confined to the workspace",
  not_applicable: "no model ran (stub)",
  unconfined: "confinement not verified: the answer was discarded",
};

export function confinementText(value) {
  return CONFINEMENT_TEXT[value] || `confinement ${String(value || "unknown")}`;
}

// Provenance line for a model draft or revision.
export function modelProvenanceText(editor) {
  if (!editor || editor.kind !== "model") return "";
  return `Revised by ${editor.model_id} (${editor.provider}); ${confinementText(editor.confinement)}.`;
}
