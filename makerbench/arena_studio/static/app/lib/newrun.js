// Control Room New Run (#1114): the pure rules behind the form. The server owns which
// cells can run and every number in the estimate; this only shapes the form.

export const PRESETS = {
  matchup: {
    label: "Overnight matchup",
    deliverable: "single",
    entrants: ["cadam:fable", "freecad:opus", "solidworks:sol", "fusion:sonnet"],
    turns: 5,
  },
  sweep: { label: "Variant sweep", deliverable: "single", entrants: ["freecad:opus", "cadam:sonnet"], turns: 4 },
  assembly: { label: "Assembly build", deliverable: "assembly", entrants: ["freecad:opus", "solidworks:sol"], turns: 6 },
};

export const MAX_REFERENCE_BYTES = 10 * 1024 * 1024;

export function freshForm(catalog, presetId = "matchup", instrumentId = null) {
  const preset = PRESETS[presetId] || PRESETS.matchup;
  const instrument =
    catalog.instruments.find((item) => item.id === instrumentId) ||
    catalog.instruments.find((item) => (preset.deliverable === "assembly") === item.assembly) ||
    catalog.instruments[0] ||
    null;
  return {
    preset: presetId,
    instrument: instrument?.id || "",
    variants: [],
    deliverable: preset.deliverable,
    entrants: preset.entrants.filter((pick) => cellReason(catalog, pick) === null),
    brief: instrument?.brief || "",
    references: [],
    turns: preset.turns,
    cap_usd: 2,
    stop_early: true,
  };
}

// Why a tool:model cell cannot run, or null.
export function cellReason(catalog, pick) {
  const [toolId, modelId] = pick.split(":");
  const tool = catalog.tools.find((item) => item.id === toolId);
  if (!tool || !(modelId in tool.cells)) return "Unknown entrant";
  return tool.cells[modelId];
}

export function togglePick(form, pick) {
  const on = form.entrants.includes(pick);
  return { ...form, preset: null, entrants: on ? form.entrants.filter((p) => p !== pick) : [...form.entrants, pick] };
}

export function chooseInstrument(form, catalog, id) {
  if (form.instrument === id) return form;
  const instrument = catalog.instruments.find((item) => item.id === id);
  return {
    ...form,
    preset: null,
    instrument: id,
    variants: [],
    deliverable: instrument?.assembly ? "assembly" : form.deliverable,
    brief: instrument?.brief || "",
    references: [],
  };
}

export function parseVariants(text) {
  return [...new Set(String(text).split(",").map((v) => v.trim()).filter(Boolean))].slice(0, 12);
}

export function clampTurns(value, max = 8) {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? Math.min(max, Math.max(1, n)) : 1;
}

// The request body the server validates. Reference images travel as data URLs.
export function payload(form) {
  return {
    instrument: form.instrument,
    variants: form.variants,
    deliverable: form.deliverable,
    entrants: form.entrants,
    brief: form.brief,
    references: form.references.map((ref) => ({ name: ref.name, data: ref.data })),
    turns: form.turns,
    cap_usd: Number(form.cap_usd) || 0,
    stop_early: form.stop_early,
  };
}

export function blockers(form, estimate) {
  const out = [];
  if (!form.instrument) out.push("Choose an instrument in step 1.");
  if (form.entrants.length < 2) out.push("Pick at least two entrants in step 3: the nightly runner compares designs.");
  if (!form.brief.trim()) out.push("Write a brief in step 4.");
  if (estimate?.over_cap) out.push(`The estimate is over your $${Number(form.cap_usd).toFixed(2)} cap; entrants stop when it is reached.`);
  return out;
}

export function warnings(form, catalog, estimate) {
  const out = [];
  if (form.deliverable === "assembly" && form.entrants.some((pick) => pick.startsWith("cadam"))) {
    out.push("CADAM (OpenSCAD) delivers parts as modules, not a native assembly.");
  }
  if (form.deliverable === "assembly") {
    out.push("The nightly runner does not grade wood assemblies yet; the plan records the mode for when it does.");
  }
  if (estimate && !estimate.openrouter_complete && estimate.unknown_cost.length) {
    out.push("No spend history for some OpenRouter entrants, so the spend is a lower bound.");
  }
  const seats = form.entrants.filter((pick) => catalog.tools.find((t) => t.id === pick.split(":")[0])?.seat);
  if (seats.length > 1) out.push("SolidWorks and Fusion share one CAD seat, so they run one after the other.");
  return out;
}

export function usd(value) {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : "unknown";
}

export function duration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "unknown";
  const minutes = Math.max(1, Math.round(seconds / 60));
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes} min`;
}
