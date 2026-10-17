// Control Room New Run (#1114): numbered steps on the left, a sticky run summary and
// launch rail on the right. "Add to tonight" writes the nightly queue only when the
// Studio was started with --control-room-queue-writes; otherwise it saves a draft.

import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { plural } from "../lib/format.js";
import {
  MAX_REFERENCE_BYTES,
  PRESETS,
  FILTER_LABELS,
  blockers,
  cardDetail,
  cellReason,
  chooseInstrument,
  clampTurns,
  duration,
  filterInstruments,
  freshForm,
  parseVariants,
  payload,
  togglePick,
  usd,
  warnings,
} from "../lib/newrun.js";
import { useResource } from "../hooks/useResource.js";
import { ErrorState, Loading } from "../components/states.js";
import { ControlRoomTabs } from "../components/controlRoomTabs.js";

const DRAFT_KEY = "control-room.new-run-draft";

function loadDraft() {
  try {
    return JSON.parse(globalThis.localStorage?.getItem(DRAFT_KEY) || "null");
  } catch {
    return null;
  }
}

function saveDraft(form) {
  try {
    // Uploaded images stay out of storage: they can be large.
    globalThis.localStorage?.setItem(DRAFT_KEY, JSON.stringify({ ...form, references: [] }));
  } catch {
    // Not kept; the form still works.
  }
}

function Step({ n, title, sub, children }) {
  return html`
    <section class="newrun-step" aria-labelledby=${`newrun-step-${n}`}>
      <h2 id=${`newrun-step-${n}`}><span class="newrun-num">${n}</span>${title}${sub && html` <span class="newrun-sub">${sub}</span>`}</h2>
      ${children}
    </section>
  `;
}

function Hero({ item }) {
  const [failed, setFailed] = useState(false);
  if (!item.hero || failed) return html`<span class="newrun-hero newrun-hero-empty" aria-hidden="true">${(item.name || "?").slice(0, 1)}</span>`;
  return html`<img class="newrun-hero" src=${item.hero} alt="" loading="lazy" onError=${() => setFailed(true)} />`;
}

function LibraryStatus({ library, onRefresh, refreshing }) {
  const when = library?.fetched_utc ? new Date(library.fetched_utc) : null;
  return html`
    <p class="hint newrun-library" data-testid="library-status">
      ${library
        ? html`From the instrument library, refreshed ${when && !Number.isNaN(when.getTime()) ? when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "at an unknown time"}.`
        : "No library snapshot yet: showing the arena registry."}
      ${" "}<button type="button" class="button-link" disabled=${refreshing} onClick=${onRefresh}>${refreshing ? "Refreshing…" : "Refresh from the library"}</button>
    </p>
  `;
}

function InstrumentStep({ catalog, form, setForm, onRefresh, refreshing }) {
  const [filters, setFilters] = useState({});
  const [query, setQuery] = useState("");
  const shown = useMemo(() => filterInstruments(catalog.instruments, filters, query), [catalog, filters, query]);
  const options = catalog.library?.filters || {};
  return html`
    <${Step} n="1" title="Instrument" sub=${`${shown.length} of ${catalog.instruments.length}`}>
      <${LibraryStatus} library=${catalog.library} onRefresh=${onRefresh} refreshing=${refreshing} />
      <div class="newrun-filters">
        <label class="field">
          <span class="field-label">Search</span>
          <input type="search" value=${query} placeholder="Name, family or type" onInput=${(event) => setQuery(event.currentTarget.value)} />
        </label>
        ${Object.entries(FILTER_LABELS).filter(([key]) => options[key]?.length).map(
          ([key, label]) => html`<label class="field" key=${key}>
            <span class="field-label">${label}</span>
            <select aria-label=${label} value=${filters[key] || "all"} onChange=${(event) => { const v = event.currentTarget.value; setFilters((f) => ({ ...f, [key]: v })); }}>
              <option value="all">All</option>
              ${options[key].map((value) => html`<option key=${value} value=${value}>${value}</option>`)}
            </select>
          </label>`,
        )}
      </div>
      <div class="newrun-instruments" role="group" aria-label="Instrument">
        ${shown.length === 0 && html`<p class="hint">No instrument matches these filters.</p>`}
        ${shown.map(
          (item) => html`
            <button
              key=${item.id}
              type="button"
              class="newrun-instrument"
              data-instrument=${item.id}
              aria-pressed=${form.instrument === item.id ? "true" : "false"}
              onClick=${() => setForm((f) => chooseInstrument(f, catalog, item.id))}
            >
              <${Hero} item=${item} />
              <b>${item.name}</b>
              <span>${cardDetail(item) || (item.assembly ? "assembly" : "single part")}</span>
              <span class="newrun-badges">
                ${item.cad && html`<span class="check-chip" data-state=${item.cad === "none" ? "absent" : "pass"}>CAD · ${item.cad}</span>`}
                ${catalog.library && html`<span class="check-chip" data-state=${item.arena_task ? "pass" : "unknown"}>${item.arena_task ? "Arena task" : "No arena task"}</span>`}
              </span>
            </button>
          `,
        )}
      </div>
    <//>
  `;
}

