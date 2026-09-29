// Pure rules for the DoE matrix screen, kept free of the DOM so
// tests/js/doe_lib.test.mjs can pin them.

import { describeReference, joinNames, RUN_ID_PATTERN } from "./launch.js";

export const BACKENDS = ["openscad", "cadquery", "build123d", "blender", "solidworks", "fusion", "solidworks-live", "fusion-live"];

export const LEVELS = ["L1", "L2", "L3", "L4"];

export const CONTEXT_TIERS = [
  { id: "blind", label: "Blind: the written brief only" },
  { id: "image", label: "Image: the approved reference image" },
];

// The preview annotates every cell; past this size the matrix is almost
// certainly a mistake, and the page would stall rendering it.
export const MAX_PREVIEW_CELLS = 2000;

export function parseSeeds(text) {
  const seeds = new Set();
  const invalid = [];
  for (const raw of String(text || "").split(/[\s,]+/)) {
    const token = raw.trim();
    if (!token) continue;
    if (/^\d+$/.test(token)) seeds.add(Number(token));
    else invalid.push(token);
  }
  return { seeds: [...seeds].sort((a, b) => a - b), invalid };
}

export function entrantsPerJob(matrix) {
  const { models, levels } = matrix;
  return levels.length * (matrix.backends || ["openscad"]).reduce((n, backend) =>
    n + (backend.endsWith("-live") ? (matrix.driver_models || []).length : models.length), 0);
}

export function cellCount(matrix) {
  return matrix.instruments.length * matrix.tiers.length * matrix.seeds.length * entrantsPerJob(matrix);
}

export const MATCHUP_AXES = ["backends", "models", "levels", "context_tiers", "seeds", "instruments", "driver_models"];

export function axisValues(matrix, axis) {
  return matrix[axis === "context_tiers" ? "tiers" : axis] || [];
}

export function matchupBlockers(matrix) {
  if (!matrix.varied_axis) return [];
  const blockers = [];
  if (axisValues(matrix, matrix.varied_axis).length < 2) blockers.push("Choose at least two values for the axis you vary.");
  const others = MATCHUP_AXES.filter((axis) => axis !== matrix.varied_axis && axisValues(matrix, axis).length > 1);
  if (others.length && !matrix.factorial) blockers.push(`Hold ${others.join(", ")} constant, or enable factorial.`);
  return blockers;
}

// null when there is nothing sensible to preview yet.
export function previewQuery(matrix) {
  const { instruments, models, levels, tiers, seeds } = matrix;
  if (![instruments, models, levels, tiers, seeds, matrix.backends || ["openscad"]].every((list) => list.length > 0)) return null;
  if (cellCount(matrix) > MAX_PREVIEW_CELLS || matchupBlockers(matrix).length) return null;
  return new URLSearchParams({
    instruments: instruments.join(","),
    models: models.join(","),
    levels: levels.join(","),
    context_tiers: tiers.join(","),
    seeds: seeds.join(","),
    backends: (matrix.backends || ["openscad"]).join(","),
    ...(matrix.varied_axis ? { varied_axis: matrix.varied_axis, values: axisValues(matrix, matrix.varied_axis).join(","), factorial: String(Boolean(matrix.factorial)) } : {}),
    ...(matrix.driver_models?.length ? { driver_models: matrix.driver_models.join(",") } : {}),
  }).toString();
}

// The same job grouping as doe.build_nightly_queue: one nightly job per
// (instrument, seed, context tier). A job with any unknown-cost cell is never
// counted as affordable, whatever the budget.
export function budgetView(cells, budgetUsd) {
  const jobs = new Map();
  for (const cell of cells || []) {
    const key = `${cell.instrument_id}|${cell.seed}|${cell.context_tier}`;
    if (!jobs.has(key)) {
      jobs.set(key, {
        instrument: cell.instrument_id,
        seed: cell.seed,
        tier: cell.context_tier,
        cells: 0,
        knownUsd: 0,
        unknownModels: new Set(),
      });
    }
    const job = jobs.get(key);
    job.cells += 1;
    const cost = cell.estimate?.cost_usd;
    if (typeof cost === "number") job.knownUsd += cost;
    else job.unknownModels.add(cell.model_id);
  }
  const list = [...jobs.values()].map((job) => {
    const unknownModels = [...job.unknownModels].sort();
    let status = "within";
    if (unknownModels.length) status = "unknown";
    else if (job.knownUsd > budgetUsd + 1e-9) status = "over";
    return { ...job, knownUsd: Math.round(job.knownUsd * 1e6) / 1e6, unknownModels, status };
  });
  const count = (status) => list.filter((job) => job.status === status).length;
  return { jobs: list, within: count("within"), over: count("over"), unknown: count("unknown") };
}

