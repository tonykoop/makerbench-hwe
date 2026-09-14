// Pure rules for the Launch & preflight screen, kept free of the DOM so
// tests/js/launch_lib.test.mjs can pin them.

// The same rule the server applies (ArenaStudioService.launch_competition).
export const RUN_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

export const LOG_LIMIT = 500;

export function parseModelList(text) {
  const seen = new Set();
  const models = [];
  for (const raw of String(text || "").split(/[\s,]+/)) {
    const id = raw.trim();
    if (id && !seen.has(id)) {
      seen.add(id);
      models.push(id);
    }
  }
  return models;
}

export function joinNames(names, max = 3) {
  const shown = names.slice(0, max).join(", ");
  return names.length > max ? `${shown} and ${names.length - max} more` : shown;
}

// `reference` is a resource state: undefined (never requested), or
// { status: "loading" | "ready" | "error", data, error }.
export function describeReference(reference) {
  if (!reference) return { kind: "unchecked", label: "Not checked" };
  if (reference.status === "loading") return { kind: "unchecked", label: "Checking…" };
  if (reference.status === "error") return { kind: "error", label: "Couldn't check" };
  if (reference.data?.approved) return { kind: "approved", label: "Approved" };
  if (reference.data?.has_image) return { kind: "review", label: "Needs review" };
  return { kind: "missing", label: "No image" };
}

// Everything that stops a launch, in the order a person should fix it. The
// server enforces the same gate; this only explains it before anyone clicks.
export function launchBlockers({ instruments, models, tier, references, runId, seed }) {
  const blockers = [];
  if (instruments.length === 0) blockers.push("Choose at least one instrument.");
  if (models.length === 0) blockers.push("List at least one entrant.");
  if (runId && !RUN_ID_PATTERN.test(runId)) {
    blockers.push(
      "Use letters, digits, dots, dashes or underscores for the run name, starting with a letter or digit.",
    );
  }
  if (!/^\d+$/.test(String(seed))) blockers.push("Seed must be a whole number.");
  if (tier === "image") {
    const state = (id) => describeReference(references[id]).kind;
    const unchecked = instruments.filter((id) => state(id) === "unchecked");
    const failed = instruments.filter((id) => state(id) === "error");
    const unapproved = instruments.filter((id) => ["review", "missing"].includes(state(id)));
    if (unchecked.length) blockers.push("Checking reference images…");
    if (failed.length) blockers.push(`Couldn't check the reference image for ${joinNames(failed)}.`);
    if (unapproved.length) {
      blockers.push(
        `Approve the reference image for ${joinNames(unapproved)}, or switch to the blind context tier.`,
      );
    }
  }
  return blockers;
}

// A DoE estimate says how one more live trial of a model is paid for. Dry runs
// use the zero-token stub generator, so this only matters for live runs.
export function costBadge(estimate) {
  const source = String(estimate?.cost_source || "unknown");
  if (source === "subscription_zero_marginal") {
    return { kind: "subscription", label: "$0 subscription" };
  }
  if (source.startsWith("telemetry_average") && typeof estimate.cost_usd === "number") {
    return { kind: "metered", label: `Metered, about $${estimate.cost_usd.toFixed(2)} a trial` };
  }
  return { kind: "unknown", label: "Cost unknown" };
}

export function launchErrorMessage(error) {
  if (error?.status === 403) {
    return "This Studio server was started without --allow-live, so it only starts dry runs with the zero-token stub. Restart Studio with --allow-live to run live entrants.";
  }
  return error?.message || "The launch failed.";
}

// Each SSE event's data is one JSON-encoded log line.
export function parseLogEvent(data) {
  try {
    const value = JSON.parse(data);
    return typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    return String(data);
  }
}

export function appendLines(lines, more, limit = LOG_LIMIT) {
  const next = lines.concat(more);
  return next.length > limit ? next.slice(next.length - limit) : next;
}

export const JOB_STATUS_TEXT = {
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  interrupted: "Stopped before finishing",
  not_found: "Not found",
};

export function jobStatusText(status) {
  return JOB_STATUS_TEXT[status] || String(status || "Unknown");
}

export const SECRET_STATUS_TEXT = {
  PRESENT: "Present",
  MISSING: "Missing",
  PLACEHOLDER: "Placeholder value",
};

export function lockText(lock) {
  if (!lock?.status) return "Unknown";
  const pid = lock.pid ? ` (process ${lock.pid})` : "";
  if (lock.status === "ABSENT") return "Free: no nightly run holds the lock";
  if (lock.status === "ACTIVE") return `Held by a running nightly${pid}`;
  if (lock.status === "STALE") return `Stale: left by a process that is no longer running${pid}`;
  return String(lock.status);
}
