import { useEffect, useState } from "preact/hooks";
import { html } from "../html.js";
import { api } from "../lib/api.js";
import { ControlRoomTabs } from "../components/controlRoomTabs.js";

const duration = n => n == null ? "not recorded" : `${Math.floor(n / 60)}m ${Math.floor(n % 60)}s`;
const money = n => typeof n === "number" ? `$${n.toFixed(2)}` : "not recorded";
export function LiveScreen() {
  const [data, setData] = useState(null), [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false), [message, setMessage] = useState("");
  const [transcript, setTranscript] = useState(null), [captures, setCaptures] = useState({});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    const load = async () => {
      try { const next = await api("/api/live", { signal: controller.signal }); setData(next); setError(""); }
      catch (e) { if (e.name !== "AbortError") { setData(null); setError(e.message); } }
      if (!controller.signal.aborted) timer = setTimeout(load, 5000);
    };
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [attempt]);
  const job = data?.job, lanes = data?.lanes || [];
  useEffect(() => { setConfirm(false); setTranscript(null); setCaptures({}); }, [job?.run_id]);
  const base = job ? `/api/live/${encodeURIComponent(job.run_id)}` : "";
  const action = async (fn) => { try { await fn(); } catch(e) { setMessage(e.message); } };
  const capture = async l => {
    const response = await fetch(`${base}/capture/${encodeURIComponent(l.id)}`, {method:"POST"});
    if (!response.ok) throw new Error((await response.json()).detail);
    const reader = new FileReader();
    reader.onload = () => setCaptures(c => ({...c, [job.run_id + l.id]: reader.result}));
    reader.readAsDataURL(await response.blob());
  };
  return html`<section class="screen live-screen">
    <header class="review-top"><div><p class="review-eyebrow">Control Room · Live</p><h1 tabindex="-1">Tonight, as it happens</h1></div><${ControlRoomTabs} current="live" /></header>
    ${error && html`<p role="alert">${error} <button type="button" class="button button-quiet" onClick=${()=>setAttempt(n=>n+1)}>Try again</button></p>`}
    ${!data && !error && html`<p role="status">Loading Live…</p>`}
    ${data && !job && html`<p>No night is running. Next: ${data.next.join(", ") || "nothing queued"}.</p>`}
    ${job && html`<section class="live-job" aria-label="Current job"><p><b>${job.instrument}</b> · ${job.deliverable} · job ${job.position ?? "?"} of ${job.total || "?"} · started ${job.started || "not recorded"} · elapsed ${duration(job.elapsed_s)}<br/><b>${job.finished}</b> of ${lanes.length} entrants finished · spend ${money(job.spend)}</p>
      <div class="live-progress" aria-label="Entrant progress">${lanes.map(l=>html`<span class=${l.state} title=${`${l.id}: ${l.state}`}></span>`)}</div>
      <p>Next: ${data.next.join(", ") || "nothing queued"}</p></section>
      <div class="live-layout"><ol class="live-lanes">${lanes.map(l=>html`<li class="live-lane" data-state=${l.state} key=${l.id}>
        <div class="live-render">${captures[job.run_id+l.id] || l.has_preview ? html`<img src=${captures[job.run_id+l.id] || `${base}/render/${encodeURIComponent(l.id)}`} alt=${`Latest render for ${l.id}`} />` : html`<span>${l.state === "failed" ? "no mesh produced" : "waiting for the first export"}</span>`}</div>
        <div class="live-body"><div class="live-who"><h2>${l.id}</h2><span class=${`live-chip ${l.state}`}>${l.state}${l.inferred_active ? " (inferred)" : ""}</span></div><p class="hint">${l.route}</p>
          <div class="live-turns">Turns ${l.turns.map(t=>html`<span class=${t.state} title=${t.state}>${t.turn}</span>`)}</div>
          <p>Time <b>${duration(l.elapsed_s)}</b> · Cost <b>${money(l.cost_usd)}</b> (${l.cost_source})</p>
          <pre class="live-log">${l.tail.join("\n")}</pre><div class="live-actions">
            <button type="button" class="button button-quiet" onClick=${()=>action(async()=>setTranscript((await api(`${base}/transcript/${encodeURIComponent(l.id)}`)).text))}>Open full transcript</button>
            ${l.has_native && l.state === "scored" && html`<button type="button" class="button button-quiet" onClick=${()=>action(async()=>{await api(`${base}/open/${encodeURIComponent(l.id)}`,{method:"POST"});setMessage("Native file opened");})}>Open native file</button>`}
            ${l.live && l.state === "running" && html`<button type="button" class="button button-quiet" onClick=${()=>action(()=>capture(l))}>Capture view now</button>`}
          </div></div></li>`)}</ol>
      <aside class="live-rail" aria-label="Night controls"><section class="live-box"><h2>Seats and runtimes</h2>${data.lights.map(l=>html`<p><b>${l.label}</b><br/><span class="hint">${l.detail}</span></p>`)}</section>
        <section class="live-box"><h2>This night so far</h2><p>Jobs done: ${data.totals.jobs_done}<br/>Designs scored: ${data.totals.scored}<br/>Valid renders: ${data.totals.valid}<br/>Failed: ${data.totals.failed}<br/>Spend: ${money(job.spend)} of ${money(job.cap)}<br/>Lock: pid ${data.lock.pid ?? "unknown"} · ${data.lock.alive === null ? "liveness unknown" : data.lock.alive ? "alive" : "not alive"}</p></section>
        <section class="live-box"><h2>Stop</h2><p>Finish the current entrant, grade what exists, and mark the rest stopped. Queued jobs stay queued.</p>
          <button type="button" class="button button-quiet" disabled=${!data.writes || job.stop_requested} onClick=${()=>setConfirm(true)}>${job.stop_requested ? "Stopping after this entrant…" : "Stop after this entrant"}</button>
          ${!data.writes && html`<p class="hint">Stop requests are disabled for this Studio.</p>`}
          ${confirm && html`<div class="live-confirm"><p>Stop ${job.instrument} after this entrant finishes?</p><button type="button" class="button button-quiet" onClick=${()=>action(async()=>{await api(`${base}/stop`,{method:"POST"});setConfirm(false);setAttempt(n=>n+1);setMessage("Stop requested; the current build continues.");})}>Yes, stop after this entrant</button><button type="button" class="button button-quiet" onClick=${()=>setConfirm(false)}>Keep going</button></div>`}
          <button type="button" class="button button-quiet" disabled title="No entrant has reported waiting on a seat">Skip waiting entrant</button>
        </section></aside></div>`}
    ${message && html`<p role="status">${message}</p>`}
    ${transcript !== null && html`<section class="live-box" aria-label="Full transcript"><h2>Full transcript (bounded to 2 MiB)</h2><button type="button" class="button button-quiet" onClick=${()=>setTranscript(null)}>Close transcript</button><pre class="live-transcript">${transcript}</pre></section>`}
  </section>`;
}
