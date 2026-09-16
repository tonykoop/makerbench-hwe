// Pure helpers for the workbench Parameters tab (#788 W5). No DOM, no fetch:
// unit-tested under node in tests/js/params_lib.test.mjs. The extraction
// itself is W1's `cad_params` behind the API; this file only groups, labels,
// validates typed input and summarises changes, mirroring W1's rules so a
// refusal is shown inline before the server sees it (the server re-validates).

export const DEFAULT_GROUP = "Parameters";
export const ADVANCED_GROUP = "Advanced";

// Fields grouped by Customizer group in source order; `$fn`-style specials
// with no group of their own land under "Advanced" (plan §5).
export function groupParameters(model) {
  const groups = [];
  const byName = new Map();
  for (const param of model?.parameters || []) {
    const name = param.group || (param.state === "special" ? ADVANCED_GROUP : DEFAULT_GROUP);
    let group = byName.get(name);
    if (!group) {
      group = { name, params: [] };
      byName.set(name, group);
      groups.push(group);
    }
    group.params.push(param);
  }
  return groups;
}

export function unitText(param) {
  return param?.unit ? param.unit : "unit unknown";
}

// The honest state line, verbatim from the API's notes, plus the state word
// for anything that cannot be edited here.
export function stateText(param) {
  const notes = [...(param?.notes || [])];
  if (param?.state === "reassigned" && !notes.some((n) => n.includes("assigned more than once"))) {
    notes.unshift("assigned more than once, edit it in the code");
  }
  if (param?.state === "derived" && !notes.some((n) => n.includes("derived"))) {
    notes.unshift("derived, edit it in the code");
  }
  return notes.join("; ");
}

export function rangeText(param) {
  const range = param?.range;
  if (range) {
    const step = range.step != null ? `, step ${formatValue(range.step)}` : "";
    return `${formatValue(range.min)} to ${formatValue(range.max)}${step}`;
  }
  if (param?.options && param.options.length) return `one of ${param.options.map(formatValue).join(", ")}`;
  return "";
}

// The registry envelope as context next to size-like (mm) parameters, never
// as a slider bound (plan Q7).
export function envelopeHint(param, envelopeMm) {
  if (!Array.isArray(envelopeMm) || envelopeMm.length !== 3) return "";
  if (param?.unit !== "mm" || param.kind !== "number") return "";
  return `Envelope ${envelopeMm.map(formatValue).join(" × ")} mm`;
}

export function formatValue(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return `[${value.map(formatValue).join(", ")}]`;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(value);
  return String(value);
}

function parseNumberText(text) {
  const trimmed = String(text ?? "").trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

// Typed input for one parameter -> { ok: true, value } or { ok: false, error }.
// `raw` is a string for number and string fields, a boolean for bool fields,
// and an array of strings for vector fields.
export function parseInput(param, raw) {
  if (!param?.editable) return { ok: false, error: `${param?.name || "this parameter"} cannot be edited here` };
  const name = param.name;
  if (param.kind === "number") {
    const value = parseNumberText(raw);
    if (value === null) return { ok: false, error: `${name} needs a finite number` };
    if (param.range && (value < param.range.min || value > param.range.max)) {
      return { ok: false, error: `${name} must be between ${formatValue(param.range.min)} and ${formatValue(param.range.max)}` };
    }
    if (param.options && !param.options.some((option) => option === value)) {
      return { ok: false, error: `${name} must be one of ${param.options.map(formatValue).join(", ")}` };
    }
    return { ok: true, value };
  }
  if (param.kind === "bool") {
    if (typeof raw !== "boolean") return { ok: false, error: `${name} needs true or false` };
    return { ok: true, value: raw };
  }
  if (param.kind === "string") {
    const value = String(raw ?? "");
    if (/[\r\n]/.test(value)) return { ok: false, error: `${name} must be a single line` };
    if (param.options && !param.options.some((option) => String(option) === value)) {
      return { ok: false, error: `${name} must be one of ${param.options.map(formatValue).join(", ")}` };
    }
    return { ok: true, value };
  }
  if (param.kind === "vector") {
    const items = Array.isArray(raw) ? raw : [];
    const expected = Array.isArray(param.value) ? param.value.length : 0;
    if (items.length !== expected) return { ok: false, error: `${name} keeps its length of ${expected}` };
    const value = items.map(parseNumberText);
    if (value.some((item) => item === null)) return { ok: false, error: `${name} needs finite numbers only` };
    return { ok: true, value };
  }
  return { ok: false, error: `${name} cannot be edited here` };
}

export function valuesEqual(a, b) {
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => valuesEqual(x, b[i]));
  if (typeof a === "boolean" || typeof b === "boolean") return a === b;
  if (typeof a === "number" && typeof b === "number") return a === b;
  return a === b;
}

// The Changes summary: every parsed value that differs from the revision's,
// in source order. `values` maps name -> parsed value (already validated).
export function changedParams(model, values) {
  const out = [];
  for (const param of model?.parameters || []) {
    if (!(param.name in (values || {}))) continue;
    const after = values[param.name];
    if (!valuesEqual(param.value, after)) out.push({ name: param.name, before: param.value, after });
  }
  return out;
}

// The request body for "Apply and compile": only the changed names.
export function applyBody(model, values) {
  const params = {};
  for (const change of changedParams(model, values)) params[change.name] = change.after;
  return params;
}

export function modelSummary(model) {
  const params = model?.parameters || [];
  if (!params.length) return "No parameters found: this source has no top-level literal assignment.";
  const editable = params.filter((p) => p.editable).length;
  return `${params.length} parameters, ${editable} editable.`;
}
