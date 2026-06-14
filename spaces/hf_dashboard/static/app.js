// app.js — MakerBench HWE dual-league dashboard UI (#98).
//
// Fetches the baked payloads from the FastAPI shell (app.py) and renders the two
// tabs. The 3D viewer (viewer.js) is dynamically imported only when a run with a
// 3D artifact is opened, so the leaderboards stay light.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

// --- tabs ------------------------------------------------------------------
$$(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    const name = btn.dataset.tab;
    $$(".tab-panel").forEach((p) =>
      p.classList.toggle("active", p.id === `tab-${name}`));
    if (name === "inspect") loadInspect();
  }));

// --- Tab A: leagues --------------------------------------------------------
function scoreText(v) {
  return typeof v === "number" ? v.toFixed(2) : "—";
}

function leagueRow(r) {
  const sub = [r.tasks.length + " task" + (r.tasks.length === 1 ? "" : "s"),
               r.n_runs + " run" + (r.n_runs === 1 ? "" : "s")].join(" · ");
  const hii = r.hii_best && r.hii_best !== "unknown"
    ? `<span class="pill pill-hii">HII ${esc(r.hii_best)}</span>` : "";
  const verified = r.verification?.verified
    ? `<span class="pill pill-ok">${r.verification.verified} verified</span>` : "";
  return `<tr>
    <td class="rank">${r.rank}</td>
    <td><span class="headline">${esc(r.headline)}</span>
        <span class="sub">${esc(sub)}</span></td>
    <td>${hii}${verified}</td>
    <td class="score">${scoreText(r.mean_score)}</td>
  </tr>`;
}

function renderLeague(rootId, league) {
  const root = $(`#${rootId}`);
  $(".league-blurb", root).textContent = league.blurb || "";
  const rows = league.rows || [];
  $(".league-body", root).innerHTML = rows.length
    ? `<table class="board"><thead><tr>
         <th>#</th><th>${rootId.endsWith("workflow") ? "Stack" : "Model"}</th>
         <th>Signals</th><th>Mean</th></tr></thead>
       <tbody>${rows.map(leagueRow).join("")}</tbody></table>`
    : `<p class="empty">No runs in this league yet.</p>`;
}

async function loadLeagues() {
  try {
    const data = await getJSON("api/leagues");
    renderLeague("league-autonomous", data.leagues.autonomous);
    renderLeague("league-workflow", data.leagues.workflow);
  } catch (err) {
    $("#league-autonomous .league-body").innerHTML =
      `<p class="empty">Could not load leagues (${esc(err.message)}).</p>`;
  }
}

// --- Tab B: inspect --------------------------------------------------------
let inspectLoaded = false;
let inspectRuns = [];

function runListItem(c) {
  return `<li data-run="${esc(c.run_id)}" data-hay="${esc(
      (c.run_id + " " + (c.stack || "") + " " + (c.task_id || "") + " " + (c.model_identifier || "")).toLowerCase())}">
    <span class="rl-league pill ${c.league === "workflow" ? "pill-hii" : ""}">${esc(c.league)}</span>
    <span class="rl-id">${esc(c.run_id)}</span><br>
    <span class="rl-meta">${esc(c.stack || c.model_identifier || "")} · ${esc(c.task_id || "")} · ${esc(c.track || "")}</span>
  </li>`;
}

async function loadInspect() {
  if (inspectLoaded) return;
  inspectLoaded = true;
  try {
    const data = await getJSON("api/inspect");
    inspectRuns = data.runs || [];
    $("#inspect-list").innerHTML = inspectRuns.map(runListItem).join("") ||
      `<li class="empty">No inspectable runs.</li>`;
    $$("#inspect-list li[data-run]").forEach((li) =>
      li.addEventListener("click", () => openRun(li.dataset.run, li)));
  } catch (err) {
    $("#inspect-list").innerHTML = `<li class="empty">Could not load runs (${esc(err.message)}).</li>`;
  }
}

$("#inspect-search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  $$("#inspect-list li[data-run]").forEach((li) => {
    li.style.display = !q || li.dataset.hay.includes(q) ? "" : "none";
  });
});

function verdictCard(verdict) {
  if (!verdict?.length) return "";
  return verdict.map((v) => {
    const levels = v.levels.map((lv) =>
      `<li class="lvl ${lv.passed ? "pass" : "fail"}">
        <span class="lvl-n">L${esc(lv.level)}</span>
        <span class="lvl-v">${lv.passed ? "PASS" : "FAIL"}</span>
        <span class="lvl-d">${esc(lv.detail)}</span></li>`).join("");
    return `<div class="card"><h3>Grader verdict — ${esc(v.task_id)}
        <span class="score">score ${scoreText(v.score)}</span></h3>
      <ul class="levels">${levels}</ul>
      ${v.notes ? `<p class="muted">${esc(v.notes)}</p>` : ""}</div>`;
  }).join("");
}

