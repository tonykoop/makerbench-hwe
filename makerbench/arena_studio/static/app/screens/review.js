// Control Room Morning Review (#1105): one stacked card per design, ranked best first.
// Every design can be re-ranked (drag or ▲▼), annotated, and sent back for revision with
// its own note. "Save review" writes morning-review.json into the run folder.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { buildHash } from "../lib/route.js";
import { plural } from "../lib/format.js";
import { webgl2Available } from "../lib/webgl.js";
import {
  clearDraft,
  costText,
  dropOn,
  initialState,
  loadDraft,
  minutesText,
  move,
  nightLabel,
  payload,
  saveDraft,
  setNote,
  toggleRevise,
  trayCounts,
  turnKind,
} from "../lib/review.js";
import { useResource } from "../hooks/useResource.js";
import { Empty, ErrorState, Loading } from "../components/states.js";
import { ModelViewer } from "../components/modelViewer.js";

const EXTRA_TURNS = 3;

function base(runId) {
  return `/api/review/${encodeURIComponent(runId)}`;
}

function NightPicker({ nights, runId }) {
  return html`
    <nav class="review-nights" aria-label="Choose night">
      ${nights.map(
        (night) => html`
          <a
            key=${night.run_id}
            class="review-night"
            href=${buildHash("review", [night.run_id])}
            aria-current=${night.run_id === runId ? "page" : undefined}
            >${nightLabel(night)}</a
          >
        `,
      )}
    </nav>
  `;
}

function Summary({ view }) {
  const s = view.summary;
  const finished = s.finished_utc ? new Date(s.finished_utc) : null;
  return html`
    <p class="review-summary" data-testid="review-summary">
      <span><b>${s.designs}</b> designs</span>
      <span><b>${s.all_pass}</b> pass every check</span>
      <span><b>${typeof s.cost_usd === "number" ? `$${s.cost_usd.toFixed(2)}` : "not recorded"}</b> spend</span>
      <span><b>${s.turns ?? "not recorded"}</b> turns</span>
      <span>Finished <b>${finished && !Number.isNaN(finished.getTime()) ? finished.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC" : "not recorded"}</b></span>
    </p>
    ${s.safety_block && html`<p class="panel-status is-error" role="alert">This night stopped early: ${s.safety_block}</p>`}
  `;
}

function Brief({ view }) {
  const brief = view.brief || {};
  return html`
    <aside class="review-brief" aria-label="Brief">
      ${brief.has_reference
        ? html`<img src=${`${base(view.run_id)}/reference`} alt="Reference photo for this night's brief" />`
        : html`<p class="hint">No reference photo in this run folder.</p>`}
      <div class="review-brief-text">
        <h2>The brief</h2>
        <dl class="review-spec">
          ${brief.mode && html`<dt>Mode</dt><dd>${brief.mode}</dd>`}
          ${(brief.spec || []).map(([label, value]) => html`<dt key=${label}>${label}</dt><dd>${value}</dd>`)}
        </dl>
        ${brief.text && html`<p>${brief.text}</p>`}
        ${view.morning_vote?.pair_files?.length > 0 &&
        html`<p class="hint">Blind morning vote: <a href="#/morning">${plural(view.morning_vote.pair_files.length, "pair")}</a>.</p>`}
      </div>
    </aside>
  `;
}

