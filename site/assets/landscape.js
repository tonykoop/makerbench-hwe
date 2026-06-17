/* MakerBench "Benchmark of Benchmarks" — vanilla JS.
   Reads data/landscape.json (generated from docs/landscape.yaml by
   site/landscape_data.py) and renders a filterable index of the
   spatial-intelligence / hardware-engineering / code-CAD benchmark landscape.
   Self-contained: no dependency on app.js. Issue #285. */
(function () {
  "use strict";
  var ROOT = document.getElementById("lscape-container");
  if (!ROOT) return;
  var CONTROLS = document.getElementById("lscape-controls");
  var META = document.getElementById("lscape-meta");

  // ---- coarse classifiers (map the rich YAML vocab to filterable buckets) ----
  function category(e) {
    var s = (e.scope || "").toLowerCase();
    if (s.indexOf("physics") === 0) return "Physics / simulation";
    if (s.indexOf("manufactur") === 0) return "Manufacturability";
    if (s.indexOf("workflow") === 0) return "Workflow";
    if (s.indexOf("knowledge") === 0) return "Knowledge QA";
    if (s.indexOf("shape") === 0) return "Shape / CAD-gen";
    return "Other";
  }
  function kind(e) {
    var t = (e.type || "").toLowerCase();
    if (t.indexOf("benchmark") !== -1 || t.indexOf("leaderboard") !== -1) return "Benchmark";
    if (t.indexOf("dataset") !== -1) return "Dataset";
    if (t.indexOf("method") !== -1) return "Method";
    return "Other";
  }
  function grade(e) {
    var g = (e.grading || "").toLowerCase();
    if (g.indexOf("n/a") === 0 || g.indexOf("comparative") === 0) return { label: "Method self-eval", tone: "faint" };
    if (g.indexOf("deterministic") !== -1) return { label: "Deterministic", tone: "good" };
    if (g.indexOf("simulation-fea") !== -1 || g.indexOf("in-loop physics") !== -1) return { label: "Simulation / FEA", tone: "control" };
    if (g.indexOf("vlm-judge") !== -1 || g.indexOf("llm-judge") !== -1) return { label: "LLM / VLM judge", tone: "mid" };
    if (g.indexOf("human-pref") !== -1) return { label: "Human preference", tone: "mid" };
    if (g.indexOf("executab") !== -1) return { label: "Executability", tone: "good" };
    if (g.indexOf("task-completion") !== -1) return { label: "Task completion", tone: "mid" };
    if (g.indexOf("mixed") !== -1) return { label: "Mixed", tone: "mid" };
    return { label: e.grading || "—", tone: "faint" };
  }

  function uniq(arr) { var seen = {}, out = []; arr.forEach(function (v) { if (v && !seen[v]) { seen[v] = 1; out.push(v); } }); return out.sort(); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  // Only allow http(s) URLs into an href; esc() does not neutralize a
  // javascript:/data: scheme, and source/source_alt come from the landscape
  // YAML. Anything else collapses to "#" so it can't execute.
  function safeUrl(url) { var u = String(url == null ? "" : url).trim(); return /^https?:\/\//i.test(u) ? u : "#"; }
  function host(url) { try { return String(url).replace(/^https?:\/\//, "").split("/")[0] || "link"; } catch (e) { return "link"; } }

  function selectEl(id, label, opts) {
    var wrap = document.createElement("div");
    wrap.className = "lscape-picker";
    var lab = document.createElement("label"); lab.setAttribute("for", id); lab.textContent = label;
    var sel = document.createElement("select"); sel.id = id;
    var all = document.createElement("option"); all.value = ""; all.textContent = "All"; sel.appendChild(all);
    opts.forEach(function (o) { var op = document.createElement("option"); op.value = o; op.textContent = o; sel.appendChild(op); });
    wrap.appendChild(lab); wrap.appendChild(sel);
    return wrap;
  }

  var state = { category: "", kind: "", grading: "", recent: false, q: "" };

  function render(entries) {
    var q = state.q.trim().toLowerCase();
    var rows = entries.filter(function (e) {
      if (state.category && e._cat !== state.category) return false;
      if (state.kind && e._kind !== state.kind) return false;
      if (state.grading && e._grade.label !== state.grading) return false;
      if (state.recent && !e.recent) return false;
      if (q) {
        var hay = (e.name + " " + (e.framing_own || "") + " " + (e.backing || "") + " " + (e.grading_detail || "") + " " + (e.note || "")).toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    });
    // pin MakerBench-HWE first, then keep source order
    rows.sort(function (a, b) { return (b.is_self ? 1 : 0) - (a.is_self ? 1 : 0); });

    if (!rows.length) { ROOT.innerHTML = '<p class="muted-note">No benchmarks match these filters.</p>'; return; }

    var html = '<div class="lscape-tablewrap"><table class="lscape"><thead><tr>' +
      "<th>Project</th><th>Category</th><th>What it measures</th><th>Grading</th><th>Inputs &rarr; outputs</th><th>Backing</th><th>Date</th>" +
      "</tr></thead><tbody>";
    rows.forEach(function (e, i) {
      var io = (e.input_modality || []).slice(0, 3).join(", ") + (e.output_representation && e.output_representation.length ? " &rarr; " + e.output_representation.slice(0, 2).join(", ") : "");
      html += '<tr class="lscape-row' + (e.is_self ? " lscape-self" : "") + '" data-i="' + i + '">' +
        '<td class="lscape-name">' +
          '<a href="' + esc(safeUrl(e.source)) + '" target="_blank" rel="noopener">' + esc(e.name) + "</a>" +
          (e.recent ? ' <span class="lscape-new" title="Flagged in the latest landscape sweep as a v1 or major revision (curated)">NEW</span>' : "") +
          (e.is_self ? ' <span class="lscape-mine">this benchmark</span>' : "") +
          '<span class="lscape-kind">' + esc(e._kind) + "</span>" +
        "</td>" +
        "<td>" + esc(e._cat) + "</td>" +
        '<td class="lscape-measures">' + esc(e.framing_own || "") + "</td>" +
        '<td><span class="lscape-grade tone-' + e._grade.tone + '">' + esc(e._grade.label) + "</span></td>" +
        '<td class="lscape-io">' + esc(io || "—") + "</td>" +
        "<td>" + esc(e.backing || "—") + "</td>" +
        "<td>" + esc(e.date || "—") + "</td>" +
        "</tr>";
      // detail row (hidden until the row is clicked)
      var detailBits = [];
      if (e.grading_detail) detailBits.push("<strong>Grading:</strong> " + esc(e.grading_detail));
      if (e.dataset_scale) detailBits.push("<strong>Scale:</strong> " + esc(e.dataset_scale));
      if (e.agentic) detailBits.push("<strong>Agentic:</strong> " + esc(e.agentic));
      if (e.anti_memorization) detailBits.push("<strong>Anti-memorization:</strong> " + esc(e.anti_memorization));
      if (e.integrity) detailBits.push("<strong>Integrity:</strong> " + esc(e.integrity));
      if (e.openness) detailBits.push("<strong>Openness:</strong> " + esc(e.openness));
      if (e.note) detailBits.push("<strong>Note:</strong> " + esc(e.note));
      var links = e.source ? '<a href="' + esc(safeUrl(e.source)) + '" target="_blank" rel="noopener">' + esc(host(e.source)) + "</a>" : "";
      if (e.source_alt) links += (links ? " · " : "") + '<a href="' + esc(safeUrl(e.source_alt)) + '" target="_blank" rel="noopener">' + esc(host(e.source_alt)) + "</a>";
      html += '<tr class="lscape-detail" data-for="' + i + '" hidden><td colspan="7"><div class="lscape-detail-grid">' +
        detailBits.map(function (b) { return "<div>" + b + "</div>"; }).join("") +
        '<div class="lscape-links">' + links + "</div>" +
        "</div></td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<p class="muted-note lscape-count">' + rows.length + " of " + entries.length + " projects shown. Click a row for grading detail and links.</p>";
    ROOT.innerHTML = html;

    // expand/collapse detail rows
    Array.prototype.forEach.call(ROOT.querySelectorAll("tr.lscape-row"), function (tr) {
      tr.addEventListener("click", function (ev) {
        if (ev.target && ev.target.tagName === "A") return; // let links work
        var d = ROOT.querySelector('tr.lscape-detail[data-for="' + tr.getAttribute("data-i") + '"]');
        if (d) { d.hidden = !d.hidden; tr.classList.toggle("is-open", !d.hidden); }
      });
    });
  }

  fetch("data/landscape.json", { cache: "no-cache" })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (data) {
      var entries = (data.entries || []).map(function (e) {
        e._cat = category(e); e._kind = kind(e); e._grade = grade(e); return e;
      });
      if (META) {
        var sd = data.sweep && data.sweep.date ? data.sweep.date : "";
        META.textContent = data.count + " projects" + (sd ? " · last sweep " + sd : "");
      }
      // build facet controls
      if (CONTROLS) {
        var cats = uniq(entries.map(function (e) { return e._cat; }));
        var kinds = uniq(entries.map(function (e) { return e._kind; }));
        var grades = uniq(entries.map(function (e) { return e._grade.label; }));
        CONTROLS.appendChild(selectEl("lscape-cat", "Category", cats));
        CONTROLS.appendChild(selectEl("lscape-kind", "Kind", kinds));
        CONTROLS.appendChild(selectEl("lscape-grade", "Grading", grades));
        var rc = document.createElement("label"); rc.className = "lscape-check";
        rc.innerHTML = '<input type="checkbox" id="lscape-recent"> Recently flagged';
        rc.title = "Entries the latest quarterly sweep flagged as a v1 or major revision (curated)";
        CONTROLS.appendChild(rc);
        var search = document.createElement("input");
        search.type = "search"; search.id = "lscape-q"; search.placeholder = "Search…"; search.className = "lscape-search";
        CONTROLS.appendChild(search);

        document.getElementById("lscape-cat").addEventListener("change", function (e) { state.category = e.target.value; render(entries); });
        document.getElementById("lscape-kind").addEventListener("change", function (e) { state.kind = e.target.value; render(entries); });
        document.getElementById("lscape-grade").addEventListener("change", function (e) { state.grading = e.target.value; render(entries); });
        document.getElementById("lscape-recent").addEventListener("change", function (e) { state.recent = e.target.checked; render(entries); });
        search.addEventListener("input", function (e) { state.q = e.target.value; render(entries); });
      }
      render(entries);
    })
    .catch(function (err) {
      ROOT.innerHTML = '<p class="muted-note">Could not load <code>data/landscape.json</code> — run <code>python site/landscape_data.py</code> first. (' + esc(err.message) + ")</p>";
    });
})();
