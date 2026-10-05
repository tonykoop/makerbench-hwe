// Text for build-check (gate) values. #1011: a robust-v1 min_wall on the 1st-percentile cliff is
// "borderline": neither a pass nor a fail, and excluded from the pass rate, so it never reads as
// "Fail" and never counts toward "of 6".

export const BORDERLINE = "borderline";

export function gateLabel(value) {
  if (value == null) return "Not recorded";
  if (value === BORDERLINE) return "Borderline";
  if (typeof value !== "number" || !Number.isFinite(value)) return "Not recorded";
  return value >= 1 ? "Pass" : "Fail";
}

function borderlineCount(row) {
  const trials = row.trials || [];
  const values = trials.length ? trials.flatMap((trial) => Object.values(trial.gates || {}))
    : Object.values(row.gates || {});
  return values.filter((value) => value === BORDERLINE).length;
}

// The one-line result of a demo row: one run, or several runs averaged.
export function resultText(row) {
  if (row.objective_pass_rate == null) return "Build checks were not completed.";
  const borderline = borderlineCount(row);
  const note = borderline ? ` (${borderline} borderline ${borderline === 1 ? "check" : "checks"} excluded)` : "";
  if (row.objective_pass_rate === 1 && row.n_objective_trials != null && !borderline) {
    const n = row.n_objective_trials;
    return `Passed all 6 build checks in ${n} of ${n} ${n === 1 ? "run" : "runs"}.`;
  }
  const gates = Object.values(row.gates || {});
  if (gates.length === 6 && (row.trials || []).length <= 1) {
    const decided = gates.filter((value) => value !== BORDERLINE);
    const passed = decided.filter((value) => value >= 1).length;
    const suffix = row.n_objective_trials === 1 ? " in this run" : "";
    if (!borderline) return `Passed ${passed} of 6 build checks${suffix}.`;
    return `Passed ${passed} of ${decided.length} decided build checks${suffix}${note}.`;
  }
  if (!borderline && row.n_objective_trials === 1
      && Math.abs(row.objective_pass_rate * 6 - Math.round(row.objective_pass_rate * 6)) < 0.00001) {
    return `Passed ${Math.round(row.objective_pass_rate * 6)} of 6 build checks in this run.`;
  }
  return `Build-check average: ${(row.objective_pass_rate * 100).toFixed(2)}%`
    + `${row.n_objective_trials != null ? ` across ${row.n_objective_trials} runs` : ""}${note}.`;
}