function VariantStep({ form, setForm }) {
  const [text, setText] = useState(form.variants.join(", "));
  useEffect(() => setText(form.variants.join(", ")), [form.instrument]);
  return html`
    <${Step} n="2" title="Variant and deliverable">
      <label class="field">
        <span class="field-label">Variants to sweep (optional, comma-separated)</span>
        <input
          type="text"
          class="wide-input"
          value=${text}
          placeholder="For example: D4, A4"
          onInput=${(event) => setText(event.currentTarget.value)}
          onChange=${(event) => setForm((f) => ({ ...f, preset: null, variants: parseVariants(event.currentTarget.value) }))}
        />
      </label>
      <p class="hint">Each variant is its own night job. The instrument repo's variant list arrives with #1109.</p>
      <div class="newrun-seg" role="group" aria-label="Deliverable">
        ${[["single", "Single part"], ["assembly", "Wood-shop assembly"]].map(
          ([id, label]) => html`<button key=${id} type="button" aria-pressed=${form.deliverable === id ? "true" : "false"}
            onClick=${() => setForm((f) => ({ ...f, preset: null, deliverable: id }))}>${label}</button>`,
        )}
      </div>
      <p class="hint">
        ${form.deliverable === "assembly"
          ? "Assembly: separate parts you can cut in the shop, a cut list, and every key dimension editable."
          : "Single part: one solid body, graded with the five standard checks."}
      </p>
    <//>
  `;
}

function EntrantStep({ catalog, form, setForm }) {
  return html`
    <${Step} n="3" title="Entrants" sub="pick tool × model">
      <div class="table-scroll">
        <table class="data-table newrun-grid">
          <caption class="visually-hidden">Entrant grid: tools by models</caption>
          <thead>
            <tr><th scope="col">Tool</th>${catalog.models.map((m) => html`<th key=${m.id} scope="col">${m.name}</th>`)}</tr>
          </thead>
          <tbody>
            ${catalog.tools.map(
              (tool) => html`
                <tr key=${tool.id}>
                  <th scope="row">${tool.name}<small>${tool.detail}${tool.backend?.status === "unavailable" ? ` · ${tool.backend.reason}` : ""}</small></th>
                  ${catalog.models.map((model) => {
                    const pick = `${tool.id}:${model.id}`;
                    const reason = cellReason(catalog, pick);
                    const on = form.entrants.includes(pick);
                    return html`<td key=${model.id}>
                      <button
                        type="button"
                        class="newrun-cell"
                        data-pick=${pick}
                        aria-pressed=${on ? "true" : "false"}
                        disabled=${reason !== null}
                        title=${reason || `${tool.name} with ${model.name}`}
                        aria-label=${`${tool.name} with ${model.name}${reason ? " (unavailable)" : ""}`}
                        onClick=${() => setForm((f) => togglePick(f, pick))}
                      >${on ? "✓" : ""}</button>
                    </td>`;
                  })}
                </tr>
              `,
            )}
          </tbody>
        </table>
      </div>
      <p class="hint">Disabled cells say why on hover. SolidWorks and Fusion share your one CAD seat, so they run one after the other.</p>
    <//>
  `;
}

