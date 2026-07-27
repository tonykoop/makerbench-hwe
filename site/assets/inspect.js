// inspect.js — page controller for site/inspect.html (#107 Inspect-a-Run gallery).
//
// Fetches data/inspect.json, renders a left-rail picker, and builds a fresh
// .mb-inspect host for each selected artifact so the old WebGL context is
// dropped before a new one starts.  All user-supplied text is set via
// textContent / setAttribute (no innerHTML with artifact data) to avoid XSS.
//
// Only PUBLIC submission geometry (site/assets/meshes/*) is ever referenced.
// Oracle geometry is never touched here.

import { initInspect } from "./inspect-run.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Escape a string for safe use as an HTML attribute value (belt-and-suspenders
 *  on top of setAttribute, which is already safe — kept for clarity). */
function esc(v) {
  return String(v)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Safely set text content of an element. */
function setText(el, text) {
  el.textContent = String(text ?? "");
}

// ---------------------------------------------------------------------------
// DOM refs — wired at DOMContentLoaded
// ---------------------------------------------------------------------------

let railEl = null;   // .mb-inspect-rail (left picker)
let detailEl = null; // .mb-inspect-detail (right panel, replaced on each pick)
let currentId = null;

// ---------------------------------------------------------------------------
// Build a fresh .mb-inspect host and call initInspect() on it.
// Drops the previous host (and its WebGL context) entirely.
// ---------------------------------------------------------------------------

function buildHost(artifact) {
  // Verdict check keys the spec defines.
  const checks = [
    { key: "no-interference", label: "No interference" },
    { key: "wall-thickness",  label: "Wall thickness" },
    { key: "thread-pitch",    label: "Thread pitch" },
    { key: "mass-constraint", label: "Mass" },
  ];

  const host = document.createElement("div");
  host.className = "mb-inspect";
  host.setAttribute("data-artifact", esc(artifact.mesh));
  if (artifact.dfm_min_wall_mm != null) {
    host.setAttribute("data-min-wall", String(Number(artifact.dfm_min_wall_mm)));
  }
  if (Array.isArray(artifact.interference_zones) && artifact.interference_zones.length) {
    host.setAttribute("data-interference", JSON.stringify(artifact.interference_zones));
  }

  // ---- canvas box (required) ----
  const canvasBox = document.createElement("div");
  canvasBox.className = "mb-inspect-canvas";
  host.appendChild(canvasBox);

  // ---- mode button bar ----
  const bar = document.createElement("div");
  bar.className = "mb-inspect-bar";
  bar.setAttribute("role", "group");
  bar.setAttribute("aria-label", "View mode");

  const modes = [
    { mode: "solid",        label: "Solid" },
    { mode: "heatmap",      label: "Wall heat-map" },
    { mode: "interference", label: "Interference" },
    { mode: "wireframe",    label: "Wireframe" },
  ];
  for (const { mode, label } of modes) {
    const btn = document.createElement("button");
    btn.className = "mb-inspect-mode";
    btn.setAttribute("data-mode", mode);
    btn.setAttribute("aria-pressed", mode === "solid" ? "true" : "false");
    btn.setAttribute("type", "button");
    setText(btn, label);
    bar.appendChild(btn);
  }
  host.appendChild(bar);

  // ---- legend ----
  const legend = document.createElement("div");
  legend.className = "mb-inspect-legend";
  host.appendChild(legend);

  // ---- metrics row ----
  if (Array.isArray(artifact.metrics) && artifact.metrics.length) {
    const readout = document.createElement("div");
    readout.className = "mb-inspect-readout";
    for (const m of artifact.metrics) {
      const cell = document.createElement("div");
      cell.className = "mb-metric";
      const lbl = document.createElement("span");
      lbl.className = "l";
      setText(lbl, m.label);
      const val = document.createElement("span");
      val.className = "v";
      setText(val, m.value);
      cell.appendChild(lbl);
      cell.appendChild(val);
      readout.appendChild(cell);
    }
    host.appendChild(readout);
  }

  // ---- verdict panel ----
  const verdict = document.createElement("div");
  verdict.className = "mb-inspect-verdict";
  verdict.setAttribute("role", "list");

  const score = typeof artifact.score === "number" ? artifact.score : null;

  for (const { key, label } of checks) {
    const card = document.createElement("div");
    card.className = "mb-inspect-check";
    card.setAttribute("data-check", key);
    card.setAttribute("role", "listitem");

    // Derive a simple pass/fail/na state from score + check type.
    // A score of 4 means all checks pass; partial scores give partial info.
    // We only mark definitive fails or passes; anything uncertain is "na".
    let state = "na";
    if (score !== null) {
      if (key === "no-interference") {
        state = score >= 2 ? "pass" : "fail";
      } else if (key === "wall-thickness") {
        // DFM (level 4) check
        state = score >= 4 ? "pass" : (score === 0 ? "na" : "na");
        // Only definitive if we have wall data
        if (artifact.dfm_min_wall_mm != null && artifact.min_wall_mm != null) {
          state = artifact.min_wall_mm >= artifact.dfm_min_wall_mm ? "pass" : "fail";
        }
      } else if (key === "mass-constraint") {
        state = score >= 3 ? "pass" : "na";
      }
      // thread-pitch stays "na" unless we have specific data
    }
    card.setAttribute("data-state", state);

    const icon = document.createElement("span");
    icon.className = "mb-check-icon";
    icon.setAttribute("aria-hidden", "true");
    setText(icon, state === "pass" ? "✓" : state === "fail" ? "✗" : "–");

    const name = document.createElement("span");
    name.className = "mb-check-label";
    setText(name, label);

    card.appendChild(icon);
    card.appendChild(name);
    verdict.appendChild(card);
  }
  host.appendChild(verdict);

  return host;
}

// ---------------------------------------------------------------------------
// Picker rail
// ---------------------------------------------------------------------------

function buildPicker(artifacts) {
  railEl.innerHTML = "";

  for (const artifact of artifacts) {
    const btn = document.createElement("button");
    btn.className = "mb-rail-item";
    btn.setAttribute("type", "button");
    btn.setAttribute("data-id", artifact.id);
    btn.setAttribute("aria-pressed", "false");

    const topRow = document.createElement("span");
    topRow.className = "mb-rail-label";
    setText(topRow, artifact.label || artifact.id);

    const metaRow = document.createElement("span");
    metaRow.className = "mb-rail-meta";

    const modelSpan = document.createElement("span");
    modelSpan.className = "mb-rail-model";
    setText(modelSpan, artifact.model_identifier || "");

    const trackSpan = document.createElement("span");
    trackSpan.className = "mb-rail-track";
    setText(trackSpan, artifact.track || "");

    const scoreSpan = document.createElement("span");
    scoreSpan.className = "mb-rail-score";
    const scoreVal = typeof artifact.score === "number" ? artifact.score : "–";
    setText(scoreSpan, `score ${scoreVal}`);

    metaRow.appendChild(modelSpan);
    metaRow.appendChild(trackSpan);
    metaRow.appendChild(scoreSpan);

    btn.appendChild(topRow);
    btn.appendChild(metaRow);
    railEl.appendChild(btn);

    btn.addEventListener("click", () => selectArtifact(artifact, artifacts));
  }
}

// ---------------------------------------------------------------------------
// Select an artifact: update picker state, build host, call initInspect.
// ---------------------------------------------------------------------------

function selectArtifact(artifact, artifacts) {
  currentId = artifact.id;

  // Update hash for deep-linking (no scroll jump — use replaceState).
  try {
    history.replaceState(null, "", "#" + encodeURIComponent(artifact.id));
  } catch (_) {}

  // Update picker aria-pressed states.
  railEl.querySelectorAll(".mb-rail-item").forEach((btn) => {
    const active = btn.getAttribute("data-id") === artifact.id;
    btn.setAttribute("aria-pressed", String(active));
    btn.classList.toggle("is-active", active);
  });

  // Replace the detail node entirely (drops the old WebGL context).
  detailEl.innerHTML = "";

  const header = document.createElement("div");
  header.className = "mb-inspect-header";

  const title = document.createElement("h2");
  title.className = "mb-inspect-title";
  setText(title, artifact.label || artifact.id);
  header.appendChild(title);

  const sub = document.createElement("p");
  sub.className = "mb-inspect-sub";
  const parts = [];
  if (artifact.model_identifier) parts.push(artifact.model_identifier);
  if (artifact.track) parts.push("track: " + artifact.track);
  if (artifact.task_id) parts.push("task: " + artifact.task_id);
  if (typeof artifact.seed === "number") parts.push("seed " + artifact.seed);
  setText(sub, parts.join(" · "));
  header.appendChild(sub);

  detailEl.appendChild(header);

  const host = buildHost(artifact);
  detailEl.appendChild(host);

  // Boot the viewer on the freshly-attached host.
  initInspect(host);
}

// ---------------------------------------------------------------------------
// Graceful empty-state
// ---------------------------------------------------------------------------

function showEmptyState(message) {
  if (detailEl) {
    detailEl.innerHTML = "";
    const note = document.createElement("p");
    note.className = "mb-inspect-note mb-inspect-empty";
    setText(note, message);
    detailEl.appendChild(note);
  }
  if (railEl) {
    railEl.innerHTML = "";
    const note = document.createElement("p");
    note.className = "mb-inspect-note";
    setText(note, "No artifacts to list.");
    railEl.appendChild(note);
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  railEl   = document.getElementById("mb-inspect-rail");
  detailEl = document.getElementById("mb-inspect-detail");

  if (!railEl || !detailEl) {
    console.warn("inspect.js: missing #mb-inspect-rail or #mb-inspect-detail");
    return;
  }

  fetch("data/inspect.json")
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then((payload) => {
      const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts : [];
      if (!artifacts.length) {
        showEmptyState(
          "No inspectable artifacts yet. " +
          "Meshes are generated from submissions by `python -m makerbench.viewer_export`."
        );
        return;
      }

      buildPicker(artifacts);

      // Deep-link: honour location.hash if it matches an artifact id.
      let startId = null;
      try {
        const hash = decodeURIComponent(location.hash.slice(1));
        if (hash && artifacts.some((a) => a.id === hash)) startId = hash;
      } catch (_) {}

      const start = startId
        ? artifacts.find((a) => a.id === startId)
        : artifacts[0];

      if (start) selectArtifact(start, artifacts);
    })
    .catch((err) => {
      console.warn("inspect.js: could not load data/inspect.json —", err);
      showEmptyState(
        "No inspectable artifacts yet. " +
        "Meshes are generated from submissions by `python -m makerbench.viewer_export`."
      );
    });
});