export function unknownCostModels(cells) {
  const models = new Set();
  for (const cell of cells || []) {
    if (typeof cell.estimate?.cost_usd !== "number") models.add(cell.model_id);
  }
  return [...models].sort();
}

// A ceiling must be a positive dollar amount: zero would mean "no cap" to the
// nightly budget guard, the opposite of what a ceiling is for.
export function missingCeilings(models, ceilings) {
  return models.filter((model) => !(Number(ceilings?.[model]) > 0));
}

export function ceilingsPayload(models, ceilings) {
  const entries = models
    .filter((model) => Number(ceilings?.[model]) > 0)
    .map((model) => [model, Number(ceilings[model])]);
  return entries.length ? Object.fromEntries(entries) : undefined;
}

export const SKIP_REASON_TEXT = {
  reference_image_not_approved: "Reference image not approved",
  no_reference_image_on_disk: "No reference image on disk",
  fewer_than_two_entrants: "Fewer than two entrants",
  checking: "Checking the reference image…",
  check_failed: "Couldn't check the reference image",
};

export function skipReasonText(reason) {
  return SKIP_REASON_TEXT[reason] || String(reason || "Unknown reason");
}

// What the server will skip, predicted in its own order: an unapproved
// reference first (approval needs an image, so "no image" is also unapproved),
// then single-entrant jobs.
export function predictedSkips(instruments, references, entrantsPerJob) {
  const skips = [];
  for (const instrument of instruments) {
    const { kind } = describeReference(references[instrument]);
    if (kind === "unchecked") skips.push({ instrument, reason: "checking" });
    else if (kind === "error") skips.push({ instrument, reason: "check_failed" });
    else if (kind !== "approved") skips.push({ instrument, reason: "reference_image_not_approved" });
    else if (entrantsPerJob < 2) skips.push({ instrument, reason: "fewer_than_two_entrants" });
  }
  return skips;
}

export function doeBlockers({ matrix, invalidSeeds, runId, budget, preview, unknownModels, ceilings }) {
  const { instruments, models, levels, tiers, seeds } = matrix;
  const blockers = [...matchupBlockers(matrix)];
  if (instruments.length === 0) blockers.push("Choose at least one instrument.");
  if (models.length === 0) blockers.push("List at least one entrant.");
  else if (entrantsPerJob(matrix) < 2) {
    blockers.push("Each nightly job needs at least two entrants: choose two models, levels, backends or live drivers.");
  }
  if (levels.length === 0) blockers.push("Choose at least one level.");
  if (matrix.backends?.length === 0) blockers.push("Choose at least one backend.");
  if (matrix.backends?.some((backend) => backend.endsWith("-live")) && matrix.driver_models?.length === 0) blockers.push("Choose a live driver model.");
  if (tiers.length === 0) blockers.push("Choose at least one context tier.");
  if (invalidSeeds.length) blockers.push(`Seeds must be whole numbers, not ${joinNames(invalidSeeds)}.`);
  else if (seeds.length === 0) blockers.push("Add at least one seed.");
  const cells = cellCount(matrix);
  if (cells > MAX_PREVIEW_CELLS) {
    blockers.push(`This matrix has ${cells} cells. Narrow it to ${MAX_PREVIEW_CELLS} or fewer.`);
  }
  if (!runId) blockers.push("Name the run this queue is for.");
  else if (!RUN_ID_PATTERN.test(runId)) {
    blockers.push("Use letters, digits, dots, dashes or underscores for the run name, starting with a letter or digit.");
  }
  if (!(budget !== "" && Number(budget) >= 0)) blockers.push("Budget per job must be zero or more dollars.");
  if (blockers.length === 0) {
    if (preview === "loading") blockers.push("Waiting for the cost preview…");
    else if (preview === "error") blockers.push("The cost preview failed. Fix that before writing the queue.");
  }
  const missing = missingCeilings(unknownModels, ceilings);
  if (missing.length) {
    blockers.push(`Set a cost ceiling for ${joinNames(missing)}. Studio never treats an unknown cost as free.`);
  }
  return blockers;
}

export function formatUsd(value) {
  return typeof value === "number" ? `$${value.toFixed(2)}` : "Unknown";
}

export function formatDuration(seconds) {
  if (typeof seconds !== "number") return "Unknown";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}
