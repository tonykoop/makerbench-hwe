import { useCallback, useEffect, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { formatWhen } from "../lib/format.js";
import { buildHash } from "../lib/route.js";
import { webgl2Available } from "../lib/webgl.js";
import {
  appendLines,
  checkRows,
  describeDiffRow,
  diffSummary,
  editorKindText,
  findLineReferences,
  isFinished,
  jobStatusText,
  latestRevision,
  originText,
  parseLogEvent,
  revisionTree,
  saveTargetText,
} from "../lib/workbench.js";
import { useResource } from "../hooks/useResource.js";
import { CodeEditor } from "../components/codeEditor.js";
import { ModelViewer } from "../components/modelViewer.js";
import { Empty, ErrorState, Loading } from "../components/states.js";

const enc = encodeURIComponent;
const POLL_MS = 1000;
const base = (designId) => `/api/workbench/designs/${enc(designId)}`;

// --- list ---------------------------------------------------------------------

function DesignList({ designs }) {
  if (designs.status === "loading" || designs.status === "idle") {
    return html`<${Loading} label="Loading designs…" />`;
  }
  if (designs.status === "error") {
    return html`<${ErrorState} error=${designs.error} onRetry=${designs.reload} />`;
  }
  const list = designs.data.designs || [];
  if (list.length === 0) {
    return html`
      <${Empty} title="No designs yet">
        <p>
          Open one from a run's trial on the <a href=${buildHash("runs")}>Runs</a> screen, or from an
          instrument master on the <a href=${buildHash("launch")}>Launch</a> screen.
        </p>
        <p>Designs live under <code>runs/workbench/</code>, never inside an instrument repo.</p>
      <//>
    `;
  }
  return html`
    <div class="table-scroll">
      <table class="data-table workbench-list">
        <caption class="visually-hidden">Designs in this checkout</caption>
        <thead>
          <tr>
            <th scope="col">Design</th>
            <th scope="col">Instrument</th>
            <th scope="col">Origin</th>
            <th scope="col" class="num">Revisions</th>
            <th scope="col">Pick</th>
            <th scope="col">Created</th>
          </tr>
        </thead>
        <tbody>
          ${list.map(
            (design) => html`
              <tr key=${design.design_id}>
                <th scope="row">
                  <a href=${buildHash("workbench", [design.design_id])}>${design.title || design.design_id}</a>
                  <code class="task-id">${design.design_id}</code>
                </th>
                <td>${design.instrument_id}</td>
                <td>${design.origin?.kind || "unknown"}</td>
                <td class="num">${design.revision_count}</td>
                <td>${design.pick ? html`<span class="gate" data-kind="approved">Picked</span>` : "None"}</td>
                <td>${formatWhen(design.created_at)}</td>
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
  `;
}

// --- job log ------------------------------------------------------------------

const CONNECTION_TEXT = {
  idle: "",
  live: "Live",
  reconnecting: "Reconnecting…",
  ended: "Finished",
};

function JobLog({ designId, draftId, finished }) {
  const [lines, setLines] = useState([]);
  const [connection, setConnection] = useState("idle");
  const logRef = useRef(null);
  const sourceRef = useRef(null);

  useEffect(() => {
    if (!draftId) return undefined;
    setLines([]);
    let buffer = [];
    let timer = null;
    const source = new EventSource(`${base(designId)}/drafts/${enc(draftId)}/log/stream?tail=200`);
    sourceRef.current = source;
    source.onopen = () => {
      buffer = [];
      clearTimeout(timer);
      timer = null;
      setLines([]);
      setConnection("live");
    };
    source.onmessage = (event) => {
      buffer.push(parseLogEvent(event.data));
      if (timer) return;
      timer = setTimeout(() => {
        const batch = buffer;
        buffer = [];
        timer = null;
        setLines((prev) => appendLines(prev, batch));
      }, 100);
    };
    // The server sends `event: end` when the job finishes; close so EventSource
    // never reconnects and replays the tail forever.
    source.addEventListener("end", () => {
      source.close();
      setConnection("ended");
    });
    source.onerror = () => {
      if (source.readyState !== EventSource.CLOSED) setConnection("reconnecting");
    };
    return () => {
      clearTimeout(timer);
      source.close();
    };
  }, [designId, draftId]);

  // A fast compile can finish before the stream's first batch lands. Once the
  // job is finished, close the stream and show the final tail from a one-shot
  // read (the same route with follow=false), so the log is never stale.
  useEffect(() => {
    if (!finished || !draftId) return undefined;
    let cancelled = false;
    sourceRef.current?.close();
    api(`${base(designId)}/drafts/${enc(draftId)}/log/stream?tail=500&follow=false`)
      .then((text) => {
        if (cancelled) return;
        const final = String(text)
          .split("\n")
          .filter((line) => line.startsWith("data: "))
          .map((line) => parseLogEvent(line.slice("data: ".length)));
        // the end event's data is the status word; drop it from the log body
        if (String(text).includes("event: end")) final.pop();
        setLines(final);
      })
      .catch(() => {})
      .finally(() => !cancelled && setConnection("ended"));
    return () => {
      cancelled = true;
    };
  }, [designId, draftId, finished]);

  useEffect(() => {
    const log = logRef.current;
    if (!log) return;
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    if (nearBottom) log.scrollTop = log.scrollHeight;
  }, [lines]);

  return html`
    <div class="log-panel workbench-log">
      <div class="log-head">
        <h3>Compile log</h3>
        <span class="connection" data-state=${connection} role="status">${CONNECTION_TEXT[connection]}</span>
      </div>
      <pre class="log" role="log" ref=${logRef} tabindex="0" aria-label="Compile log">${lines.length
        ? lines.join("\n")
        : "No log lines yet."}</pre>
    </div>
  `;
}

// --- preview and checks ---------------------------------------------------------

function Preview({ designId, kind, itemId, artifacts, label }) {
  const [view, setView] = useState("image");
  const [viewerFailed, setViewerFailed] = useState(false);
  const names = artifacts || [];
  const png = names.includes("preview.png") ? `${base(designId)}/${kind}/${enc(itemId)}/artifacts/preview.png` : null;
  const glb = names.includes("model.glb") ? `${base(designId)}/${kind}/${enc(itemId)}/artifacts/model.glb` : null;
  const canWebgl = glb && !viewerFailed && webgl2Available();
  if (!png && !glb) {
    return html`<p class="viewer-note" role="note">Render unavailable: this compile produced no preview.</p>`;
  }
  return html`
    <div class="workbench-preview">
      ${glb &&
      html`<div class="view-toggle" role="group" aria-label="Preview mode">
        <button type="button" class="button button-quiet" aria-pressed=${view === "image"} onClick=${() => setView("image")}>Image</button>
        <button
          type="button"
          class="button button-quiet"
          aria-pressed=${view === "3d"}
          aria-disabled=${canWebgl ? "false" : "true"}
          onClick=${() => canWebgl && setView("3d")}
        >
          3D
        </button>
        ${!canWebgl && html`<span class="hint">3D needs WebGL; the image is shown instead.</span>`}
      </div>`}
      ${view === "3d" && canWebgl
        ? html`<${ModelViewer} src=${glb} label=${label} onFailure=${() => setViewerFailed(true)} />`
        : png
          ? html`<img class="workbench-image" src=${png} alt=${label} />`
          : html`<p class="viewer-note" role="note">Render unavailable without WebGL.</p>`}
    </div>
  `;
}

function Checks({ objective }) {
  const checks = checkRows(objective);
  if (checks.state === "none") return html`<p class="hint">${checks.note}</p>`;
  if (checks.state !== "scored") {
    return html`<p class=${`check-note check-${checks.state}`} role="note">${checks.note}</p>`;
  }
  return html`
    <div>
      <p class="hint" role="status">${checks.note}</p>
      <div class="table-scroll"><table class="data-table checks">
        <caption class="visually-hidden">Objective checks</caption>
        <thead>
          <tr><th scope="col">Check</th><th scope="col">Result</th><th scope="col">Detail</th></tr>
        </thead>
        <tbody>
          ${checks.rows.map(
            (row) => html`
              <tr key=${row.name}>
                <th scope="row">${row.name}</th>
                <td><span class="gate" data-kind=${row.result === "pass" ? "approved" : row.result === "fail" ? "error" : "review"}>${row.result}</span></td>
                <td>${row.detail}</td>
              </tr>
            `,
          )}
        </tbody>
      </table></div>
    </div>
  `;
}

// --- revisions and compare ---------------------------------------------------------

function RevisionTree({ designId, revisions, current, compareWith, onCompare }) {
  const tree = revisionTree(revisions);
  if (tree.length === 0) return html`<p class="hint">No revisions yet. Compile the draft, then save it.</p>`;
  return html`
    <div class="table-scroll">
      <table class="data-table revisions">
        <caption class="visually-hidden">Revisions of this design</caption>
        <thead>
          <tr>
            <th scope="col" class="num">Rev</th>
            <th scope="col">Lineage</th>
            <th scope="col">Editor</th>
            <th scope="col">Compile</th>
            <th scope="col">Saved</th>
            <th scope="col"><span class="visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          ${tree.map(
            (rev) => html`
              <tr key=${rev.rev_id} aria-current=${rev.rev_id === current ? "true" : undefined}>
                <td class="num">${rev.seq}</td>
                <td>
                  <span class="lineage" style=${`--depth:${rev.depth}`}>
                    ${rev.parent_rev_id == null ? "origin" : `from ${rev.parent_rev_id.slice(0, 8)}`}
                    ${rev.branch === "detached" ? " (parent not listed)" : ""}
                  </span>
                </td>
                <td>${editorKindText(rev.editor_kind)}</td>
                <td>${rev.compile_status || "unknown"}</td>
                <td>${formatWhen(rev.created_at)}</td>
                <td class="actions-cell">
                  <a href=${buildHash("workbench", [designId, rev.rev_id])}>Open</a>
                  ${current && rev.rev_id !== current &&
                  html`<button
                    type="button"
                    class="button button-quiet"
                    data-compare=${rev.rev_id}
                    aria-pressed=${compareWith === rev.rev_id}
                    onClick=${() => onCompare(rev.rev_id)}
                  >
                    Compare
                  </button>`}
                </td>
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
  `;
}

function ComparePanel({ designId, a, b, onClose }) {
  const compare = useResource(`${base(designId)}/compare?a=${enc(a)}&b=${enc(b)}`);
  const titleRef = useRef(null);
  useEffect(() => titleRef.current?.focus(), []);
  let body;
  if (compare.status === "loading" || compare.status === "idle") body = html`<${Loading} label="Comparing…" />`;
  else if (compare.status === "error") body = html`<${ErrorState} error=${compare.error} onRetry=${compare.reload} />`;
  else {
    const data = compare.data;
    const params = Object.entries(data.parameter_delta || {});
    const flipped = Object.entries(data.objective_delta?.flipped || {});
    const rate = data.objective_delta?.pass_rate || [null, null];
    body = html`
      <div class="compare-grid workbench-compare-grid">
        <section class="panel" aria-label=${`Revision ${data.a.seq}`}>
          <h3>Revision ${data.a.seq}</h3>
          <${Preview} designId=${designId} kind="revisions" itemId=${a} artifacts=${data.a.artifacts} label=${`Preview of revision ${data.a.seq}`} />
          <p class="hint">${editorKindText(data.a.editor?.kind)}, ${formatWhen(data.a.created_at)}</p>
        </section>
        <section class="panel" aria-label=${`Revision ${data.b.seq}`}>
          <h3>Revision ${data.b.seq}</h3>
          <${Preview} designId=${designId} kind="revisions" itemId=${b} artifacts=${data.b.artifacts} label=${`Preview of revision ${data.b.seq}`} />
          <p class="hint">${editorKindText(data.b.editor?.kind)}, ${formatWhen(data.b.created_at)}</p>
        </section>
      </div>
      <h3>Source</h3>
      <p class="hint" role="status">${diffSummary(data.diff)}</p>
      <div class="table-scroll">
        <pre class="diff" aria-label="Unified source diff">${(data.diff || []).map((row) => {
          const d = describeDiffRow(row);
          return html`<span class=${`diff-row diff-${d.kind}`}><span class="visually-hidden">${d.label}: </span><span class="diff-marker" aria-hidden="true">${d.marker}</span> ${d.text}\n</span>`;
        })}</pre>
      </div>
      <h3>Parameters</h3>
      ${params.length
        ? html`<table class="data-table">
            <caption class="visually-hidden">Parameter changes</caption>
            <thead><tr><th scope="col">Parameter</th><th scope="col">Revision ${data.a.seq}</th><th scope="col">Revision ${data.b.seq}</th></tr></thead>
            <tbody>
              ${params.map(([name, [before, after]]) => html`<tr key=${name}><th scope="row">${name}</th><td>${String(before)}</td><td>${String(after)}</td></tr>`)}
            </tbody>
          </table>`
        : html`<p class="hint">No declared parameter changed.</p>`}
      <h3>Checks</h3>
      <p class="hint">
        Pass rate ${rate[0] == null ? "unknown" : `${Math.round(rate[0] * 100)}%`} →
        ${" "}${rate[1] == null ? "unknown" : `${Math.round(rate[1] * 100)}%`}.
        ${flipped.length ? ` Flipped: ${flipped.map(([name, [x, y]]) => `${name} ${String(x)}→${String(y)}`).join(", ")}.` : " No check flipped."}
      </p>
    `;
  }
  return html`
    <section class="panel compare-panel" aria-labelledby="compare-title">
      <div class="panel-head">
        <h2 id="compare-title" tabindex="-1" ref=${titleRef}>Compare</h2>
        <button type="button" class="button button-quiet" onClick=${onClose}>Close</button>
      </div>
      ${body}
    </section>
  `;
}

// --- the design view ------------------------------------------------------------------

function useDraftStatus(designId, draftId) {
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!draftId) {
      setDraft(null);
      return undefined;
    }
    let cancelled = false;
    let timer = null;
    const poll = () =>
      api(`${base(designId)}/drafts/${enc(draftId)}`)
        .then((data) => {
          if (cancelled) return;
          setDraft(data);
          setError(null);
          if (!isFinished(data.job?.status)) timer = setTimeout(poll, POLL_MS);
        })
        .catch((err) => {
          if (cancelled) return;
          setError(err);
        });
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [designId, draftId]);
  return { draft, error };
}