function ReferenceStep({ form, setForm, onToast }) {
  const input = useRef(null);
  const [taskImage, setTaskImage] = useState(true);
  useEffect(() => setTaskImage(true), [form.instrument]);
  const add = (files) => {
    for (const file of [...files].filter((f) => /^image\/(png|jpeg|webp)$/.test(f.type))) {
      if (file.size > MAX_REFERENCE_BYTES) {
        onToast(`${file.name} is over 10 MB.`);
        continue;
      }
      const reader = new FileReader();
      reader.onload = () => setForm((f) => ({ ...f, references: [...f.references, { name: file.name, data: reader.result }] }));
      reader.readAsDataURL(file);
    }
  };
  return html`
    <${Step} n="4" title="Reference images and brief">
      <div class="newrun-refs">
        ${form.instrument && form.references.length === 0 && taskImage &&
        html`<figure class="newrun-ref">
          <img src=${`/api/tasks/${encodeURIComponent(form.instrument)}/reference/image`} alt="The instrument's reference image" onError=${() => setTaskImage(false)} />
          <figcaption>From the task</figcaption>
        </figure>`}
        ${form.references.map(
          (ref, index) => html`<figure class="newrun-ref" key=${ref.name + index}>
            <img src=${ref.data} alt=${`Reference image ${ref.name}`} />
            <button type="button" aria-label=${`Remove ${ref.name}`} onClick=${() => setForm((f) => ({ ...f, references: f.references.filter((_, i) => i !== index) }))}>✕</button>
          </figure>`,
        )}
        <button type="button" class="newrun-drop" onClick=${() => input.current?.click()}
          onDragOver=${(event) => event.preventDefault()}
          onDrop=${(event) => { event.preventDefault(); add(event.dataTransfer.files); }}>
          Drop photos or drawings here, or click to add
        </button>
        <input ref=${input} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange=${(event) => add(event.currentTarget.files)} />
      </div>
      <label class="field" for="newrun-brief"><span class="field-label">Brief for every entrant</span></label>
      <textarea id="newrun-brief" class="review-note newrun-brief" value=${form.brief} onInput=${(event) => {
        const value = event.currentTarget.value;
        setForm((f) => ({ ...f, brief: value }));
      }}></textarea>
    <//>
  `;
}

function BudgetStep({ catalog, form, setForm }) {
  return html`
    <${Step} n="5" title="Budget and turns">
      <div class="newrun-row">
        <label class="field">
          <span class="field-label">Revision turns per design: <b>${form.turns}</b></span>
          <input type="range" min="1" max=${catalog.max_turns} value=${form.turns}
            onInput=${(event) => { const v = clampTurns(event.currentTarget.value, catalog.max_turns); setForm((f) => ({ ...f, turns: v })); }} />
        </label>
        <label class="field">
          <span class="field-label">OpenRouter cap ($)</span>
          <input type="number" min="0" max="100" step="0.5" value=${form.cap_usd}
            onInput=${(event) => { const v = Math.max(0, Number(event.currentTarget.value) || 0); setForm((f) => ({ ...f, cap_usd: v })); }} />
        </label>
        <label class="field-check">
          <input type="checkbox" checked=${form.stop_early} onChange=${(event) => { const v = event.currentTarget.checked; setForm((f) => ({ ...f, stop_early: v })); }} />
          Stop early once every check passes
        </label>
      </div>
    <//>
  `;
}

