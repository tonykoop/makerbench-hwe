import { useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { useResource } from "../hooks/useResource.js";
import { Empty, ErrorState, Loading } from "../components/states.js";
import { formatWhen, UNKNOWN_GRADE_COUNT } from "../lib/format.js";
import { buildHash } from "../lib/route.js";
import { toggleSelection } from "../lib/matchupCompare.js";
import { gateLabel } from "../lib/gateText.js";
import { MatchupCompare } from "../components/matchupCompare.js";

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

// #788 W4: open a trial's candidate in the design workbench. The list names
// entrants, exactly as the run summary already does; the notice says so.
function OpenInWorkbench({ runId, trials }) {
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState({ text: "", error: false });
  const statusRef = useRef(null);
  const list = (trials || []).filter((trial) => trial.trial_id);
  if (list.length === 0) return null;
  const open = async (trialId) => {
    if (busy) return;
    setBusy(trialId);
    setMessage({ text: "", error: false });
    try {
      const created = await api("/api/workbench/designs", {
        method: "POST",
        body: { trial: { run_id: runId, trial_id: trialId } },
      });
      window.location.hash = buildHash("workbench", [created.design_id]);
    } catch (error) {
      setMessage({ text: error.message, error: true });
      statusRef.current?.focus();
    } finally {
      setBusy("");
    }
  };
  return html`
    <section class="open-in-workbench" aria-labelledby="open-workbench-title">
      <h3 id="open-workbench-title">Open in the workbench</h3>
      <p class="hint" role="note">This shows the entrant. Vote first if you want to stay blind.</p>
      <ul class="tag-list trial-list">
        ${list.map(
          (trial) => html`
            <li key=${trial.trial_id}>
              <button
                type="button"
                class="button button-quiet"
                data-open-trial=${trial.trial_id}
                aria-disabled=${busy ? "true" : "false"}
                onClick=${() => open(trial.trial_id)}
              >
                Open ${trial.trial_id}
              </button>
            </li>
          `,
        )}
      </ul>
      <p class=${`panel-status${message.error ? " is-error" : ""}`} role="status" tabindex="-1" ref=${statusRef}>${message.text}</p>
    </section>
  `;
}

function MatchupResults({ metadata, trials }) {
  const [selected, setSelected] = useState([]);
  const [comparing, setComparing] = useState(false);
  const openRef = useRef(null);
  if (!metadata) return null;
  const varied = (metadata.varied_axes || [metadata.varied_axis]).join(", ");
  const rows = trials || [];
  const chosen = selected.map((id) => rows.find((trial) => trial.trial_id === id)).filter(Boolean);
  const close = () => {
    setComparing(false);
    // focus returns once the button is back in the layout
    setTimeout(() => openRef.current?.focus(), 0);
  };
  return html`
    <section class="matchup-results" aria-labelledby="matchup-results-title">
      <h3 id="matchup-results-title">Matchup results</h3>
      <p>Varied: ${varied}${metadata.factorial ? " (factorial)" : ""}</p>
      <p class="hint">Held: ${Object.entries(metadata.held || {}).map(([key, value]) => `${key}: ${value}`).join(" · ")}</p>
      <p class="hint">Objective mesh gates only. Wall time is the latest recorded attempt, excluding rate-limit waiting.</p>
      <div class="matchup-grid">
        ${rows.map((trial) => html`
          <article class="matchup-entrant" key=${trial.trial_id}>
            <h4>${trial.entrant}</h4>
            <p>${trial.instrument_id} · seed ${trial.seed} · repetition ${trial.rep}</p>
            ${trial.render_url
              ? html`<img class="matchup-render" src=${trial.render_url} alt=${`Render for ${trial.entrant}`} />`
              : html`<p class="hint">Render not available</p>`}
            <dl class="facts">
              <div><dt>Status</dt><dd>${trial.status}</dd></div>
              <div><dt>Wall time</dt><dd>${trial.wall_time_s == null ? "Not recorded" : `${trial.wall_time_s.toFixed(2)} s`}</dd></div>
              <div><dt>Gate pass rate</dt><dd>${trial.objective_pass_rate == null ? "Not measured" : `${(trial.objective_pass_rate * 100).toFixed(2)}%`}</dd></div>
            </dl>
            <ul class="matchup-gates">
              ${Object.entries(trial.gates || {}).map(([name, value]) => html`
                <li key=${name}><span>${name}</span>: <strong>${gateLabel(value)}</strong></li>
              `)}
            </ul>
            ${rows.length > 1 && html`<label class="compare-pick">
              <input
                type="checkbox"
                data-compare-trial=${trial.trial_id}
                checked=${selected.includes(trial.trial_id)}
                onChange=${() => setSelected((current) => toggleSelection(current, trial.trial_id))}
              />
              Compare
            </label>`}
          </article>
        `)}
      </div>
      ${rows.length > 1 && html`<div class="compare-launch">
        <button
          type="button"
          class="button"
          ref=${openRef}
          aria-disabled=${chosen.length === 2 ? "false" : "true"}
          onClick=${() => chosen.length === 2 && setComparing(true)}
        >
          Compare side by side
        </button>
        <span class="hint" role="status">${chosen.length === 2 ? `${chosen[0].entrant} and ${chosen[1].entrant} selected.` : `Choose two trials to compare (${chosen.length} of 2).`}</span>
      </div>`}
      ${comparing && chosen.length === 2
        && html`<${MatchupCompare} key=${selected.join("|")} trials=${chosen} onClose=${close} />`}
    </section>
  `;
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
      <p><a class="button button-link" href=${buildHash("vote", [run.run_id])}>Start blind voting</a></p>
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
      <${MatchupResults} metadata=${run.matchup} trials=${run.matchup_trials} />
      <ul class="run-links">
        <li><a href=${buildHash("analytics", [run.run_id])}>Agreement analytics</a></li>
        <li><a href=${buildHash("compare", [], { a: run.run_id })}>Compare with another run</a></li>
      </ul>
      <${OpenInWorkbench} runId=${run.run_id} trials=${run.trials} />
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
