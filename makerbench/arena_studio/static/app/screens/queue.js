// Tonight's Queue (#1160): draft or explicitly enabled locked edits; read-only GO and readiness.
import { useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { useResource } from "../hooks/useResource.js";
import { ControlRoomTabs } from "../components/controlRoomTabs.js";
import { ErrorState, Loading } from "../components/states.js";

const editable = (job) => ["queued", "draft"].includes(job.status);
const money = (value) => Number.isFinite(Number(value)) ? `$${Number(value).toFixed(2)}` : "unknown";

function Check({ ok, label }) {
  return html`<li><span class=${`queue-verdict ${ok ? "go" : "no"}`}>${ok ? "GO" : "NO-GO"}</span><span>${label}</span></li>`;
}

export function QueueScreen() {
  const queue = useResource("/api/queue");
  const status = useResource("/api/queue/status");
  const [updated, setUpdated] = useState(null);
  const [selected, setSelected] = useState(null);
  const [preflight, setPreflight] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState("");
  const [over, setOver] = useState(null);
  const data = updated || queue.data;
  const jobs = data?.jobs || [];
  const pending = jobs.filter(editable);
  const removed = jobs.filter((j) => j.status === "removed");
  const others = jobs.filter((j) => !editable(j) && !["running", "blocked", "removed"].includes(j.status));
  const running = jobs.filter((j) => j.status === "running");
  const blocked = jobs.filter((j) => j.status === "blocked");
  const ordered = [...running, ...pending, ...blocked, ...others];
  const chosen = jobs.find((j) => j.job_id === selected && editable(j));

  async function edit(action, jobId = "", order = []) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const next = await api("/api/queue/edit", { method: "POST", body: { action, job_id: jobId, order, revision: data.revision } });
      setUpdated(next);
      if (action === "remove" && jobId === selected) { setSelected(null); setPreflight(null); }
      const what = action === "remove" ? "Night removed" : action === "undo" ? "Night restored" : action === "tomorrow" ? "Night moved to the end of the queue" : "Order changed";
      setNotice(`${what} · ${next.queue_writes ? "queue written" : "draft only"}`);
    } catch (problem) { setError(problem); }
    finally { setBusy(false); }
  }

  function move(job, delta) {
    const order = pending.map((j) => j.job_id), at = order.indexOf(job.job_id), to = at + delta;
    if (to < 0 || to >= order.length) return;
    [order[at], order[to]] = [order[to], order[at]];
    edit("reorder", "", order);
  }

  function drop(event, target) {
    event.preventDefault(); setOver(null);
    const source = event.dataTransfer.getData("text/plain");
    if (!editable(target) || source === target.job_id || !pending.some((j) => j.job_id === source)) return;
    const order = pending.filter((j) => j.job_id !== source).map((j) => j.job_id);
    order.splice(order.indexOf(target.job_id), 0, source);
    edit("reorder", "", order);
  }

  async function runPreflight() {
    if (!chosen || busy) return;
    setBusy(true); setError(null); setPreflight(null);
    try { setPreflight(await api("/api/queue/preflight", { method: "POST", body: { job_id: selected } })); }
    catch (problem) { setError(problem); }
    finally { setBusy(false); }
  }

  function refresh() {
    setUpdated(null); setPreflight(null); setNotice(""); queue.reload(); status.reload();
  }

  return html`<div class="screen screen-queue">
    <header class="review-top">
      <div><p class="review-eyebrow">Control Room · Tonight's Queue</p><h1 tabindex="-1">What runs tonight, and in what order</h1></div>
      <${ControlRoomTabs} current="queue" />
    </header>
    ${data && !data.queue_writes && html`<p class="queue-banner" data-testid="draft-banner"><b>Draft only.</b> Queue writes are off. Reordering, moving and removing change an in-memory draft. The live queue is untouched.</p>`}
    ${data?.queue_writes && html`<p class="queue-banner">Queue writes are on. Changes are saved to ${data.queue_name}.</p>`}
    ${error && html`<${ErrorState} error=${error} onRetry=${refresh} />`}
    ${notice && html`<p class="queue-notice" role="status">${notice}</p>`}
    ${queue.status === "loading" && !data && html`<${Loading} label="Reading tonight's queue…" />`}
    ${queue.error && html`<${ErrorState} error=${queue.error} onRetry=${refresh} />`}
    <div class="queue-layout">
      <section class="queue-upcoming" aria-labelledby="queue-heading">
        <div class="queue-list-heading"><h2 id="queue-heading">Upcoming nights <span>${pending.length} queued · ${running.length} running · ${blocked.length} blocked</span></h2>
          <button class="button button-quiet" disabled=${busy} onClick=${refresh}>Refresh</button></div>
        ${data && !ordered.length && html`<p class="state">${data.exists ? "No upcoming nights." : "No nightly queue file yet."} <a href="#/newrun">Draft a new run</a>.</p>`}
        <ol class="queue-list" aria-label="Nights">
          ${ordered.map((job) => {
            const index = pending.indexOf(job), canEdit = editable(job);
            return html`<li key=${job.job_id} class=${`queue-night ${over === job.job_id ? "queue-over" : ""}`} data-status=${job.status} data-job=${job.job_id}
              draggable=${canEdit && !busy} onDragStart=${(e) => e.dataTransfer.setData("text/plain", job.job_id)}
              onDragOver=${(e) => { if (canEdit && !busy) { e.preventDefault(); setOver(job.job_id); } }}
              onDragLeave=${() => setOver(null)} onDragEnd=${() => setOver(null)} onDrop=${(e) => !busy && drop(e, job)}>
              <div class="queue-order" title=${canEdit ? "Drag to reorder, or use the arrow buttons" : undefined}>
                <strong>${canEdit ? index + 1 : job.status === "running" ? "▶" : job.status === "blocked" ? "!" : "✓"}</strong>
                <small>${canEdit ? "next" : job.status === "running" ? "now" : job.status === "blocked" ? "held" : "done"}</small>
                ${canEdit && html`<button type="button" aria-label=${`Move ${job.name} up`} disabled=${busy || index === 0} onClick=${() => move(job, -1)}>▲</button>
                  <button type="button" aria-label=${`Move ${job.name} down`} disabled=${busy || index === pending.length - 1} onClick=${() => move(job, 1)}>▼</button>`}
              </div>
              <div class="queue-thumb">${job.reference_url ? html`<img src=${job.reference_url} alt=${`Reference for ${job.name}`} />` : html`<span>No reference</span>`}</div>
              <div class="queue-body">
                <div class="queue-who"><h3>${job.name}</h3><span class=${`queue-chip queue-status ${job.display_status}`}>${job.display_status}</span>
                  <span class="queue-sub">${job.deliverable === "single" ? "single part" : job.deliverable} ${job.family && `· ${job.family}`}</span></div>
                <div class="queue-entrants">${job.entrants.map((e) => html`<span key=${e.id} class="queue-chip">${e.label}</span>`)}</div>
                <dl class="queue-meta"><div><dt>OpenRouter cap</dt><dd>${money(job.cap_usd)}</dd></div><div><dt>Drafted by</dt><dd>${job.drafted_by}</dd></div>
                  <div class="queue-job-id"><dt>Job</dt><dd>${job.job_id}</dd></div></dl>
                ${job.reason && html`<p class="queue-reason">${job.reason}</p>`}
                <div class="queue-actions">${canEdit && html`
                  <button class="button button-quiet" aria-pressed=${selected === job.job_id} disabled=${busy} onClick=${() => { setSelected(job.job_id); setPreflight(null); }}>${selected === job.job_id ? "Selected for preflight" : "Select for preflight"}</button>
                  <button class="button button-quiet" title="Moves this night to the end of the queue" disabled=${busy} onClick=${() => edit("tomorrow", job.job_id)}>Move to tomorrow</button>
                  <button class="button button-quiet" disabled=${busy} onClick=${() => edit("remove", job.job_id)}>Remove</button>`}
                  ${job.status === "running" && html`<a class="button button-quiet" href="#/nightly">Watch live</a>`}
                  ${job.status === "blocked" && html`<a class="button button-quiet" href=${job.run_id ? `#/review/${encodeURIComponent(job.run_id)}` : "#/review"}>Open in Morning Review</a>`}
                </div>
              </div>
            </li>`;
          })}
        </ol>
        ${removed.map((job) => html`<div key=${job.job_id} class="queue-undo"><span>${job.name} was removed from tonight.</span>
          ${job.can_undo && html`<button class="button button-quiet" disabled=${busy} onClick=${() => edit("undo", job.job_id)}>Undo</button>`}</div>`)}
      </section>
      <aside class="queue-rail" aria-label="Night controls">
        <section class="queue-box" aria-labelledby="queue-go"><h2 id="queue-go">GO switch <a href="https://github.com/tonykoop/makerbench-hwe/issues/664" target="_blank" rel="noopener noreferrer">(#664)</a></h2>
          <div class="queue-go-row"><div><b>Scheduled task</b><p class="muted">makerbench-nightly-cad</p></div><button type="button" class="queue-switch" role="switch" aria-checked=${status.data?.task.startsWith("Enabled") || false} aria-disabled="true" disabled title="Read-only. This page never enables the task."><span></span></button></div>
          <dl class="queue-kv"><dt>Task state</dt><dd>${status.data?.task || "unknown (Windows task)"}</dd><dt>Written GO</dt><dd>${status.data?.written_go.text || "checking…"}</dd></dl>
          <p class="muted">This page never enables the task. Written GO is checked at most once every 10 minutes.</p>
          <h3>GO checklist</h3><ul class="queue-checklist"><li>Reference image and supported entrants</li><li>CLI, backend and bridge readiness</li><li>Credential presence and spending caps</li><li>Free nightly seat and a successful preflight</li><li>Supervised night reviewed, then written GO on #664</li></ul>
        </section>
        <section class="queue-box" aria-labelledby="queue-lights"><h2 id="queue-lights">Status lights</h2>
          ${status.status === "loading" && html`<${Loading} label="Checking presence and bridge pings…" />`}
          ${status.error && html`<${ErrorState} error=${status.error} onRetry=${status.reload} />`}
          <ul class="queue-lights">${(status.data?.lights || []).map((light) => html`<li key=${light.label}><span class=${`queue-dot ${light.state}`} aria-hidden="true"></span><span>${light.label}</span><small>${light.detail}</small></li>`)}</ul>
        </section>
        <section class="queue-box" aria-labelledby="queue-preflight"><h2 id="queue-preflight">Preflight</h2>
          <p class="muted">${chosen ? `Preflight for ${chosen.name} (${chosen.entrants.length} entrants). Spends nothing.` : "Select a night, then run preflight. It spends nothing."}</p>
          ${preflight && html`<div data-testid="preflight-result"><ul class="queue-checks">${preflight.checks.map((c) => html`<${Check} key=${c.label} ok=${c.ok} label=${c.label} />`)}</ul>
            ${preflight.entrants.map((e) => html`<div class="queue-entrant-check" key=${e.id}><b class=${e.ok ? "queue-good" : "queue-bad"}>${e.ok ? "GO" : "NO-GO"}</b> ${e.route}<ul>${e.notes.map((note) => html`<li>${note}</li>`)}</ul></div>`)}
            <p class=${preflight.ok ? "queue-good" : "queue-bad"}><b>${preflight.ok ? "GO · Night can start" : "NO-GO · Resolve the failed checks"}</b></p></div>`}
          <button type="button" class="button" disabled=${!chosen || busy} onClick=${runPreflight}>${busy && chosen ? "Working…" : "Run preflight"}</button>
        </section>
      </aside>
    </div>
  </div>`;
}
