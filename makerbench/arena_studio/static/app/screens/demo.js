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

function DemoCard({ row, index, selected }) {
  return html`<article class="matchup-entrant" key=${index}>
              <h3>${row.label}</h3>
              <div class="demo-render-strip">${(row.trials || []).filter(trial => trial.image).map((trial, index) => html`
                <figure key=${index}>
                  <img class="matchup-render" src=${trial.image} alt=${`${row.label}${trial.seed == null ? "" : `, run ${trial.seed}`}`} />
                  ${trial.seed != null && html`<figcaption>Run ${trial.seed}</figcaption>`}
                </figure>`)}</div>
              <p>${resultText(row)}</p>
              <${CheckChips} row=${row} prefix=${`${selected}-${index}`} />
            </article>`;
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
      ${runs.data?.hero && html`<figure class="demo-hero">
        <img src=${runs.data.hero.image} alt=${runs.data.hero.alt} />
        <figcaption>${runs.data.hero.caption}</figcaption>
      </figure>`}
    </section>
    <ul class="run-links">${(runs.data?.runs || []).map((run) => html`
      <li key=${run.run_id}><a href=${`#/runs/${run.run_id}`}>${run.title}</a></li>`)}
    </ul>
    ${summary.status === "loading" || summary.status === "idle" || (summary.status === "ready" && summary.data.id !== selected)
      ? html`<${Loading} label="Loading showcase…" />`
      : summary.status === "error"
        ? html`<${ErrorState} error=${summary.error} onRetry=${summary.reload} />`
        : html`<section><h2>${summary.data.title}</h2><p>${summary.data.note}</p>
          ${summary.data.varied_axis && html`<p>What changed: ${summary.data.varied_axis === "models" ? "the AI model" : "the CAD tool"}.
            Kept the same: ${Object.entries(summary.data.held || {}).map(([k, v]) =>
              `${HELD_LABELS[k] || k}: ${HELD_VALUES[v] || v}`).join(" · ")}.</p>`}
          ${summary.data.story && html`<section class="demo-story" aria-labelledby="backend-story-title">
            <h3 id="backend-story-title">${summary.data.story.title}</h3>
            <p>${summary.data.story.summary}</p>
            <table class="demo-history-table">
              <caption>Published average across six build checks (three runs per tool)</caption>
              <thead><tr><th scope="col">CAD tool</th><th scope="col">Original results</th>
                <th scope="col">Same meshes, fixed check</th><th scope="col">Fresh runs</th></tr></thead>
              <tbody>${summary.data.story.rows.map(row => html`<tr key=${row.backend}>
                <th scope="row">${row.label}</th><td data-period="published">${(row.published * 100).toFixed(1)}%</td>
                <td data-period="same_meshes">${(row.same_meshes * 100).toFixed(1)}%</td>
                <td data-period="fresh">${(row.fresh * 100).toFixed(1)}%</td>
              </tr>`)}</tbody>
            </table>
            <p>${summary.data.story.caveat}</p>
            <p>Source write-ups: ${summary.data.story.sources.map(source => html`<a key=${source.path} href=${source.url} target="_blank" rel="noopener noreferrer">${source.label}</a>`)}</p>
          </section>`}
          ${selected === "kora" ? html`<div class="demo-context-grid">
            ${["blind", "image"].map(tier => html`<section class="demo-context-arm" key=${tier}>
              <h3>${tier === "blind" ? "Text brief only" : "With a reference photo"}</h3>
              ${tier === "blind" ? html`<p>The same kora task, described in words without a reference image.</p>` : html`
                <figure class="demo-reference">
                  <img src=${summary.data.photo.image} alt=${summary.data.photo.alt} />
                  <figcaption>${summary.data.photo.caption}<br />Photo from
                    <a href=${summary.data.photo.credit_url} target="_blank" rel="noopener noreferrer">tonykoop/kora</a>,
                    <a href=${summary.data.photo.license_url} target="_blank" rel="noopener noreferrer">CC BY 4.0</a>.
                  </figcaption>
                </figure>`}
              <div class="matchup-grid">${summary.data.rows.map((row, index) => row.context_tier === tier && html`
                <${DemoCard} row=${row} index=${index} selected=${selected} />`)}</div>
            </section>`)}
          </div>` : html`<div class=${`matchup-grid ${selected === "strings-gallery" ? "demo-gallery" : ""}`}>
            ${summary.data.rows.map((row, index) => html`<${DemoCard} row=${row} index=${index} selected=${selected} />`)}
          </div>`}
          <p>How these examples were made: <a class="demo-source" href=${summary.data.source_url} target="_blank" rel="noopener noreferrer">Read the study on GitHub</a></p>
        </section>`}
  </div>`;
}
