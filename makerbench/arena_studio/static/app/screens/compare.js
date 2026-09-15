import { html } from "../html.js";
import { formatWhen } from "../lib/format.js";
import { buildHash } from "../lib/route.js";
import { sharedEntrants } from "../lib/analytics.js";
import { useResource } from "../hooks/useResource.js";
import { Empty, ErrorState, Loading } from "../components/states.js";
import { LeaderboardTable } from "./analytics.js";

const enc = encodeURIComponent;

function RunPicker({ side, label, value, runs, onChoose }) {
  const options = runs.status === "ready" ? runs.data.runs || [] : [];
  const known = options.some((run) => run.run_id === value);
  return html`
    <label class="field">
      <span class="field-label">${label}</span>
      <select name=${`run-${side}`} value=${value} onChange=${(event) => onChoose(side, event.currentTarget.value)}>
        <option value="">Choose a run</option>
        ${value && !known && html`<option value=${value}>${value} (not found)</option>`}
        ${options.map((run) => html`<option key=${run.run_id} value=${run.run_id}>${run.run_id}</option>`)}
      </select>
    </label>
  `;
}

// Each side loads on its own, so one failing run never blanks the other.
function CompareColumn({ runId, label, summary, board }) {
  if (!runId) {
    return html`<section class="panel compare-column" aria-label=${label}>
      <p class="hint">Choose the ${label.toLowerCase()} above.</p>
    </section>`;
  }
  let facts;
  if (summary.status === "loading" || summary.status === "idle") facts = html`<${Loading} label=${`Loading ${runId}…`} />`;
  else if (summary.status === "error") facts = html`<${ErrorState} error=${summary.error} onRetry=${summary.reload} />`;
  else {
    const run = summary.data;
    facts = html`
      <dl class="facts">
        <div><dt>Started</dt><dd>${formatWhen(run.created_at)}</dd></div>
        <div><dt>Entrants</dt><dd class="measure">${(run.models || []).length}</dd></div>
        <div><dt>Instruments</dt><dd class="measure">${(run.instruments || []).length}</dd></div>
        <div><dt>Trials</dt><dd class="measure">${run.trials_count}</dd></div>
        <div><dt>Blind votes</dt><dd class="measure">${run.votes_count}</dd></div>
      </dl>
    `;
  }
  let leaderboard;
  if (board.status === "loading" || board.status === "idle") leaderboard = html`<${Loading} label="Loading the leaderboard…" />`;
  else if (board.status === "error") leaderboard = html`<${ErrorState} error=${board.error} onRetry=${board.reload} />`;
  else if ((board.data.leaderboard || []).length === 0) leaderboard = html`<${Empty} title="No human votes yet" />`;
  else leaderboard = html`<${LeaderboardTable} board=${board.data} caption=${`Human Elo for ${runId}`} />`;

  return html`
    <section class="panel compare-column" aria-label=${`${label}: ${runId}`}>
      <h2>${runId}</h2>
      ${facts}
      <h3>Human Elo</h3>
      ${leaderboard}
      <p><a href=${buildHash("analytics", [runId])}>Open agreement analytics for ${runId}</a></p>
    </section>
  `;
}

export function CompareScreen({ route, runs }) {
  const a = route.query.a || "";
  const b = route.query.b || "";
  const summaryA = useResource(a ? `/api/runs/${enc(a)}/summary` : null);
  const boardA = useResource(a ? `/api/runs/${enc(a)}/leaderboard` : null);
  const summaryB = useResource(b ? `/api/runs/${enc(b)}/summary` : null);
  const boardB = useResource(b ? `/api/runs/${enc(b)}/leaderboard` : null);

  const choose = (side, value) => {
    const next = { a, b, [side]: value };
    window.location.hash = buildHash("compare", [], next);
  };
  const shared = boardA.status === "ready" && boardB.status === "ready" ? sharedEntrants(boardA.data, boardB.data) : null;

  return html`
    <div class="screen screen-compare">
      <h1 tabindex="-1">Compare runs</h1>
      <p class="lede">Two runs side by side: what each contains and how people rated its entrants. Nothing here changes a run.</p>
      <div class="compare-pickers">
        <${RunPicker} side="a" label="First run" value=${a} runs=${runs} onChoose=${choose} />
        <${RunPicker} side="b" label="Second run" value=${b} runs=${runs} onChoose=${choose} />
      </div>
      ${runs.status === "error" && html`<${ErrorState} error=${runs.error} onRetry=${runs.reload} />`}
      ${a && b && a === b && html`<p class="caveat" role="note">Both sides show the same run. Choose a different second run.</p>`}
      ${shared &&
      a !== b &&
      html`<p class="hint">
        ${shared.length ? `Entrants rated in both runs: ${shared.join(", ")}.` : "No entrant was rated in both runs."}
      </p>`}
      <div class="compare-grid">
        <${CompareColumn} key=${`a:${a}`} runId=${a} label="First run" summary=${summaryA} board=${boardA} />
        <${CompareColumn} key=${`b:${b}`} runId=${b} label="Second run" summary=${summaryB} board=${boardB} />
      </div>
    </div>
  `;
}
