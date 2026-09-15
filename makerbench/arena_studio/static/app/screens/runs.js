import { html } from "../html.js";
import { useResource } from "../hooks/useResource.js";
import { Empty, ErrorState, Loading } from "../components/states.js";
import { formatWhen, UNKNOWN_GRADE_COUNT } from "../lib/format.js";
import { buildHash } from "../lib/route.js";

function RunsTable({ runs, selected }) {
  if (runs.status === "loading" || runs.status === "idle") {
    return html`<${Loading} label="Loading runs…" />`;
  }
  if (runs.status === "error") {
    return html`<${ErrorState} error=${runs.error} onRetry=${runs.reload} />`;
  }
  const list = runs.data.runs || [];
  if (list.length === 0) {
    return html`
      <${Empty} title="No arena runs found">
        <p>
          Studio looks for <code>run_log.json</code> under <code>runs/code_cad_arena</code> and
          <code>arena_gen</code> in this checkout.
        </p>
        <p>Start a run with <code>makerbench arena run</code>, or restart Studio with <code>--run-dir</code>.</p>
      <//>
    `;
  }
  return html`
    <div class="table-scroll">
      <table class="data-table">
        <caption class="visually-hidden">Runs found in this checkout</caption>
        <thead>
          <tr>
            <th scope="col">Run</th>
            <th scope="col">Started</th>
            <th scope="col" class="num">Entrants</th>
            <th scope="col" class="num">Instruments</th>
            <th scope="col" class="num">Trials</th>
            <th scope="col" class="num">Blind votes</th>
          </tr>
        </thead>
        <tbody>
          ${list.map(
            (run) => html`
              <tr key=${run.run_id} aria-current=${run.run_id === selected ? "true" : undefined}>
                <th scope="row"><a href=${buildHash("runs", [run.run_id])}>${run.run_id}</a></th>
                <td>${formatWhen(run.created_at)}</td>
                <td class="num">${(run.models || []).length}</td>
                <td class="num">${(run.instruments || []).length}</td>
                <td class="num">${run.trials_count}</td>
                <td class="num">${run.votes_count}</td>
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
  `;
}

function TagList({ items, empty }) {
  if (!items || items.length === 0) return html`<p class="hint">${empty}</p>`;
  return html`<ul class="tag-list">${items.map((item) => html`<li key=${item}>${item}</li>`)}</ul>`;
}

function RunSummary({ runId }) {
  const summary = useResource(`/api/runs/${encodeURIComponent(runId)}/summary`);
  if (summary.status === "loading" || summary.status === "idle") {
    return html`<${Loading} label=${`Loading ${runId}…`} />`;
  }
  if (summary.status === "error") {
    return html`<${ErrorState} error=${summary.error} onRetry=${summary.reload} />`;
  }
  const run = summary.data;
  const config = run.config || {};
  return html`
    <article class="run-summary">
      <h2>${run.run_id}</h2>
      <dl class="facts">
        <div><dt>Started</dt><dd>${formatWhen(run.created_at)}</dd></div>
        <div><dt>Trials</dt><dd class="measure">${run.trials_count}</dd></div>
        <div><dt>Blind votes</dt><dd class="measure">${run.votes_count}</dd></div>
        ${config.backend && html`<div><dt>Backend</dt><dd>${config.backend}</dd></div>`}
        ${config.context_tier &&
        html`<div><dt>Context tier</dt><dd>${config.context_tier}</dd></div>`}
        <div>
          <dt>Compiled and manifold</dt>
          <dd class="unknown">
            ${UNKNOWN_GRADE_COUNT}
            <span class="fact-note">Run logs don't record these counts yet.</span>
          </dd>
        </div>
      </dl>
      <h3>Entrants</h3>
      <${TagList} items=${run.models} empty="No entrants recorded." />
      <h3>Instruments</h3>
      <${TagList} items=${run.instruments} empty="No instruments recorded." />
    </article>
  `;
}

export function RunsScreen({ route, runs }) {
  const selected = route.args[0] || null;
  return html`
    <div class="screen">
      <h1 tabindex="-1">Runs</h1>
      <p class="lede">Arena runs Studio found in this checkout. Choose one to see what it contains.</p>
      <div class="runs-layout">
        <section aria-label="All runs">
          <${RunsTable} runs=${runs} selected=${selected} />
        </section>
        <section aria-label="Selected run" aria-live="polite">
          ${selected
            ? html`<${RunSummary} key=${selected} runId=${selected} />`
            : html`<p class="hint">Choose a run to see its entrants, instruments and votes.</p>`}
        </section>
      </div>
    </div>
  `;
}