function traceCard(wf) {
  const steps = wf?.tool_call_log || [];
  const trace = steps.length
    ? `<ul class="trace">${steps.map((s) =>
        `<li><span class="tool">${esc(s.tool)}</span>${
          s.note ? ` <span class="note">— ${esc(s.note)}</span>` : ""}</li>`).join("")}</ul>`
    : `<p class="muted">No WorkflowManifest trace — autonomous or undisclosed.</p>`;
  const wall = typeof wf?.wall_clock_seconds === "number" ? `${wf.wall_clock_seconds}s` : "—";
  return `<div class="card"><h3>Workflow trace &amp; HII</h3>
    <dl class="kv">
      <dt>HII</dt><dd>${esc(wf?.hii ?? "unknown")}</dd>
      <dt>Wall clock</dt><dd>${esc(wall)}</dd>
      <dt>Tool calls</dt><dd>${steps.length}</dd>
    </dl>${trace}</div>`;
}

async function openRun(runId, li) {
  $$("#inspect-list li").forEach((x) => x.classList.toggle("active", x === li));
  const detail = $("#inspect-detail");
  detail.innerHTML = `<p class="muted">Loading ${esc(runId)}…</p>`;
  let d;
  try {
    d = await getJSON(`api/run/${encodeURIComponent(runId)}`);
  } catch (err) {
    detail.innerHTML = `<p class="empty">Could not load ${esc(runId)} (${esc(err.message)}).</p>`;
    return;
  }
  const art = d.artifact_3d;
  detail.innerHTML = `
    <div class="detail-head">
      <h2>${esc(d.run_id)}</h2>
      <span class="pill ${d.league === "workflow" ? "pill-hii" : ""}">${esc(d.league)}</span>
      <span class="pill">${esc(d.harness_class)}</span>
      <span class="score">score ${scoreText(d.score)}</span>
    </div>
    <p class="muted">${esc(d.stack)}</p>
    <div class="detail-grid">
      <div>
        <div class="card"><h3>Artifact — 3D viewer</h3>
          <div id="viewer-host">
            <div class="viewer-note">${art ? "Loading viewer…" : "No 3D artifact in this run."}</div>
          </div>
          <div class="viewer-tools">
            ${art ? `<button id="btn-wire">Wireframe</button>
                     <a class="pill" href="artifacts/${encodeURIComponent(d.run_id)}/${encodeURIComponent(art.file)}"
                        target="_blank" rel="noopener">download ${esc(art.kind)}</a>` : ""}
          </div>
          ${art && art.kind === "step"
            ? `<p class="muted">STEP B-Rep — tessellated to glTF server-side by the
               Docker Space's headless geometry stack. Interference-zone /
               wall-thickness heatmap overlay pending (additive slot).</p>` : ""}
        </div>
        ${traceCard(d.workflow)}
      </div>
      <div>
        ${verdictCard(d.verdict)}
        <div class="card"><h3>Deliverable packet &amp; certificate</h3>
          ${d.packet?.has_packet
            ? `<ul class="trace">${d.packet.files.map((f) =>
                `<li><a href="artifacts/${encodeURIComponent(d.run_id)}/${encodeURIComponent(f)}"
                   target="_blank" rel="noopener">${esc(f)}</a></li>`).join("")}</ul>`
            : `<p class="muted">No deliverable packet attached.</p>`}
          <dl class="kv">
            <dt>Certificate</dt><dd>${d.certificate?.has_certificate ? esc(d.certificate.file) : "none"}</dd>
            <dt>Verification</dt><dd>${esc(d.certificate?.verification ?? "pending")}</dd>
          </dl>
        </div>
      </div>
    </div>`;

  if (art) {
    try {
      const { mountViewer } = await import("./viewer.js");
      mountViewer($("#viewer-host"),
        `artifacts/${encodeURIComponent(d.run_id)}/${encodeURIComponent(art.file)}`,
        art.kind, $("#btn-wire"));
    } catch (err) {
      $("#viewer-host .viewer-note").textContent =
        "3D viewer unavailable (WebGL or CDN blocked). Use the download link.";
    }
  }
}

loadLeagues();