function useEstimate(form) {
  const [state, setState] = useState({ status: "idle", data: null, error: null });
  const body = JSON.stringify({ ...payload(form), references: [] });
  useEffect(() => {
    if (form.entrants.length === 0) {
      setState({ status: "idle", data: null, error: null });
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setState((prev) => ({ ...prev, status: "loading" }));
      api("/api/newrun/estimate", { method: "POST", body: JSON.parse(body), signal: controller.signal })
        .then((data) => setState({ status: "ready", data, error: null }))
        .catch((error) => error?.name !== "AbortError" && setState({ status: "error", data: null, error }));
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [body]);
  return state;
}

function Rail({ catalog, form, estimate, onTonight, onRunNow, busy }) {
  const e = estimate.data;
  const instrument = catalog.instruments.find((item) => item.id === form.instrument);
  const block = blockers(form, e);
  const notes = warnings(form, catalog, e);
  return html`
    <aside class="newrun-rail" aria-label="Run summary">
      <div class="newrun-box" data-testid="newrun-summary">
        <h2>This run</h2>
        <p class="newrun-big"><b>${e ? e.designs : 0}</b> designs</p>
        <dl class="newrun-kv">
          <dt>Instrument</dt><dd>${instrument?.name || "none"}${form.variants.length ? ` · ${form.variants.join(", ")}` : ""}</dd>
          <dt>Deliverable</dt><dd>${form.deliverable === "assembly" ? "Assembly" : "Single part"}</dd>
          <dt>Entrants</dt><dd>${form.entrants.length}</dd>
          <dt>Turns each</dt><dd>${form.turns}</dd>
          <dt>OpenRouter spend</dt>
          <dd data-testid="spend">${e ? `${e.openrouter_complete ? "about" : "at least"} ${usd(e.openrouter_usd)} of ${usd(Number(form.cap_usd))}` : form.entrants.length ? "estimating…" : "pick entrants"}</dd>
          <dt>Subscription designs</dt>
          <dd>${e ? `Claude ${e.subscription_designs.claude} · Codex ${e.subscription_designs.codex}` : "none"}</dd>
          <dt>Run time</dt><dd>${e ? duration(e.wall_time_s) : "unknown"}</dd>
        </dl>
        ${estimate.status === "error" && html`<p class="review-fail-note" role="alert">${estimate.error.message}</p>`}
        ${e && html`<details class="newrun-per">
          <summary>Per entrant</summary>
          <ul>${e.entrants.map((row) => html`<li key=${row.entrant}>${row.tool} · ${row.model}: ${row.route === "subscription" ? "subscription" : usd(row.cost_usd)}, ${duration(row.duration_s)}</li>`)}</ul>
        </details>`}
        ${[...block, ...notes].map((text) => html`<p class="newrun-warn" key=${text}>${text}</p>`)}
        ${e?.runner_gaps?.length > 0 && html`<p class="newrun-warn" data-testid="runner-gaps">
          The nightly runner can't apply yet: ${e.runner_gaps.join("; ")}. ${catalog.queue_writes ? "This run is staged, not queued." : ""}
        </p>`}
      </div>
      <div class="newrun-box">
        <h2>Ready check</h2>
        <ul class="newrun-lights">
          ${catalog.lights.map((light) => html`<li key=${light.text} data-state=${light.state}><span class="newrun-dot"></span>${light.text}</li>`)}
        </ul>
      </div>
      <div class="newrun-go">
        <button type="button" class="button" disabled=${busy || block.length > 0} onClick=${onTonight}>Add to tonight</button>
        <button type="button" class="button button-quiet" disabled=${busy || !catalog.allow_live || block.length > 0 || e?.run_now_gaps?.length > 0} onClick=${onRunNow}
          title=${catalog.allow_live ? "Starts now on this PC" : "Run now needs a Studio started with --allow-live"}>Run now</button>
        ${!catalog.allow_live && html`<p class="hint">Run now needs a Studio started with <code>--allow-live</code>.</p>`}
        ${catalog.allow_live && e?.run_now_gaps?.length > 0 && html`<p class="hint" data-testid="run-now-hint">
          Run now uses the default brief and one variant; use Add to tonight for custom settings (${e.run_now_gaps.join("; ")}).</p>`}
      </div>
    </aside>
  `;
}

function Form({ catalog, onRefresh, refreshing }) {
  const [form, setForm] = useState(() => {
    const draft = loadDraft();
    return draft && catalog.instruments.some((item) => item.id === draft.instrument) ? draft : freshForm(catalog);
  });
  const [toast, setToast] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const dialog = useRef(null);
  const estimate = useEstimate(form);

  useEffect(() => saveDraft(form), [form]);
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (result) dialog.current?.showModal?.();
  }, [result]);

  const submit = async (path) => {
    setBusy(true);
    try {
      const data = await api(path, { method: "POST", body: payload(form) });
      if (data && data.success === false) {
        // The launcher refused (e.g. the reference image is not approved): never "Run started".
        setToast(`Not started: ${data.error || data.message || "the launcher refused this run."}`);
        return;
      }
      setResult({ path, data });
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy(false);
    }
  };

  const queued = result?.data?.queued;
  return html`
    <div class="newrun-presets" role="group" aria-label="Presets">
      <span class="hint">Start from a preset</span>
      ${Object.entries(PRESETS).map(
        ([id, preset]) => html`<button key=${id} type="button" class="review-night newrun-pill" aria-pressed=${form.preset === id ? "true" : "false"}
          onClick=${() => { setForm(freshForm(catalog, id)); setToast("Preset loaded. Change anything you like."); }}>${preset.label}</button>`,
      )}
      <button type="button" class="review-night newrun-pill" disabled title="Start this from a design card in Morning Review">Revise my favourite</button>
    </div>
    <div class="newrun-layout">
      <div class="newrun-steps">
        <${InstrumentStep} catalog=${catalog} form=${form} setForm=${setForm} onRefresh=${onRefresh} refreshing=${refreshing} />
        <${VariantStep} form=${form} setForm=${setForm} />
        <${EntrantStep} catalog=${catalog} form=${form} setForm=${setForm} />
        <${ReferenceStep} form=${form} setForm=${setForm} onToast=${setToast} />
        <${BudgetStep} catalog=${catalog} form=${form} setForm=${setForm} />
      </div>
      <${Rail} catalog=${catalog} form=${form} estimate=${estimate} busy=${busy}
        onTonight=${() => submit("/api/newrun/tonight")} onRunNow=${() => submit("/api/newrun/run-now")} />
    </div>
    <p class="review-toast" role="status" aria-live="polite" hidden=${!toast}>${toast || ""}</p>
    <dialog class="review-dialog" ref=${dialog} onClose=${() => setResult(null)}>
      ${result && html`
        <h2>${result.path.endsWith("run-now") ? "Run started" : queued ? "Added to tonight" : result.data.staged_because?.length ? "Staged, not queued" : "Saved as a draft"}</h2>
        <p>
          ${result.path.endsWith("run-now")
            ? "The arena run is starting on this PC."
            : queued
              ? `${plural(result.data.added, "job")} added to the nightly queue. Nightly runs still wait for your GO (#664).`
              : result.data.staged_because?.length
                ? `The nightly runner can't apply ${result.data.staged_because.join("; ")}, so this run was not queued. It is saved at runs/${result.data.draft} with its plan.`
                : `Queue writes are off, so the nightly queue was not changed. What would have been queued is saved at runs/${result.data.draft}.`}
        </p>
        <pre class="review-json">${JSON.stringify(result.data.jobs || result.data, null, 2)}</pre>
        <div class="actions"><button type="button" class="button" onClick=${() => dialog.current?.close()}>Done</button></div>
      `}
    </dialog>
  `;
}

export function NewRunScreen() {
  const catalog = useResource("/api/newrun/catalog");
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState(null);
  const refresh = async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await api("/api/newrun/library/refresh", { method: "POST" });
      catalog.reload();
    } catch (error) {
      setRefreshError(error.message);
    } finally {
      setRefreshing(false);
    }
  };
  let body;
  if (catalog.status === "error") body = html`<${ErrorState} error=${catalog.error} onRetry=${catalog.reload} />`;
  else if (catalog.status !== "ready") body = html`<${Loading} label="Reading the instrument list…" />`;
  else body = html`
    ${refreshError && html`<p class="panel-status is-error" role="alert">${refreshError} Showing the last snapshot.</p>`}
    <${Form} catalog=${catalog.data} onRefresh=${refresh} refreshing=${refreshing} />`;
  return html`
    <div class="screen screen-newrun">
      <header class="review-top">
        <div>
          <p class="review-eyebrow">Control Room · New Run</p>
          <h1 tabindex="-1">Set up a run</h1>
        </div>
        <${ControlRoomTabs} current="newrun" />
      </header>
      ${body}
    </div>
  `;
}
