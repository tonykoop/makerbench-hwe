// Pure rules for the Agreement analytics and Compare screens, kept free of the
// DOM so tests/js/analytics_lib.test.mjs can pin them.

// Tony (rebuild Q5): an Elo from fewer than five games is marked provisional.
// UI-only: the API's ratings are unchanged.
export const PROVISIONAL_GAMES = 5;

// The same threshold as analytics.find_outliers.
export const OUTLIER_RANK_GAP = 2;

// The same rule as service._SAFE_ID_RE, which export_winners applies.
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

export function isProvisional(row) {
  return Number(row?.games || 0) < PROVISIONAL_GAMES;
}

export const INTERPRETATION_TEXT = {
  strong_alignment: "People and the objective checks rank entrants much the same way",
  inverted_ranking: "People and the objective checks rank entrants in opposite order",
  weak_or_mixed_alignment: "Weak or mixed agreement",
  insufficient_overlap: "Too few entrants have both a human rating and objective checks",
  insufficient_variance: "No variation in the scores to compare",
};

export function interpretationText(code) {
  return INTERPRETATION_TEXT[code] || "No reading";
}

export function formatRho(rho) {
  return typeof rho === "number" ? `ρ = ${rho.toFixed(2)}` : "ρ not available";
}

export function formatElo(value) {
  return typeof value === "number" ? String(Math.round(value)) : "Unknown";
}

export function formatPassRate(value) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "Unknown";
}

export function formatRank(value) {
  return typeof value === "number" ? `#${Number.isInteger(value) ? value : value.toFixed(1)}` : "Unranked";
}

export function findOutliers(rankings, gap = OUTLIER_RANK_GAP) {
  return (rankings || []).filter(
    (row) => typeof row.rank_delta === "number" && Math.abs(row.rank_delta) >= gap,
  );
}

// One scale for every interval bar, so intervals compare row to row.
export function ratingScale(rows, { pad = 10 } = {}) {
  const values = [];
  for (const row of rows || []) {
    for (const value of [row.rating, row.ci_low, row.ci_high]) {
      if (typeof value === "number") values.push(value);
    }
  }
  if (values.length === 0) return null;
  const lo = Math.min(...values) - pad;
  const hi = Math.max(...values) + pad;
  return { lo, hi, x: (value) => ((value - lo) / (hi - lo)) * 100 };
}

// Scatter positions in percent of the plot box: x is the pass rate, and y puts
// the highest Elo at the top.
export function scatterPoints(rankings) {
  const rows = (rankings || []).filter(
    (row) => typeof row.subjective_elo === "number" && typeof row.objective_pass_rate === "number",
  );
  if (rows.length === 0) return { points: [], eloRange: null };
  const scale = ratingScale(
    rows.map((row) => ({ rating: row.subjective_elo })),
    { pad: 20 },
  );
  return {
    eloRange: [scale.lo, scale.hi],
    points: rows.map((row) => ({
      entrant: row.entrant,
      elo: row.subjective_elo,
      passRate: row.objective_pass_rate,
      x: Math.min(Math.max(row.objective_pass_rate, 0), 1) * 100,
      y: 100 - scale.x(row.subjective_elo),
    })),
  };
}

export function pointLabel(point) {
  return `${point.entrant}: Elo ${formatElo(point.elo)}, pass rate ${formatPassRate(point.passRate)}`;
}

// What export_winners will overwrite: one winner.scad per instrument with
// trials. Ids the server refuses are listed separately; it has the final say.
export function exportTargets(instrumentIds) {
  const targets = [];
  const skipped = [];
  for (const id of new Set((instrumentIds || []).filter(Boolean).map(String))) {
    if (SAFE_ID.test(id)) targets.push({ instrument: id, path: `instruments/${id}/winner.scad` });
    else skipped.push(id);
  }
  return { targets, skipped };
}

export function runInstruments(summary) {
  if (Array.isArray(summary?.trials)) return summary.trials.map((trial) => trial?.instrument_id);
  return summary?.instruments || [];
}

export function sharedEntrants(boardA, boardB) {
  const names = (board) => new Set((board?.leaderboard || []).map((row) => row.entrant));
  const b = names(boardB);
  return [...names(boardA)].filter((entrant) => b.has(entrant)).sort();
}
