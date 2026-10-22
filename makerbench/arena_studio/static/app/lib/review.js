// Control Room Morning Review (#1105): the pure rules behind the ranked card stack.
// The server sends each design's checks already sorted into pass/fail/unknown, so nothing
// here reads a recorded objective.

const DRAFT_PREFIX = "control-room.review-draft.";

export function initialState(view, draft = null) {
  const ids = view.designs.map((design) => design.trial_id);
  const sameSet = (order) => Array.isArray(order) && order.length === ids.length && ids.every((id) => order.includes(id));
  const saved = view.saved_review;
  if (draft && sameSet(draft.order)) {
    return { order: [...draft.order], notes: { ...(draft.notes || {}) }, revise: { ...(draft.revise || {}) } };
  }
  if (saved && sameSet(saved.ranking)) {
    const revise = Object.fromEntries((saved.revision_requests || []).map((request) => [request.trial_id, true]));
    return { order: [...saved.ranking], notes: { ...(saved.notes || {}) }, revise };
  }
  return { order: [...view.grader_order], notes: {}, revise: {} };
}

export function move(order, id, delta) {
  const from = order.indexOf(id);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= order.length) return order;
  const next = [...order];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

// Drop `dragged` onto `target`: it takes the target's place, pushing the target down
// when moving up and up when moving down.
export function dropOn(order, dragged, target) {
  if (dragged === target || !order.includes(dragged) || !order.includes(target)) return order;
  const movingDown = order.indexOf(dragged) < order.indexOf(target);
  const rest = order.filter((id) => id !== dragged);
  rest.splice(rest.indexOf(target) + (movingDown ? 1 : 0), 0, dragged);
  return rest;
}

export function hasNote(state, id) {
  return Boolean((state.notes[id] || "").trim());
}

// Turning revision on needs a note; turning it off never does.
export function toggleRevise(state, id) {
  if (!state.revise[id] && !hasNote(state, id)) return { state, error: "Write a note first so the model knows what to change." };
  return { state: { ...state, revise: { ...state.revise, [id]: !state.revise[id] } }, error: null };
}

export function setNote(state, id, text) {
  const next = { ...state, notes: { ...state.notes, [id]: text } };
  // A request cannot outlive its note.
  if (!text.trim() && next.revise[id]) next.revise = { ...next.revise, [id]: false };
  return next;
}

export function payload(state, { reviewer = "tony", extraTurns = 3 } = {}) {
  return {
    ranking: [...state.order],
    notes: Object.fromEntries(Object.entries(state.notes).filter(([, text]) => text && text.trim())),
    revise: state.order.filter((id) => state.revise[id]),
    extra_turns: extraTurns,
    reviewer,
  };
}

export function trayCounts(state) {
  return {
    notes: Object.values(state.notes).filter((text) => text && text.trim()).length,
    revise: state.order.filter((id) => state.revise[id]).length,
  };
}

export function costText(cost) {
  if (cost?.kind === "usd") return `$${Number(cost.usd).toFixed(2)}`;
  if (cost?.kind === "subscription") return "subscription";
  return "not recorded";
}

export function minutesText(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "not recorded";
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

export function turnKind(turn) {
  if (turn.passed >= turn.total) return "pass";
  return turn.passed >= Math.ceil(turn.total / 2) ? "partial" : "fail";
}

export function nightLabel(night) {
  const when = night.night ? new Date(night.night) : null;
  const day = when && !Number.isNaN(when.getTime())
    ? when.toLocaleDateString(undefined, { weekday: "short", day: "numeric", timeZone: "UTC" })
    : null;
  return day ? `${day} · ${night.title}` : night.title;
}

export function draftKey(runId) {
  return DRAFT_PREFIX + runId;
}

export function loadDraft(runId, storage = globalThis.localStorage) {
  try {
    const parsed = JSON.parse(storage?.getItem(draftKey(runId)) || "null");
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

export function saveDraft(runId, state, storage = globalThis.localStorage) {
  try {
    storage?.setItem(draftKey(runId), JSON.stringify(state));
  } catch {
    // Not kept; the review still saves to the run folder.
  }
}

export function clearDraft(runId, storage = globalThis.localStorage) {
  try {
    storage?.removeItem(draftKey(runId));
  } catch {
    // Nothing to clear.
  }
}

// --- A blocked night (#1158): the runner's verdict, shown honestly. ---

const NIGHT_STATUS = {
  votable: "votable",
  partial: "partial",
  blocked: "blocked",
  running: "running",
};

export function nightStatusLabel(status) {
  return NIGHT_STATUS[status] || null;
}

// The banner headline for a night that is not fully votable; null when every design is in.
export function statusHeadline(summary) {
  const failed = (summary?.failed_entrants || []).length;
  const total = summary?.designs ?? 0;
  if (summary?.status === "blocked") {
    return total > 0 && failed >= total
      ? "This night is blocked: nothing to vote on."
      : `This night is blocked: ${plural(failed, "entrant")} of ${total} failed.`;
  }
  if (summary?.status === "partial") return `This night is only partly votable: ${plural(failed, "entrant")} of ${total} failed.`;
  if (summary?.status === "running") return "This night is still running.";
  return null;
}

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function usd(value) {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : null;
}

export function spendText(spend) {
  const spent = usd(spend?.spent_usd);
  if (!spent) return "Spend not recorded.";
  const parts = [`${spent} spent`];
  const limit = usd(spend?.limit_usd);
  if (limit) parts[0] += ` of ${limit} cap`;
  const reserved = usd(spend?.reserved_usd);
  if (reserved && spend.reserved_usd > 0) parts.push(`${reserved} still reserved`);
  if (spend?.halted_reason) parts.push(`halted: ${spend.halted_reason}`);
  return `${parts.join(" · ")}.`;
}

// Which cards get "Retry this entrant": every entrant the night lists as failed.
export function failedIds(summary) {
  return new Set((summary?.failed_entrants || []).map((entry) => entry.trial_id));
}

// The toast after a retry, from the server's answer and nothing else.
export function retryResultText(result, design) {
  const note = result?.runner?.note ? ` ${result.runner.note}` : "";
  if (result?.queued) return `Queued ${result.job.job_id} for ${design.name} in the Control Room revision queue.${note}`;
  if (result?.queue?.skipped) return `A runner already took ${result.job.job_id}; nothing was changed.`;
  return `Queue writes are off: the retry for ${design.name} is kept as a draft in ${result?.draft || "the run folder"}.${note}`;
}