function Viewer({ runId, design, useWebgl }) {
  const [failed, setFailed] = useState(false);
  const tid = encodeURIComponent(design.trial_id);
  let inner;
  if (design.has_mesh && useWebgl && !failed) {
    inner = html`<${ModelViewer}
      src=${`${base(runId)}/mesh/${tid}`}
      label=${`3D view of ${design.name}'s design`}
      onFailure=${() => setFailed(true)}
    />
    <span class="review-viewer-hint">drag to spin</span>`;
  } else if (design.has_render) {
    inner = html`<img class="review-render" src=${`${base(runId)}/render/${tid}`} alt=${`Render of ${design.name}'s design`} />`;
  } else {
    inner = html`<p class="review-viewer-empty">No shape to show.</p>`;
  }
  return html`<div class="review-viewer">${inner}${!design.all_pass && html`<span class="review-fail-flag">fails a check</span>`}</div>`;
}

function Assembly({ assembly }) {
  const rows = assembly.cut_list || [];
  return html`
    <details class="review-assembly">
      <summary>Parts (${assembly.parts.length}) and cut list (${rows.length} rows)</summary>
      <p class="hint">Parts: ${assembly.parts.join(", ") || "none listed"}</p>
      ${rows.length > 0
        ? html`<div class="table-scroll">
            <table class="data-table">
              <caption class="visually-hidden">Cut list</caption>
              <thead><tr><th scope="col">Part</th><th scope="col">Material</th><th scope="col" class="num">T × W × L mm</th><th scope="col">Grain</th><th scope="col" class="num">Qty</th></tr></thead>
              <tbody>
                ${rows.map((row) => html`<tr key=${row.part_id}>
                  <th scope="row">${row.part_id}</th><td>${row.material}</td>
                  <td class="num measure">${row.stock_thickness_mm} × ${row.stock_width_mm} × ${row.stock_length_mm}</td>
                  <td>${row.grain_direction}</td><td class="num">${row.quantity}</td></tr>`)}
              </tbody>
            </table>
          </div>`
        : html`<p class="hint">No cut list recorded for this design.</p>`}
    </details>
  `;
}

function Card({ runId, design, index, count, state, useWebgl, onMove, onNote, onRevise, onOpen, drag }) {
  const revise = Boolean(state.revise[design.trial_id]);
  const noteId = `note-${index}`;
  return html`
    <li
      class=${`review-card${drag.over === design.trial_id ? " is-over" : ""}${drag.id === design.trial_id ? " is-dragging" : ""}`}
      data-trial=${design.trial_id}
      draggable="true"
      onDragStart=${(event) => drag.start(event, design.trial_id)}
      onDragOver=${(event) => drag.over_(event, design.trial_id)}
      onDrop=${(event) => drag.drop(event, design.trial_id)}
      onDragEnd=${drag.end}
    >
      <div class="review-rank" title="Drag to re-rank">
        <span class="review-rank-n" aria-label=${`Rank ${index + 1}`}>${index + 1}</span>
        <button type="button" aria-label=${`Move ${design.name} up`} disabled=${index === 0} onClick=${() => onMove(design.trial_id, -1)}>▲</button>
        <button type="button" aria-label=${`Move ${design.name} down`} disabled=${index === count - 1} onClick=${() => onMove(design.trial_id, 1)}>▼</button>
      </div>
      <${Viewer} runId=${runId} design=${design} useWebgl=${useWebgl} />
      <div class="review-body">
        <div class="review-who"><h3>${design.name}</h3><span class="review-route">${design.route}</span></div>
        <ul class="review-chips" aria-label="Checks">
          ${design.checks.map(
            (check) => html`<li key=${check.name} class="check-chip" data-state=${check.state}>
              ${check.name}${check.state === "fail" ? " ✕" : check.state === "unknown" ? " ?" : ""}
            </li>`,
          )}
        </ul>
        ${design.failures.map((text) => html`<p class="review-fail-note">${text}</p>`)}
        <p class="review-stats">
          <span>Editable: <b>${design.editable_parameters == null ? "not recorded" : plural(design.editable_parameters, "parameter")}</b></span>
          <span>Time <b>${minutesText(design.time_s)}</b></span>
          <span>Cost <b>${costText(design.cost)}</b></span>
        </p>
        <div class="review-turns" aria-label="Checks passed per turn">
          <span>Turns</span>
          ${design.turns.length === 0
            ? html`<span class="hint">not recorded</span>`
            : design.turns.map(
                (turn) => html`<span key=${turn.turn} class="review-turn" data-kind=${turnKind(turn)} title=${`Turn ${turn.turn}: ${turn.passed}/${turn.total} checks`}>${turn.passed}</span>`,
              )}
        </div>
        ${design.assembly && html`<${Assembly} assembly=${design.assembly} />`}
        <label class="field-label" for=${noteId}>Your notes</label>
        <textarea
          id=${noteId}
          class="review-note"
          maxlength="4000"
          placeholder="What should change? For example: pegs too small, neck should lean back more."
          value=${state.notes[design.trial_id] || ""}
          onInput=${(event) => onNote(design.trial_id, event.currentTarget.value)}
        ></textarea>
        <div class="actions">
          ${design.native
            ? html`<button type="button" class="button button-quiet" data-open onClick=${() => onOpen(design)}>Open ${design.native.file} in ${design.native.app}</button>`
            : html`<span class="hint">No native file recorded.</span>`}
          <button type="button" class="button button-quiet review-revise" aria-pressed=${revise ? "true" : "false"} onClick=${() => onRevise(design.trial_id, noteId)}>
            ${revise ? "Will revise tonight" : "Revise with my notes"}
          </button>
        </div>
      </div>
    </li>
  `;
}

function useDrag(setOrder) {
  // The dragged id lives in a ref: drag events fire faster than a re-render.
  const dragged = useRef(null);
  const [drag, setDrag] = useState({ id: null, over: null });
  const reset = () => {
    dragged.current = null;
    setDrag({ id: null, over: null });
  };
  return {
    ...drag,
    start(event, id) {
      if (event.target.closest?.("textarea, model-viewer, button, a, summary")) {
        event.preventDefault();
        return;
      }
      event.dataTransfer?.setData("text/plain", id);
      dragged.current = id;
      setDrag({ id, over: null });
    },
    over_(event, id) {
      if (!dragged.current) return;
      event.preventDefault();
      setDrag((prev) => (prev.over === id ? prev : { ...prev, over: id }));
    },
    drop(event, id) {
      const from = dragged.current;
      if (!from) return;
      event.preventDefault();
      setOrder((order) => dropOn(order, from, id));
      reset();
    },
    end: reset,
  };
}

function Night({ view, voter }) {
  const runId = view.run_id;
  const [state, setState] = useState(() => initialState(view, loadDraft(runId)));
  const [toast, setToast] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);
  const dialog = useRef(null);
  const useWebgl = useMemo(() => webgl2Available(), []);
  const byId = useMemo(() => Object.fromEntries(view.designs.map((d) => [d.trial_id, d])), [view]);

  // Layout effect: the draft is written with the DOM change, so an immediate reload keeps it.
  useLayoutEffect(() => saveDraft(runId, state), [runId, state]);
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (saved) dialog.current?.showModal?.();
  }, [saved]);

  const setOrder = (fn) => setState((prev) => ({ ...prev, order: fn(prev.order) }));
  const drag = useDrag(setOrder);
  const counts = trayCounts(state);

  const onRevise = (id, noteId) => {
    const { state: next, error } = toggleRevise(state, id);
    if (error) {
      setToast(error);
      document.getElementById(noteId)?.focus();
      return;
    }
    setState(next);
  };
  const onOpen = async (design) => {
    try {
      const result = await api(`${base(runId)}/open/${encodeURIComponent(design.trial_id)}`, { method: "POST" });
      setToast(`Opening ${result.opened} in ${result.app}.`);
    } catch (error) {
      setToast(error.message);
    }
  };
  const onSave = async () => {
    setSaving(true);
    try {
      const result = await api(base(runId), { method: "POST", body: payload(state, { reviewer: voter, extraTurns: EXTRA_TURNS }) });
      clearDraft(runId);
      setSaved(result.review);
    } catch (error) {
      setToast(error.message);
    } finally {
      setSaving(false);
    }
  };

  const requests = saved?.revision_requests || [];
  return html`
    <${Summary} view=${view} />
    <div class="review-layout">
      <${Brief} view=${view} />
      <ol class="review-stack" aria-label="Designs, best first">
        ${state.order.map(
          (id, index) => html`<${Card}
            key=${id}
            runId=${runId}
            design=${byId[id]}
            index=${index}
            count=${state.order.length}
            state=${state}
            useWebgl=${useWebgl}
            drag=${drag}
            onMove=${(trialId, delta) => setOrder((order) => move(order, trialId, delta))}
            onNote=${(trialId, text) => setState((prev) => setNote(prev, trialId, text))}
            onRevise=${onRevise}
            onOpen=${onOpen}
          />`,
        )}
      </ol>
    </div>
    <div class="review-tray" role="region" aria-label="Review tray">
      <div class="review-tray-in">
        <p class="review-tray-msg" data-testid="tray">
          Your ranking: <b>${state.order.map((id) => byId[id].name).join(" › ")}</b>${" · "}
          <b>${plural(counts.notes, "note")}</b>${" · "}<b>${counts.revise}</b> sent back for tonight
        </p>
        <div class="actions">
          <button type="button" class="button button-quiet" onClick=${() => {
            setOrder(() => [...view.grader_order]);
            setToast("Ranking reset to the grader's order.");
          }}>Reset ranking</button>
          <button type="button" class="button" disabled=${saving} onClick=${onSave}>${saving ? "Saving…" : "Save review"}</button>
        </div>
      </div>
    </div>
    <p class="review-toast" role="status" aria-live="polite" hidden=${!toast}>${toast || ""}</p>
    <dialog class="review-dialog" ref=${dialog} onClose=${() => setSaved(null)}>
      ${saved && html`
        <h2>Review saved</h2>
        <p>
          ${requests.length
            ? `${plural(requests.length, "design")} will be revised with your notes, ${EXTRA_TURNS} more turns each from its best turn. `
            : "Ranking and notes recorded. Nothing is sent back for revision. "}
          Written to <code>morning-review.json</code> in the run folder.
          ${requests.length > 0 && (saved.queued ? " Added to the Control Room revision queue." : " Queue writes are off, so no queue was changed.")}
        </p>
        <pre class="review-json">${JSON.stringify({ ranking: saved.ranking, notes: saved.notes, revision_requests: requests }, null, 2)}</pre>
        <div class="actions"><button type="button" class="button" onClick=${() => dialog.current?.close()}>Done</button></div>
      `}
    </dialog>
  `;
}

export function ReviewScreen({ route, voter }) {
  const nights = useResource("/api/review/nights");
  const list = nights.status === "ready" ? nights.data.nights : [];
  const runId = route.args[0] || list[0]?.run_id || null;
  const view = useResource(runId ? `${base(runId)}` : null);
  const current = list.find((night) => night.run_id === runId);

  let body;
  if (nights.status === "loading" || nights.status === "idle") body = html`<${Loading} label="Looking for finished nights…" />`;
  else if (nights.status === "error") body = html`<${ErrorState} error=${nights.error} onRetry=${nights.reload} />`;
  else if (list.length === 0) {
    body = html`<${Empty} title="No nights to review yet">
      <p>A night appears here once the nightly runner writes <code>morning-summary.json</code> into its run folder under <code>runs/</code>.</p>
    <//>`;
  } else if (view.status === "error") body = html`<${ErrorState} error=${view.error} onRetry=${view.reload} />`;
  else if (view.status !== "ready") body = html`<${Loading} label="Reading the night's designs…" />`;
  else body = html`<${Night} key=${runId} view=${view.data} voter=${voter} />`;

  const when = current?.night ? new Date(current.night) : null;
  return html`
    <div class="screen screen-review">
      <header class="review-top">
        <div>
          <p class="review-eyebrow">
            Morning Review${when && !Number.isNaN(when.getTime()) ? ` · Night of ${when.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })}` : ""}
          </p>
          <h1 tabindex="-1">${view.status === "ready" ? view.data.title : current?.title || "Morning Review"}</h1>
        </div>
        ${list.length > 0 && html`<${NightPicker} nights=${list} runId=${runId} />`}
      </header>
      ${body}
    </div>
  `;
}
