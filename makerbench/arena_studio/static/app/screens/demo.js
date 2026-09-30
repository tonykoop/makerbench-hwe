import { html } from "../html.js";
import { useResource } from "../hooks/useResource.js";
import { Loading, ErrorState } from "../components/states.js";

const HELD_LABELS = { backends: "CAD tool", context_tiers: "reference", instruments: "instrument",
  levels: "difficulty", models: "AI model", seeds: "run numbers" };
const HELD_VALUES = { openscad: "OpenSCAD", blind: "no reference image", L1: "introductory" };
const CHECK_NAMES = { renders: "Renders", watertight: "Watertight", nonzero_volume: "Has volume",
  fits_envelope: "Fits the size limit", min_wall: "Wall thickness", body_count: "Body count" };

function resultText(row) {
  if (row.objective_pass_rate == null) return "Build checks were not completed.";
  if (row.objective_pass_rate === 1 && row.n_objective_trials != null) {
    const n = row.n_objective_trials;
    return `Passed all 6 build checks in ${n} of ${n} ${n === 1 ? "run" : "runs"}.`;
  }
  const gates = Object.values(row.gates || {});
  if (gates.length === 6) return `Passed ${gates.filter(value => value >= 1).length} of 6 build checks.`;
  if (row.n_objective_trials === 1 && Math.abs(row.objective_pass_rate * 6 - Math.round(row.objective_pass_rate * 6)) < 0.00001) {
    return `Passed ${Math.round(row.objective_pass_rate * 6)} of 6 build checks in this run.`;
  }
  return `Build-check average: ${(row.objective_pass_rate * 100).toFixed(2)}%${row.n_objective_trials != null ? ` across ${row.n_objective_trials} runs` : ""}.`;
}

export function DemoScreen({ route, runs }) {
  const selected = route.args[0] || "post3-models";
  const summary = useResource(`/api/runs/${encodeURIComponent(selected)}/summary`);
  return html`<div class="screen demo-screen">
    <section class="demo-intro" aria-label="About MakerBench">
      <h1 tabindex="-1">AI designs instruments. MakerBench checks the build.</h1>
      <p class="lede">MakerBench asks AI models to design physical instruments in CAD.
        Six objective build checks score whether each design renders and meets the build requirements.</p>
      <p>Explore the recorded results and pictures below. This demo is read-only.</p>
    </section>
    <ul class="run-links">${(runs.data?.runs || []).map((run) => html`
      <li key=${run.run_id}><a href=${`#/runs/${run.run_id}`}>${run.title}</a></li>`)}
    </ul>
    ${summary.status === "loading" || summary.status === "idle"
      ? html`<${Loading} label="Loading showcase…" />`
      : summary.status === "error"
        ? html`<${ErrorState} error=${summary.error} onRetry=${summary.reload} />`
        : html`<section><h2>${summary.data.title}</h2><p>${summary.data.note}</p>
          ${summary.data.varied_axis && html`<p>What changed: ${summary.data.varied_axis === "models" ? "the AI model" : "the CAD tool"}.
            Kept the same: ${Object.entries(summary.data.held || {}).map(([k, v]) =>
              `${HELD_LABELS[k] || k}: ${HELD_VALUES[v] || v}`).join(" · ")}.</p>`}
          <div class="matchup-grid">${summary.data.rows.map((row, i) => html`
            <article class="matchup-entrant" key=${i}>
              <h3>${row.label}</h3>
              ${row.image && html`<img class="matchup-render" src=${row.image} alt=${row.label} />`}
              <p>${resultText(row)}</p>
              <ul>${Object.entries(row.gates || {}).map(([gate, value]) => html`
                <li key=${gate}>${CHECK_NAMES[gate] || gate}: ${value >= 1 ? "Pass" : "Fail"}</li>`)}</ul>
            </article>`)}</div>
          <p>How these examples were made: <code>${summary.data.source}</code></p>
        </section>`}
  </div>`;
}
