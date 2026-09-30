import { html } from "../html.js";
import { useResource } from "../hooks/useResource.js";
import { Loading, ErrorState } from "../components/states.js";

export function DemoScreen({ route, runs }) {
  const selected = route.args[0] || "post3-models";
  const summary = useResource(`/api/runs/${encodeURIComponent(selected)}/summary`);
  return html`<div class="screen">
    <h1 tabindex="-1">Studio showcase</h1>
    <p class="lede">Read-only public examples. Browse objective results and renders.</p>
    <p>No launches, voting or editing are available in this demo.</p>
    <ul class="run-links">${(runs.data?.runs || []).map((run) => html`
      <li key=${run.run_id}><a href=${`#/runs/${run.run_id}`}>${run.title}</a></li>`)}
    </ul>
    ${summary.status === "loading" || summary.status === "idle"
      ? html`<${Loading} label="Loading showcase…" />`
      : summary.status === "error"
        ? html`<${ErrorState} error=${summary.error} onRetry=${summary.reload} />`
        : html`<section><h2>${summary.data.title}</h2><p>${summary.data.note}</p>
          ${summary.data.varied_axis && html`<p>Varied: ${summary.data.varied_axis} · Held:
            ${Object.entries(summary.data.held || {}).map(([k, v]) => `${k}: ${v}`).join(" · ")}</p>`}
          <div class="matchup-grid">${summary.data.rows.map((row, i) => html`
            <article class="matchup-entrant" key=${i}>
              <h3>${row.label}</h3><p>${row.status}</p>
              ${row.image && html`<img class="matchup-render" src=${row.image} alt=${row.label} />`}
              <p>Measured mesh gate rate: ${row.objective_pass_rate == null ? "Not measured"
                : `${(row.objective_pass_rate * 100).toFixed(2)}%`}
                ${row.n_objective_trials != null && ` (${row.n_objective_trials} trials)`}</p>
              <ul>${Object.entries(row.gates || {}).map(([gate, value]) => html`
                <li key=${gate}>${gate}: ${value >= 1 ? "Pass" : "Fail"}</li>`)}</ul>
            </article>`)}</div>
          <p>Source study and scope: <code>${summary.data.source}</code></p>
        </section>`}
  </div>`;
}
