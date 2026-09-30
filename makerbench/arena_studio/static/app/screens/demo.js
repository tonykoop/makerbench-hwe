import { html } from "../html.js";
import { useResource } from "../hooks/useResource.js";
import { Loading, ErrorState } from "../components/states.js";

const HELD_LABELS = { backends: "CAD tool", context_tiers: "reference", instruments: "instrument",
  levels: "difficulty", models: "AI model", seeds: "run numbers" };
const HELD_VALUES = { openscad: "OpenSCAD", blind: "no reference image", L1: "introductory" };
const CHECK_NAMES = { renders: "Renders", watertight: "Watertight", nonzero_volume: "Has volume",
  fits_envelope: "Fits the size limit", min_wall: "Wall thickness", body_count: "Body count" };
const CHECK_ORDER = ["renders", "watertight", "nonzero_volume", "fits_envelope", "min_wall", "body_count"];
const CHECK_HELP = { renders: "The recorded design could be rendered.", watertight: "Checks for holes or open edges.",
  nonzero_volume: "Checks that the model has a positive volume.", fits_envelope: "Checks the model against the allowed size.",
  min_wall: "Checks the sampled wall thickness against the task's floor.", body_count: "Checks the required parts; some historical runs used a part-module fallback." };

function CheckChips({ row, prefix }) {
  return html`<ul class="demo-checks" aria-label="Build checks">${CHECK_ORDER.map(gate => {
    const trials = (row.trials || []).filter(trial => trial.gates?.[gate] != null);
    const passed = trials.filter(trial => trial.gates[gate] >= 1).length;
    const state = trials.length === 0 ? "unknown" : passed === trials.length ? "pass" : "fail";
    const label = state === "unknown" ? "Not measured" : state === "pass" ? "Pass" : "Fail";
    const details = trials.flatMap(trial => (trial.failed_checks || []).filter(failure => failure.check === gate)
      .map(failure => ({ ...failure, seed: trial.seed })));
    const id = `${prefix}-${gate}-detail`;
    return html`<li key=${gate}><span class="demo-check" data-state=${state} tabindex="0" aria-describedby=${id}
      onMouseEnter=${event => { delete event.currentTarget.dataset.dismissed; }}
      onFocus=${event => { delete event.currentTarget.dataset.dismissed; }}
      onKeyDown=${event => { if (event.key === "Escape") event.currentTarget.dataset.dismissed = "true"; }}>
      ${CHECK_NAMES[gate]}: ${label}${trials.length > 1 ? ` (${passed}/${trials.length})` : ""}
      <span class="demo-check-detail" role="tooltip" id=${id}>
        <strong>${CHECK_NAMES[gate]}</strong><br />${CHECK_HELP[gate]}<br />
        ${trials.length === 0 ? "No recorded measurement is available." : `${passed} of ${trials.length} recorded runs passed this check.`}
        ${details.map(detail => html`<span class="demo-failure-detail" key=${`${detail.seed}-${detail.body_id}`}>
          Run ${detail.seed}${detail.body_id ? `, ${detail.body_id}` : ""}: measured ${detail.measured ?? "not recorded"} ${detail.unit || ""};
          threshold ${detail.threshold ?? "not recorded"} ${detail.unit || ""}${detail.tolerance != null ? `; tolerance ${detail.tolerance} ${detail.unit || ""}` : ""}.
          ${detail.requires || ""}. ${detail.detail || ""}
        </span>`)}
        ${trials.length > details.length && html`<span class="demo-failure-detail">Measurements and thresholds were not saved for ${details.length ? "the other" : "these"} historical runs.</span>`}
      </span>
    </span></li>`;
  })}</ul>`;
}

function resultText(row) {
  if (row.objective_pass_rate == null) return "Build checks were not completed.";
  if (row.objective_pass_rate === 1 && row.n_objective_trials != null) {
    const n = row.n_objective_trials;
    return `Passed all 6 build checks in ${n} of ${n} ${n === 1 ? "run" : "runs"}.`;
  }
  const gates = Object.values(row.gates || {});
  if (gates.length === 6 && (row.trials || []).length <= 1) return `Passed ${gates.filter(value => value >= 1).length} of 6 build checks${row.n_objective_trials === 1 ? " in this run" : ""}.`;
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
              <div class="demo-render-strip">${(row.trials || []).filter(trial => trial.image).map((trial, index) => html`
                <figure key=${index}>
                  <img class="matchup-render" src=${trial.image} alt=${`${row.label}${trial.seed == null ? "" : `, run ${trial.seed}`}`} />
                  ${trial.seed != null && html`<figcaption>Run ${trial.seed}</figcaption>`}
                </figure>`)}</div>
              <p>${resultText(row)}</p>
              <${CheckChips} row=${row} prefix=${`${selected}-${i}`} />
            </article>`)}</div>
          <p>How these examples were made: <code>${summary.data.source}</code></p>
        </section>`}
  </div>`;
}