function DesignView({ designId, revId }) {
  const design = useResource(base(designId));
  const [source, setSource] = useState(null);
  const [sourceError, setSourceError] = useState(null);
  const [draftId, setDraftId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");
  const [savePanel, setSavePanel] = useState(false);
  const [status, setStatus] = useState({ text: "", error: false });
  const [compareWith, setCompareWith] = useState(null);
  const [busy, setBusy] = useState(false);
  const editorRef = useRef(null);
  const statusRef = useRef(null);
  const resultRef = useRef(null);
  const saveTitleRef = useRef(null);
  const focusAfterSave = useRef(false);
  const { draft, error: draftError } = useDraftStatus(designId, draftId);

  const revisions = design.status === "ready" ? design.data.revisions || [] : [];
  const latest = latestRevision(revisions);
  const currentRev = revId ? revisions.find((r) => r.rev_id === revId) || null : latest;
  const currentRevId = currentRev?.rev_id || null;
  const currentRevision = useResource(currentRevId ? `${base(designId)}/revisions/${enc(currentRevId)}` : null);

  // Load the source of the current revision, or of the origin draft when the
  // design has no revision yet.
  useEffect(() => {
    let cancelled = false;
    setSource(null);
    setSourceError(null);
    if (design.status !== "ready") return undefined;
    let path;
    if (currentRevId) path = `${base(designId)}/revisions/${enc(currentRevId)}/source`;
    else {
      const origin = (design.data.drafts || []).find((d) => d.parent_rev_id == null);
      if (!origin) return undefined;
      path = `${base(designId)}/drafts/${enc(origin.draft_id)}/source`;
      if (!draftId) setDraftId(origin.draft_id);
    }
    api(path)
      .then((text) => !cancelled && setSource(String(text)))
      .catch((err) => !cancelled && setSourceError(err));
    return () => {
      cancelled = true;
    };
  }, [design.status, designId, currentRevId]);

  const finished = isFinished(draft?.job?.status);
  const running = Boolean(draft && !finished);
  useEffect(() => {
    // A finished compile moves focus to the result heading (plan §6).
    if (finished && draft && resultRef.current && document.activeElement?.closest(".code-editor") == null) {
      resultRef.current.focus();
    } else if (finished && draft && resultRef.current) {
      resultRef.current.focus();
    }
  }, [finished, draft?.draft_id]);

  const announce = (text, error = false) => {
    setStatus({ text, error });
    statusRef.current?.focus();
  };

  const compile = async () => {
    // One job at a time: Ctrl+Enter while a compile runs (the editor keeps
    // focus, read-only) must not start a second draft and orphan the first.
    if (busy || running || source == null) return;
    if (!currentRevId) {
      announce("Save the origin revision before editing.", true);
      return;
    }
    setBusy(true);
    try {
      const result = await api(`${base(designId)}/drafts`, { method: "POST", body: { parent_rev_id: currentRevId, source } });
      setDraftId(result.draft_id);
      setStatus({ text: "", error: false });
      editorRef.current?.focus();
    } catch (err) {
      announce(err.message, true);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!draftId) return;
    try {
      await api(`${base(designId)}/drafts/${enc(draftId)}/cancel`, { method: "POST", body: {} });
      announce("Compile cancelled.");
    } catch (err) {
      announce(err.message, true);
    }
  };

  const openSave = () => {
    setSavePanel(true);
    setTimeout(() => saveTitleRef.current?.focus(), 0);
  };

  const save = async () => {
    if (saving || !draftId) return;
    setSaving(true);
    try {
      const rev = await api(`${base(designId)}/revisions`, { method: "POST", body: { draft_id: draftId, note } });
      setSavePanel(false);
      setNote("");
      focusAfterSave.current = true;
      const parent = revisions.find((r) => r.rev_id === rev.parent_rev_id);
      const isBranch = latest && parent && latest.rev_id !== parent.rev_id;
      setStatus({
        text: isBranch ? `Saved revision ${rev.seq} as a branch of ${parent.seq}.` : `Saved revision ${rev.seq} from ${parent ? parent.seq : "the origin"}.`,
        error: false,
      });
      design.reload();
      window.location.hash = buildHash("workbench", [designId, rev.rev_id]);
    } catch (err) {
      announce(err.message, true);
    } finally {
      setSaving(false);
    }
  };
  useEffect(() => {
    if (focusAfterSave.current && design.status === "ready") {
      focusAfterSave.current = false;
      statusRef.current?.focus();
    }
  }, [design.status, revisions.length]);

  if (design.status === "loading" || design.status === "idle") return html`<${Loading} label="Loading the design…" />`;
  if (design.status === "error") return html`<${ErrorState} error=${design.error} onRetry=${design.reload} />`;
  const d = design.data;
  const title = d.curation?.title || d.title || d.design_id;
  const canSave = draft?.job?.status === "succeeded" && draft.parent_rev_id === currentRevId;
  const draftFailed = draft?.job?.status === "failed";
  const lineRefs = draftFailed ? findLineReferences(draft.job.error) : [];
  const shownObjective = draft && finished ? draft.objective : currentRevision.status === "ready" ? currentRevision.data.objective : null;
  const shownArtifacts = draft && finished ? { kind: "drafts", id: draft.draft_id, names: draft.artifacts } : currentRevision.status === "ready" ? { kind: "revisions", id: currentRevId, names: currentRevision.data.artifacts } : null;

  return html`
    <article class="workbench-design">
      <header class="workbench-head">
        <h2 tabindex="-1">${title}</h2>
        <dl class="facts">
          <div><dt>Instrument</dt><dd>${d.instrument_id}</dd></div>
          <div><dt>Backend</dt><dd>${d.backend}</dd></div>
          <div><dt>Origin</dt><dd>${originText(d.origin)}</dd></div>
          <div><dt>Revision</dt><dd class="measure">${currentRev ? `${currentRev.seq} of ${revisions.length}` : "none yet"}</dd></div>
        </dl>
        <p class="hint">${saveTargetText(currentRev, latest)}</p>
        <p class=${`panel-status${status.error ? " is-error" : ""}`} role="status" tabindex="-1" ref=${statusRef}>${status.text}</p>
      </header>
      <div class="workbench-layout">
        <section class="panel workbench-code" aria-label="Code">
          <div class="tabs" role="tablist" aria-label="Design tabs">
            <button type="button" role="tab" aria-selected="true" class="tab" id="tab-code">Code</button>
            <span class="tab tab-later" role="tab" aria-selected="false" aria-disabled="true" title="Arrives in a later slice">Parameters</span>
            <span class="tab tab-later" role="tab" aria-selected="false" aria-disabled="true" title="Arrives in a later slice">Revise</span>
            <span class="tab tab-later" role="tab" aria-selected="false" aria-disabled="true" title="Arrives in a later slice">Curate</span>
          </div>
          <div role="tabpanel" aria-labelledby="tab-code">
            ${sourceError && html`<${ErrorState} error=${sourceError} />`}
            ${source == null && !sourceError && html`<${Loading} label="Loading the source…" />`}
            ${source != null &&
            html`<${CodeEditor}
              editorRef=${editorRef}
              value=${source}
              onChange=${setSource}
              onSubmit=${compile}
              language=${d.backend}
              label=${`Source for ${title}, revision ${currentRev ? currentRev.seq : "draft"}`}
              readOnly=${Boolean(running)}
            />`}
            <div class="actions">
              <button type="button" class="button" data-action="compile" aria-disabled=${busy || running || source == null ? "true" : "false"} onClick=${compile}>
                Compile
              </button>
              ${running && html`<button type="button" class="button button-quiet" data-action="cancel" onClick=${cancel}>Cancel</button>`}
              ${canSave && html`<button type="button" class="button" data-action="save" onClick=${openSave}>Save as revision</button>`}
            </div>
            ${savePanel &&
            html`<section class="panel confirm save-panel" aria-labelledby="save-title">
              <h3 id="save-title" tabindex="-1" ref=${saveTitleRef}>Save as revision</h3>
              <p class="hint">${saveTargetText(currentRev, latest)} Parent: <code>${currentRevId}</code>.</p>
              <label class="field">
                <span class="field-label">Note (optional)</span>
                <input type="text" name="note" maxlength="1000" value=${note} onInput=${(event) => setNote(event.currentTarget.value)} />
              </label>
              <div class="actions">
                <button type="button" class="button" data-action="confirm-save" aria-disabled=${saving ? "true" : "false"} onClick=${save}>Save</button>
                <button type="button" class="button button-quiet" onClick=${() => { setSavePanel(false); editorRef.current?.focus(); }}>Keep editing</button>
              </div>
            </section>`}
            ${draft &&
            html`<div class="job-status" data-status=${draft.job?.status}>
              <p role="status">
                <strong>${jobStatusText(draft.job?.status)}</strong>
                ${draft.queue_position ? ` (position ${draft.queue_position} in the queue)` : ""}
                ${draft.job?.status === "succeeded" && draft.parent_rev_id === currentRevId ? " — unsaved draft" : ""}
              </p>
              ${draftFailed &&
              html`<div class="state state-error" role="alert">
                <p class="state-title">The compile failed.</p>
                <pre class="command"><code>${draft.job.error}</code></pre>
                ${lineRefs.length > 0 &&
                html`<p class="hint">
                  ${lineRefs.map(
                    (line) => html`<button type="button" class="button button-quiet" data-line=${line} onClick=${() => editorRef.current?.goToLine(line)}>Go to line ${line}</button> `,
                  )}
                </p>`}
                <button type="button" class="button button-quiet" onClick=${compile}>Try again</button>
              </div>`}
              ${draftError && html`<${ErrorState} error=${draftError} />`}
            </div>`}
            ${draftId && html`<${JobLog} key=${draftId} designId=${designId} draftId=${draftId} finished=${finished} />`}
          </div>
        </section>
        <section class="panel workbench-result" aria-label="Preview and checks">
          <h3 tabindex="-1" ref=${resultRef}>
            ${draft && finished ? `Result of the ${draft.job?.status} compile` : currentRev ? `Revision ${currentRev.seq}` : "Preview"}
          </h3>
          ${shownArtifacts
            ? html`<${Preview} designId=${designId} kind=${shownArtifacts.kind} itemId=${shownArtifacts.id} artifacts=${shownArtifacts.names} label=${`Preview of ${title}`} />`
            : html`<p class="hint">No preview yet.</p>`}
          <h4>Checks</h4>
          <${Checks} objective=${shownObjective} />
        </section>
      </div>
      <section class="panel workbench-revisions" aria-label="Revisions">
        <h3>Revisions</h3>
        <${RevisionTree} designId=${designId} revisions=${revisions} current=${currentRevId} compareWith=${compareWith} onCompare=${setCompareWith} />
      </section>
      ${compareWith && currentRevId &&
      html`<${ComparePanel} key=${`${currentRevId}:${compareWith}`} designId=${designId} a=${compareWith} b=${currentRevId} onClose=${() => { setCompareWith(null); document.querySelector(`[data-compare="${CSS.escape(compareWith)}"]`)?.focus(); }} />`}
    </article>
  `;
}

export function WorkbenchScreen({ route }) {
  const designId = route.args[0] || null;
  const revId = route.args[1] || null;
  const designs = useResource(designId ? null : "/api/workbench/designs");
  return html`
    <div class="screen screen-workbench">
      <h1 tabindex="-1">Workbench</h1>
      <p class="lede">
        Edit a design's code, compile it in the sandbox, and save each step as a revision. Revisions are never
        overwritten, and nothing here changes a blind vote.
      </p>
      ${designId
        ? html`<p><a href=${buildHash("workbench")}>All designs</a></p>
            <${DesignView} key=${designId} designId=${designId} revId=${revId} />`
        : html`<${DesignList} designs=${designs} />`}
    </div>
  `;
}
