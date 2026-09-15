import { useCallback, useEffect, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { plural } from "../lib/format.js";
import { costBadge, describeReference, parseModelList } from "../lib/launch.js";
import {
  budgetView,
  ceilingsPayload,
  CONTEXT_TIERS,
  doeBlockers,
  formatDuration,
  formatUsd,
  LEVELS,
  parseSeeds,
  predictedSkips,
  previewQuery,
  skipReasonText,
  unknownCostModels,
} from "../lib/doe.js";
import { useResource } from "../hooks/useResource.js";
import { Empty, ErrorState, Loading } from "../components/states.js";

const enc = encodeURIComponent;
const MAX_JOB_ROWS = 40;

function useDebounced(value, delay) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

function useReferences() {
  const [references, setReferences] = useState({});
  const requested = useRef(new Set());
  const load = useCallback(async (taskId) => {
    if (requested.current.has(taskId)) return;
    requested.current.add(taskId);
    setReferences((prev) => ({ ...prev, [taskId]: { status: "loading", data: null } }));
    try {
      const data = await api(`/api/tasks/${enc(taskId)}/reference`);
      setReferences((prev) => ({ ...prev, [taskId]: { status: "ready", data } }));
    } catch (error) {
      requested.current.delete(taskId);
      setReferences((prev) => ({ ...prev, [taskId]: { status: "error", error } }));
    }
  }, []);
  return [references, load];
}

function toggleIn(list, value) {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

function InstrumentPicker({ tasks, selected, onToggle, references }) {
  if (tasks.status === "loading" || tasks.status === "idle") return html`<${Loading} label="Loading instruments…" />`;
  if (tasks.status === "error") return html`<${ErrorState} error=${tasks.error} onRetry=${tasks.reload} />`;
  const list = tasks.data.tasks || [];
  if (list.length === 0) return html`<${Empty} title="The instrument registry is empty" />`;
  return html`
    <fieldset class="choice-group">
      <legend class="field-label">Instruments (${selected.length} of ${list.length})</legend>
      <div class="check-list">
        ${list.map((task) => {
          const checked = selected.includes(task.id);
          const gate = describeReference(references[task.id]);
          return html`
            <label class="check-row" key=${task.id}>
              <input type="checkbox" name="instrument" value=${task.id} checked=${checked} onChange=${() => onToggle(task.id)} />
              <span class="check-text">
                <span class="task-name">${task.display_name || task.id}</span>
                ${task.family && html`<span class="task-family">${task.family}</span>`}
              </span>
              <span class="gate" data-kind=${checked ? gate.kind : "unchecked"}>${checked ? gate.label : ""}</span>
            </label>
          `;
        })}
      </div>
    </fieldset>
  `;
}

function ChoiceChips({ legend, name, options, selected, onToggle }) {
  return html`
    <fieldset class="choice-group">
      <legend class="field-label">${legend}</legend>
      <div class="chip-row">
        ${options.map(
          (option) => html`
            <label class="chip" key=${option.id}>
              <input
                type="checkbox"
                name=${name}
                value=${option.id}
                checked=${selected.includes(option.id)}
                onChange=${() => onToggle(option.id)}
              />
              <span>${option.label}</span>
            </label>
          `,
        )}
      </div>
    </fieldset>
  `;
}

function ModelEstimates({ cells }) {
  const byModel = new Map();
  for (const cell of cells) if (!byModel.has(cell.model_id)) byModel.set(cell.model_id, cell.estimate || {});
  return html`
    <div class="table-scroll">
      <table class="data-table model-estimates">
        <caption class="visually-hidden">What one trial of each entrant costs and takes, from history</caption>
        <thead>
          <tr>
            <th scope="col">Entrant</th>
            <th scope="col">Cost per trial</th>
            <th scope="col" class="num">Typical time</th>
          </tr>
        </thead>
        <tbody>
          ${[...byModel.entries()].map(([model, estimate]) => {
            const badge = costBadge(estimate);
            return html`
              <tr key=${model}>
                <th scope="row"><code>${model}</code></th>
                <td><span class="cost" data-kind=${badge.kind}>${badge.label}</span></td>
                <td class="num">${formatDuration(estimate.duration_s)}</td>
              </tr>
            `;
          })}
        </tbody>
      </table>
    </div>
  `;
}

const JOB_STATUS_TEXT = { within: "Within budget", over: "Over budget", unknown: "Unknown cost" };

function BudgetWhatIf({ cells, budget, onBudget }) {
  const budgetNumber = Number(budget);
  const valid = budget !== "" && budgetNumber >= 0;
  const view = budgetView(cells, valid ? budgetNumber : 0);
  const sliderMax = Math.max(50, valid ? Math.ceil(budgetNumber) : 0);
  return html`
    <div class="budget">
      <label class="field">
        <span class="field-label">Budget per nightly job (USD)</span>
        <input
          type="text"
          name="budget"
          inputmode="decimal"
          size="7"
          value=${budget}
          onInput=${(event) => onBudget(event.currentTarget.value.trim())}
        />
      </label>
      <input
        type="range"
        name="budget-slider"
        min="0"
        max=${sliderMax}
        step="0.25"
        value=${valid ? budgetNumber : 0}
        aria-label="Budget per nightly job"
        aria-valuetext=${valid ? formatUsd(budgetNumber) : "Not set"}
        onInput=${(event) => onBudget(event.currentTarget.value)}
      />
    </div>
    <p class="budget-summary" role="status">
      ${`${plural(view.within, "job")} within ${valid ? formatUsd(budgetNumber) : "the budget"}, ${plural(view.over, "job")} over, and ${plural(view.unknown, "job")} with unknown cost, which never count as affordable.`}
    </p>
    <div class="table-scroll">
      <table class="data-table jobs-what-if">
        <caption class="visually-hidden">Each nightly job's known spend against the budget</caption>
        <thead>
          <tr>
            <th scope="col">Instrument</th>
            <th scope="col" class="num">Seed</th>
            <th scope="col">Context</th>
            <th scope="col" class="num">Known spend</th>
            <th scope="col">Against the budget</th>
          </tr>
        </thead>
        <tbody>
          ${view.jobs.slice(0, MAX_JOB_ROWS).map(
            (job) => html`
              <tr key=${`${job.instrument}|${job.seed}|${job.tier}`}>
                <th scope="row">${job.instrument}</th>
                <td class="num">${job.seed}</td>
                <td>${job.tier}</td>
                <td class="num measure">${formatUsd(job.knownUsd)}</td>
                <td class="job-status" data-status=${job.status}>
                  ${JOB_STATUS_TEXT[job.status]}${job.status === "unknown" ? `: ${job.unknownModels.join(", ")}` : ""}
                </td>
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
    ${view.jobs.length > MAX_JOB_ROWS && html`<p class="hint">Showing ${MAX_JOB_ROWS} of ${view.jobs.length} jobs.</p>`}
  `;
}

function PreviewPanel({ query, preview, stale, budget, onBudget, skips }) {
  let body;
  if (!query) {
    body = html`<p class="hint">Choose instruments, entrants, levels, context tiers and seeds to preview the matrix.</p>`;
  } else if (stale || preview.status === "loading" || preview.status === "idle") {
    body = html`<${Loading} label="Estimating the matrix…" />`;
  } else if (preview.status === "error") {
    body = html`<${ErrorState} error=${preview.error} onRetry=${preview.reload} />`;
  } else {
    const { cells = [], summary = {} } = preview.data;
    const jobs = budgetView(cells, 0).jobs.length;
    body = html`
      <dl class="facts">
        <div><dt>Cells</dt><dd class="measure">${summary.n_cells ?? cells.length}</dd></div>
        <div><dt>Nightly jobs</dt><dd class="measure">${jobs}</dd></div>
        <div>
          <dt>Known cost</dt>
          <dd class="measure">
            ${formatUsd(summary.known_cost_usd)}
            ${summary.has_unknown_cost_cells && html`<span class="fact-note">plus cells with unknown cost</span>`}
          </dd>
        </div>
        <div>
          <dt>Known time</dt>
          ${summary.has_unknown_time_cells && !summary.known_time_s
            ? html`<dd class="unknown">No timing history</dd>`
            : html`<dd class="measure">
                ${formatDuration(summary.known_time_s)}
                ${summary.has_unknown_time_cells && html`<span class="fact-note">plus cells with no timing history</span>`}
              </dd>`}
        </div>
      </dl>
      <h3>Entrants</h3>
      <${ModelEstimates} cells=${cells} />
      <h3>Budget what-if</h3>
      <${BudgetWhatIf} cells=${cells} budget=${budget} onBudget=${onBudget} />
    `;
  }
  return html`
    <section class="panel doe-preview" aria-labelledby="doe-preview-title" aria-busy=${stale ? "true" : "false"}>
      <h2 id="doe-preview-title">Preview</h2>
      ${body}
      ${skips.length > 0 &&
      html`<div class="skips">
        <h3>Instruments the queue will skip</h3>
        <ul>
          ${skips.map((skip) => html`<li key=${skip.instrument}><strong>${skip.instrument}</strong>: ${skipReasonText(skip.reason)}</li>`)}
        </ul>
      </div>`}
    </section>
  `;
}

export function DoeScreen() {
  const tasks = useResource("/api/tasks");
  const [references, loadReference] = useReferences();
  const [instruments, setInstruments] = useState([]);
  const [modelsText, setModelsText] = useState("");
  const [levels, setLevels] = useState([...LEVELS]);
  const [tiers, setTiers] = useState(["blind"]);
  const [seedsText, setSeedsText] = useState("0");
  const [runId, setRunId] = useState("");
  const [budget, setBudget] = useState("5");
  const [ceilings, setCeilings] = useState({});
  const [write, setWrite] = useState({ status: "idle" });
  const writeRef = useRef(null);
  const replaceRef = useRef(null);
  // The replace confirm and its dismissal each unmount the focused control.
  useEffect(() => {
    if (write.status === "exists") replaceRef.current?.focus();
    else if (write.status === "idle" && write.returnFocus) writeRef.current?.focus();
  }, [write]);

  const models = parseModelList(modelsText);
  const { seeds, invalid: invalidSeeds } = parseSeeds(seedsText);
  const matrix = { instruments, models, levels, tiers, seeds };
  const query = invalidSeeds.length ? null : previewQuery(matrix);
  const settledQuery = useDebounced(query, 350);
  const preview = useResource(settledQuery ? `/api/doe/preview?${settledQuery}` : null);
  const stale = query !== settledQuery;
  const previewState = !query ? "none" : stale || preview.status !== "ready" ? (preview.status === "error" && !stale ? "error" : "loading") : "ready";
  const cells = previewState === "ready" ? preview.data.cells || [] : [];
  const unknownModels = unknownCostModels(cells);
  const skips = predictedSkips(instruments, references, models.length * levels.length);
  const blockers = doeBlockers({ matrix, invalidSeeds, runId, budget, preview: previewState, unknownModels, ceilings });
  const busy = write.status === "sending";

  const toggleInstrument = (taskId) => {
    setInstruments((prev) => toggleIn(prev, taskId));
    loadReference(taskId);
  };

  const send = async (replace) => {
    if (busy || blockers.length) return;
    setWrite({ status: "sending" });
    try {
      const result = await api("/api/doe/queue", {
        method: "POST",
        body: {
          run_id: runId,
          instruments,
          models,
          levels,
          context_tiers: tiers,
          seeds,
          budget_usd: Number(budget),
          max_cost_usd_by_model: ceilingsPayload(unknownModels, ceilings),
          replace,
        },
      });
      setWrite({ status: "done", result });
    } catch (error) {
      // 409: a queue already exists for this run name. Ask before replacing it.
      setWrite(error.status === 409 ? { status: "exists", error } : { status: "error", error });
    }
  };
  const submit = (event) => {
    event.preventDefault();
    send(false);
  };
  const keepExisting = () => setWrite({ status: "idle", returnFocus: true });

  return html`
    <div class="screen screen-doe">
      <h1 tabindex="-1">DoE matrix</h1>
      <p class="lede">
        Design a nightly experiment: instruments × entrants × levels × context tiers × seeds. See what it costs, then write
        the queue. Nothing runs from this page.
      </p>
      <div class="doe-layout">
        <section class="panel doe-design" aria-labelledby="doe-design-title">
          <h2 id="doe-design-title">Design the matrix</h2>
          <${InstrumentPicker} tasks=${tasks} selected=${instruments} onToggle=${toggleInstrument} references=${references} />
          <label class="field">
            <span class="field-label">Entrants</span>
            <textarea
              name="models"
              rows="3"
              spellcheck="false"
              autocomplete="off"
              placeholder="claude-code-opus-5, codex-gpt-5.6"
              value=${modelsText}
              onInput=${(event) => setModelsText(event.currentTarget.value)}
            ></textarea>
            <span class="field-note">Model ids, separated by commas or new lines.</span>
          </label>
          <${ChoiceChips}
            legend="Levels"
            name="level"
            options=${LEVELS.map((level) => ({ id: level, label: level }))}
            selected=${levels}
            onToggle=${(level) => setLevels((prev) => LEVELS.filter((item) => toggleIn(prev, level).includes(item)))}
          />
          <${ChoiceChips}
            legend="Context tiers"
            name="context_tier"
            options=${CONTEXT_TIERS}
            selected=${tiers}
            onToggle=${(tier) => setTiers((prev) => toggleIn(prev, tier))}
          />
          <label class="field">
            <span class="field-label">Seeds</span>
            <input
              type="text"
              name="seeds"
              spellcheck="false"
              autocomplete="off"
              value=${seedsText}
              onInput=${(event) => setSeedsText(event.currentTarget.value)}
            />
            <span class="field-note">Whole numbers, separated by commas.</span>
          </label>
        </section>
        <${PreviewPanel}
          query=${query}
          preview=${preview}
          stale=${stale}
          budget=${budget}
          onBudget=${setBudget}
          skips=${skips}
        />
      </div>

      <section class="panel doe-write" aria-labelledby="doe-write-title">
        <h2 id="doe-write-title">Write the nightly queue</h2>
        <form class="doe-write-form" onSubmit=${submit} noValidate>
          <label class="field">
            <span class="field-label">Run name</span>
            <input
              type="text"
              name="run_id"
              spellcheck="false"
              autocomplete="off"
              placeholder="doe-2026-09-15"
              value=${runId}
              onInput=${(event) => setRunId(event.currentTarget.value.trim())}
            />
          </label>
          <p class="hint">
            Writes <code>${`runs/code_cad_arena/${runId || "<run name>"}/doe_queue.json`}</code> for the separate nightly
            runner to read later.
          </p>
          ${unknownModels.length > 0 &&
          html`<fieldset class="choice-group ceilings">
            <legend class="field-label">Cost ceilings for entrants with unknown cost</legend>
            <p class="hint">No history says what these cost, so set the most one trial may spend.</p>
            ${unknownModels.map(
              (model) => html`
                <label class="field" key=${model}>
                  <span class="field-label"><code>${model}</code>, USD per trial</span>
                  <input
                    type="text"
                    name=${`ceiling-${model}`}
                    inputmode="decimal"
                    size="7"
                    value=${ceilings[model] || ""}
                    onInput=${(event) => {
                      const value = event.currentTarget.value.trim();
                      setCeilings((prev) => ({ ...prev, [model]: value }));
                    }}
                  />
                </label>
              `,
            )}
          </fieldset>`}
          ${blockers.length > 0 &&
          html`<ul class="blockers" aria-label="Before you can write the queue">
            ${blockers.map((blocker) => html`<li key=${blocker}>${blocker}</li>`)}
          </ul>`}
          <div class="actions">
            <button type="submit" class="button" ref=${writeRef} aria-disabled=${busy || blockers.length ? "true" : "false"}>
              Write nightly queue
            </button>
          </div>
        </form>
        ${busy && html`<${Loading} label="Writing the queue…" />`}
        ${write.status === "exists" &&
        html`<div
          class="doe-replace"
          role="group"
          aria-labelledby="doe-replace-title"
          onKeyDown=${(event) => event.key === "Escape" && keepExisting()}
        >
          <p id="doe-replace-title" class="state-title" tabindex="-1" ref=${replaceRef}>
            ${"A queue already exists at "}<code>${`runs/code_cad_arena/${runId}/doe_queue.json`}</code>${". Replace it?"}
          </p>
          <p class="hint">The nightly runner may already be working through its jobs. Replacing it resets every job's status.</p>
          <div class="actions">
            <button type="button" class="button button-danger" onClick=${() => send(true)}>Replace the queue</button>
            <button type="button" class="button button-quiet" onClick=${keepExisting}>Keep the existing queue</button>
          </div>
        </div>`}
        ${write.status === "error" && html`<${ErrorState} error=${write.error} />`}
        ${write.status === "done" &&
        html`<div class="export-result doe-result" role="status">
          <p class="state-title">
            Wrote ${plural(write.result.n_jobs || 0, "nightly job")} to <code>${write.result.queue_path}</code>. Nothing
            has run.
          </p>
          ${(write.result.skipped || []).length > 0 &&
          html`<ul>
            ${write.result.skipped.map(
              (skip, index) => html`<li key=${index}><strong>${skip.instrument_id}</strong>: ${skipReasonText(skip.reason)}</li>`,
            )}
          </ul>`}
        </div>`}
      </section>
    </div>
  `;
}
