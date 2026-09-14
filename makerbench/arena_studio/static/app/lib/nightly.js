// Pure rules for the Nightly cockpit and Morning review screens, kept free of
// the DOM so tests/js/nightly_lib.test.mjs can pin them.

export const LEASE_TEXT = {
  ACTIVE: "A nightly run holds the lease",
  STALE: "Stale lease: the process that took it is gone",
  UNREADABLE: "The lease file can't be read",
  ABSENT: "No lease: no nightly run is active",
};

export function leaseText(lease) {
  return LEASE_TEXT[lease?.status] || `Lease status: ${lease?.status ?? "unknown"}`;
}

export function leaseKind(lease) {
  if (lease?.status === "ACTIVE") return "ok";
  if (lease?.status === "ABSENT") return "idle";
  return "problem";
}

export function formatAge(seconds) {
  if (typeof seconds !== "number") return "No heartbeat recorded";
  if (seconds < 60) return `${Math.round(seconds)} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

export const NIGHTLY_STATUS_TEXT = {
  queued: "Queued",
  running: "Running",
  votable: "Ready for morning review",
  completed: "Completed",
  failed: "Failed",
  halted: "Halted",
};

export function nightlyStatusText(status) {
  return NIGHTLY_STATUS_TEXT[status] || String(status || "Unknown");
}

export function formatUsd(value) {
  return typeof value === "number" ? `$${value.toFixed(2)}` : "Unknown";
}

// The budget replayed from a job's run directory; null until the job has one.
export function budgetSummary(job) {
  if (!job?.budget) return { kind: "none", text: "Not started" };
  const { spent_usd: spent, halted_reason: halted } = job.budget;
  const text = `${formatUsd(spent)} of ${formatUsd(job.budget_usd)} spent`;
  if (halted) return { kind: "halted", text };
  if (typeof spent === "number" && typeof job.budget_usd === "number" && spent > job.budget_usd) {
    return { kind: "over", text };
  }
  return { kind: "ok", text };
}

// What a person should notice about a job, in plain words.
export function jobNotes(job) {
  const notes = [];
  if (job?.orphaned) {
    notes.push("Marked running, but no nightly run holds the lease, so it stalled. The next nightly run resumes it.");
  }
  if (job?.budget?.halted_reason) notes.push(`Halted: ${job.budget.halted_reason}`);
  const violations = (job?.budget?.outcomes || []).filter((outcome) => outcome.violation);
  for (const outcome of violations) notes.push(`${outcome.entrant_id}: ${outcome.violation}`);
  return notes;
}

export function shouldPoll(view) {
  return view?.lease?.status === "ACTIVE" || (view?.jobs || []).some((job) => job.status === "running");
}
