import { useEffect, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { plural } from "../lib/format.js";
import {
  exportTargets,
  findOutliers,
  formatElo,
  formatPassRate,
  formatRank,
  formatRho,
  interpretationText,
  isProvisional,
  pointLabel,
  PROVISIONAL_GAMES,
  ratingScale,
  runInstruments,
  scatterPoints,
} from "../lib/analytics.js";
import { useResource } from "../hooks/useResource.js";
import { Empty, ErrorState, Loading } from "../components/states.js";

const enc = encodeURIComponent;

function ResourceBody({ resource, label, children }) {
  if (resource.status === "loading" || resource.status === "idle") return html`<${Loading} label=${label} />`;
  if (resource.status === "error") return html`<${ErrorState} error=${resource.error} onRetry=${resource.reload} />`;
  return children(resource.data);
}

export function ProvisionalBadge({ row }) {
  return isProvisional(row) ? html`<span class="badge-provisional">Provisional</span>` : null;
}

function IntervalBar({ row, scale }) {
  if (!scale || typeof row.ci_low !== "number" || typeof row.ci_high !== "number") {
    return html`<span class="unknown">No interval yet</span>`;
  }
  const lo = scale.x(row.ci_low);
  const hi = scale.x(row.ci_high);
  const mid = scale.x(row.rating);
  return html`
    <span class="ci">
      <svg class="ci-bar" viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        <line class="ci-axis" x1="0" y1="6" x2="100" y2="6" />
        <rect class="ci-range" x=${lo} y="2" width=${Math.max(hi - lo, 0.5)} height="8" />
        <line class="ci-point" x1=${mid} y1="0" x2=${mid} y2="12" />
      </svg>
      <span class="ci-text">${formatElo(row.ci_low)} to ${formatElo(row.ci_high)}</span>
    </span>
  `;
}

export function LeaderboardTable({ board, caption, withIntervals = false }) {
  const rows = board?.leaderboard || [];
  const scale = withIntervals ? ratingScale(rows) : null;
  const unrated = board?.unrated_entrants || [];
  return html`
    <div class="table-scroll">
      <table class="data-table leaderboard">
        <caption class="visually-hidden">${caption}</caption>
        <thead>
          <tr>
            <th scope="col" class="num">Rank</th>
            <th scope="col">Entrant</th>
            <th scope="col" class="num">Elo</th>
            ${withIntervals && html`<th scope="col">95% interval</th>`}
            <th scope="col" class="num">Games</th>
            <th scope="col" class="num">Won, lost, drawn</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(
            (row) => html`
              <tr key=${row.entrant}>
                <td class="num">${row.rank}</td>
                <th scope="row"><span class="entrant">${row.entrant}</span> <${ProvisionalBadge} row=${row} /></th>
                <td class="num measure">${formatElo(row.rating)}</td>
                ${withIntervals && html`<td><${IntervalBar} row=${row} scale=${scale} /></td>`}
                <td class="num">${row.games}</td>
                <td class="num">${row.wins}, ${row.losses}, ${row.draws}</td>
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
    ${unrated.length > 0 && html`<p class="hint">No votes yet, so not ranked: ${unrated.join(", ")}.</p>`}
  `;
}

function EloSection({ board }) {
  return html`
    <section class="panel elo-section" aria-labelledby="elo-title">
      <h2 id="elo-title">Human Elo</h2>
      <${ResourceBody} resource=${board} label="Loading the leaderboard…">
        ${(data) => {
          if ((data.leaderboard || []).length === 0) {
            return html`
              <${Empty} title="No human votes yet">
                <p>Cast blind votes on this run, then refresh this page.</p>
                ${(data.unrated_entrants || []).length > 0 &&
                html`<p>Entrants waiting for votes: ${data.unrated_entrants.join(", ")}.</p>`}
              <//>
            `;
          }
          const method = data.ci_method || {};
          return html`
            <p class="hint">
              Provisional means fewer than ${PROVISIONAL_GAMES} games, so the rating can still move a lot.
              ${typeof method.confidence === "number" &&
              ` Intervals are ${Math.round(method.confidence * 100)}% bootstrap intervals from ${method.n_resamples} resamples.`}
            </p>
            <${LeaderboardTable} board=${data} caption="Human Elo leaderboard" withIntervals=${true} />
          `;
        }}
      <//>
    </section>
  `;
}

const PLOT = { width: 360, height: 220, left: 48, right: 14, top: 14, bottom: 36 };

function Scatter({ rankings }) {
  const { points, eloRange } = scatterPoints(rankings);
  const [active, setActive] = useState(null);
  if (points.length === 0) return null;
  const { width, height, left, right, top, bottom } = PLOT;
  const px = (x) => left + (x / 100) * (width - left - right);
  const py = (y) => top + (y / 100) * (height - top - bottom);
  return html`
    <figure class="scatter">
      <svg
        viewBox=${`0 0 ${width} ${height}`}
        role="group"
        aria-label="Human Elo against objective pass rate. Each entrant is a focusable point; the table below holds the same numbers."
      >
        <line class="axis" x1=${left} y1=${height - bottom} x2=${width - right} y2=${height - bottom} />
        <line class="axis" x1=${left} y1=${top} x2=${left} y2=${height - bottom} />
        ${[0, 50, 100].map(
          (tick) => html`<text key=${tick} class="tick" x=${px(tick)} y=${height - bottom + 14} text-anchor="middle">
            ${tick}%
          </text>`,
        )}
        <text class="tick" x=${left - 6} y=${py(0) + 4} text-anchor="end">${Math.round(eloRange[1])}</text>
        <text class="tick" x=${left - 6} y=${py(100)} text-anchor="end">${Math.round(eloRange[0])}</text>
        <text class="axis-label" x=${(left + width - right) / 2} y=${height - 4} text-anchor="middle">
          Objective pass rate
        </text>
        <text class="axis-label" transform=${`translate(12 ${(top + height - bottom) / 2}) rotate(-90)`} text-anchor="middle">
          Human Elo
        </text>
        ${points.map(
          (point, index) => html`
            <circle
              key=${point.entrant}
              class="point"
              cx=${px(point.x)}
              cy=${py(point.y)}
              r="6"
              tabindex="0"
              role="img"
              aria-label=${pointLabel(point)}
              data-active=${active === index ? "true" : "false"}
              onFocus=${() => setActive(index)}
              onMouseEnter=${() => setActive(index)}
            />
          `,
        )}
      </svg>
      <figcaption class="scatter-readout" aria-live="polite">
        ${active === null ? "Focus or hover a point to read it." : pointLabel(points[active])}
      </figcaption>
    </figure>
  `;
}

function RankingsTable({ rankings, hasJudge }) {
  return html`
    <div class="table-scroll">
      <table class="data-table rankings">
        <caption class="visually-hidden">Human and objective rankings per entrant (the scatter's data)</caption>
        <thead>
          <tr>
            <th scope="col">Entrant</th>
            <th scope="col" class="num">Human Elo</th>
            <th scope="col" class="num">People's rank</th>
            <th scope="col" class="num">Pass rate</th>
            <th scope="col" class="num">Checks' rank</th>
            <th scope="col" class="num">Rank gap</th>
            ${hasJudge && html`<th scope="col" class="num">Judge Elo</th>`}
            <th scope="col" class="num">Votes</th>
            <th scope="col" class="num">Trials</th>
          </tr>
        </thead>
        <tbody>
          ${rankings.map(
            (row) => html`
              <tr key=${row.entrant}>
                <th scope="row">${row.entrant}</th>
                <td class="num measure">${formatElo(row.subjective_elo)}</td>
                <td class="num">${formatRank(row.subjective_rank)}</td>
                <td class="num measure">${formatPassRate(row.objective_pass_rate)}</td>
                <td class="num">${formatRank(row.objective_rank)}</td>
                <td class="num">${typeof row.rank_delta === "number" ? row.rank_delta : "None"}</td>
                ${hasJudge && html`<td class="num measure">${formatElo(row.judge_elo)}</td>`}
                <td class="num">${row.n_subjective_votes ?? 0}</td>
                <td class="num">${row.n_objective_trials ?? 0}</td>
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
  `;
}

const MATRIX_ROWS = [
  ["subjective_objective", "People and the objective checks"],
  ["subjective_judge", "People and the VLM judge"],
  ["objective_judge", "Objective checks and the VLM judge"],
];

function MatrixTable({ matrix }) {
  return html`
    <div class="table-scroll">
      <table class="data-table">
        <caption class="visually-hidden">Rank agreement between each pair of signals</caption>
        <thead>
          <tr>
            <th scope="col">Signals compared</th>
            <th scope="col" class="num">ρ</th>
            <th scope="col" class="num">Entrants</th>
            <th scope="col">Reading</th>
          </tr>
        </thead>
        <tbody>
          ${MATRIX_ROWS.map(([key, label]) => {
            const pair = matrix[key] || {};
            return html`
              <tr key=${key}>
                <th scope="row">${label}</th>
                <td class="num measure">${typeof pair.rho === "number" ? pair.rho.toFixed(2) : "None"}</td>
                <td class="num">${pair.n ?? 0}</td>
                <td>${interpretationText(pair.interpretation)}${pair.small_sample ? ". Too few entrants to trust." : ""}</td>
              </tr>
            `;
          })}
        </tbody>
      </table>
    </div>
  `;
}

function AgreementSection({ agreement }) {
  return html`
    <section class="panel agreement-section" aria-labelledby="agreement-title">
      <h2 id="agreement-title">People versus the objective checks</h2>
      <${ResourceBody} resource=${agreement} label="Loading agreement…">
        ${(data) => {
          const pair = data.agreement || {};
          const rankings = data.rankings || [];
          const outliers = findOutliers(rankings);
          return html`
            <p class="agreement-headline">
              <span class="measure">${formatRho(pair.rho)}</span>${` across ${plural(pair.n || 0, "entrant")}. ${interpretationText(pair.interpretation)}.`}
            </p>
            ${pair.small_sample && html`<p class="caveat" role="note">${pair.caveat}</p>`}
            ${data.matrix && html`<${MatrixTable} matrix=${data.matrix} />`}
            ${rankings.length === 0
              ? html`<${Empty} title="No entrant has both a human rating and objective checks yet" />`
              : html`
                  <div class="agreement-layout">
                    <${Scatter} rankings=${rankings} />
                    ${outliers.length > 0 &&
                    html`<div class="outliers">
                      <h3>Ranked very differently</h3>
                      <ul>
                        ${outliers.map(
                          (row) => html`<li key=${row.entrant}>
                            <strong>${row.entrant}</strong>${`: ${formatRank(row.subjective_rank)} with people, ${formatRank(row.objective_rank)} on the checks.`}
                          </li>`,
                        )}
                      </ul>
                    </div>`}
                  </div>
                  <${RankingsTable} rankings=${rankings} hasJudge=${Boolean(data.matrix)} />
                `}
            ${(data.caveats || []).length > 0 &&
            html`<details class="caveats">
              <summary>How to read this</summary>
              <ul>
                ${data.caveats.map((caveat) => html`<li key=${caveat}>${caveat}</li>`)}
              </ul>
            </details>`}
          `;
        }}
      <//>
    </section>
  `;
}

function FamiliesSection({ families }) {
  const [chosen, setChosen] = useState("");
  return html`
    <section class="panel families-section" aria-labelledby="families-title">
      <h2 id="families-title">By instrument family</h2>
      <${ResourceBody} resource=${families} label="Loading families…">
        ${(data) => {
          const entries = Object.entries(data.families || {}).sort(([a], [b]) => a.localeCompare(b));
          if (entries.length === 0) {
            return html`<${Empty} title="No family has votes yet" />`;
          }
          const name = entries.some(([family]) => family === chosen) ? chosen : entries[0][0];
          const slice = data.families[name];
          const pair = slice.agreement?.agreement || {};
          return html`
            <label class="field">
              <span class="field-label">Family</span>
              <select name="family" value=${name} onChange=${(event) => setChosen(event.currentTarget.value)}>
                ${entries.map(([family]) => html`<option key=${family} value=${family}>${family}</option>`)}
              </select>
            </label>
            <p class="agreement-headline">
              ${`${plural(slice.n_trials || 0, "trial")} in ${name}. `}<span class="measure">${formatRho(pair.rho)}</span>${` across ${plural(pair.n || 0, "entrant")}.`}
            </p>
            ${pair.small_sample && html`<p class="caveat" role="note">${pair.caveat}</p>`}
            <${LeaderboardTable} board=${slice} caption=${`Human Elo for the ${name} family`} />
          `;
        }}
      <//>
    </section>
  `;
}

function ExportSection({ runId, summary }) {
  const [stage, setStage] = useState({ step: "idle" });
  const titleRef = useRef(null);
  const exportRef = useRef(null);
  const errorRef = useRef(null);
  const { targets, skipped } = exportTargets(summary.status === "ready" ? runInstruments(summary.data) : []);
  const unavailable = summary.status !== "ready" || targets.length === 0;

  // The confirm, the sending state and the error each replace the control that had
  // focus, so every step says where keyboard focus goes next (never <body>).
  useEffect(() => {
    if (stage.step === "confirm" || stage.step === "done") titleRef.current?.focus();
    else if (stage.step === "error") errorRef.current?.focus();
    else if (stage.step === "idle" && stage.returnFocus) exportRef.current?.focus();
  }, [stage]);

  const cancel = () => setStage({ step: "idle", returnFocus: true });

  const confirm = async () => {
    setStage({ step: "sending" });
    try {
      const result = await api(`/api/runs/${enc(runId)}/export-winners`, { method: "POST" });
      setStage({ step: "done", result });
    } catch (error) {
      setStage({ step: "error", error });
    }
  };

  return html`
    <section class="panel export-section" aria-labelledby="export-title">
      <h2 id="export-title">Export</h2>
      <p class="hint">
        Copies each instrument's best entry into this checkout's <code>instruments/</code> folder, or opens a Markdown
        report of the run.
      </p>
      <div class="actions">
        <button
          type="button"
          class="button"
          ref=${exportRef}
          aria-disabled=${unavailable || stage.step === "sending" ? "true" : "false"}
          onClick=${() => !unavailable && stage.step !== "sending" && setStage({ step: "confirm" })}
        >
          Export winners…
        </button>
        <a class="button button-quiet" href=${`/api/runs/${enc(runId)}/export-report?fmt=markdown`} target="_blank" rel="noopener">
          Open the Markdown report
        </a>
      </div>
      ${stage.step === "confirm" &&
      html`<div
        class="confirm"
        role="group"
        aria-labelledby="export-confirm-title"
        onKeyDown=${(event) => event.key === "Escape" && cancel()}
      >
        <p id="export-confirm-title" class="state-title" tabindex="-1" ref=${titleRef}>
          Overwrite ${plural(targets.length, "file")} in <code>instruments/</code>?
        </p>
        <ul class="path-list">
          ${targets.map((target) => html`<li key=${target.path}><code>${target.path}</code></li>`)}
        </ul>
        ${skipped.length > 0 &&
        html`<p class="hint">The server refuses these instrument ids and skips them: ${skipped.join(", ")}.</p>`}
        <div class="actions">
          <button type="button" class="button button-danger" onClick=${confirm}>
            Overwrite ${plural(targets.length, "file")}
          </button>
          <button type="button" class="button button-quiet" onClick=${cancel}>Cancel</button>
        </div>
      </div>`}
      ${stage.step === "sending" && html`<${Loading} label="Exporting winners…" />`}
      ${stage.step === "error" &&
      html`<div class="export-error" tabindex="-1" ref=${errorRef}>
        <${ErrorState} error=${stage.error} onRetry=${confirm} />
      </div>`}
      ${stage.step === "done" &&
      html`<div class="export-result">
        <p class="state-title" tabindex="-1" ref=${titleRef} role="status">
          Exported ${plural(stage.result.exported_count || 0, "winner")}.
        </p>
        <ul class="path-list">
          ${(stage.result.winners || []).map(
            (winner) => html`<li key=${winner.exported_path}>
              <code>${winner.exported_path}</code>: ${winner.model_id} for ${winner.instrument_id}
            </li>`,
          )}
        </ul>
        ${(stage.result.skipped || []).length > 0 &&
        html`<p class="hint">
          Skipped: ${stage.result.skipped.map((item) => `${item.instrument_id} (${item.reason})`).join(", ")}.
        </p>`}
      </div>`}
    </section>
  `;
}

function AnalyticsForRun({ runId }) {
  const base = `/api/runs/${enc(runId)}`;
  const board = useResource(`${base}/leaderboard/ci`);
  const agreement = useResource(`${base}/agreement/detailed`);
  const families = useResource(`${base}/agreement/families`);
  const summary = useResource(`${base}/summary`);
  const refresh = () => [board, agreement, families, summary].forEach((resource) => resource.reload());

  return html`
    <div class="screen screen-analytics">
      <h1 tabindex="-1">Agreement analytics</h1>
      <div class="lede-row">
        <p class="lede">
          How people's blind votes on <strong>${runId}</strong> line up with the objective checks.
        </p>
        <button type="button" class="button button-quiet" onClick=${refresh}>Refresh</button>
      </div>
      <${EloSection} board=${board} />
      <${AgreementSection} agreement=${agreement} />
      <${FamiliesSection} families=${families} />
      <${ExportSection} runId=${runId} summary=${summary} />
    </div>
  `;
}

export function AnalyticsScreen({ route }) {
  const runId = route.args[0] || null;
  if (!runId) {
    return html`
      <div class="screen">
        <h1 tabindex="-1">Agreement analytics</h1>
        <${Empty} title="Choose a run">
          <p>Pick a run in the header to see how people's votes compare with the objective checks.</p>
        <//>
      </div>
    `;
  }
  return html`<${AnalyticsForRun} key=${runId} runId=${runId} />`;
}
