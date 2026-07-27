/* MakerBench "Benchmark of Benchmarks" — vanilla JS.
   Reads data/landscape.json (generated from docs/landscape.yaml by
   site/landscape_data.py) and renders a filterable index of the
   spatial-intelligence / hardware-engineering / code-CAD benchmark landscape.
   Rebuilds from docs/landscape.yaml via `python site/landscape_data.py`.
   No dependency on app.js. Issue #285. */
(function () {
  "use strict";
  var ROOT = document.getElementById("lscape-container");
  if (!ROOT) return;
  var CONTROLS = document.getElementById("lscape-controls");
  var META = document.getElementById("lscape-meta");

  function uniq(arr) {
    var seen = {};
    var out = [];
    arr.forEach(function (v) {
      if (v && !seen[v]) {
        seen[v] = 1;
        out.push(v);
      }
    });
    return out.sort();
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
      }[c];
    });
  }

  // Only allow http(s) URLs into an href; esc() does not neutralize a
  // javascript:/data: scheme. Anything else collapses to "#" so it can't execute.
  function safeUrl(url) {
    var u = String(url == null ? "" : url).trim();
    return /^https?:\/\//i.test(u) ? u : "#";
  }

  function host(url) {
    try {
      return String(url).replace(/^https?:\/\//, "").split("/")[0] || "link";
    } catch (e) {
      return "link";
    }
  }

  function labelAxis(axis) {
    return axis && axis.length ? axis.join(", ") : "uncategorized";
  }

  function deriveAxis(entry) {
    var s = String(entry.scope || "").toLowerCase();
    var out = [];
    if (s.indexOf("physics") !== -1 || s.indexOf("simulation") !== -1) {
      out.push("physics-sim");
    }
    if (s.indexOf("manufactur") !== -1 || s.indexOf("sheet") !== -1) {
      out.push("dfm");
    }
    if (s.indexOf("assembly") !== -1) {
      out.push("reverse-engineering");
    }
    if (s.indexOf("shape") !== -1) {
      out.push("spatial-intelligence");
      out.push("code-cad");
    }
    if (s.indexOf("hardware") !== -1 || s.indexOf("workflow") !== -1) {
      out.push("hardware-engineering");
    }
    if (!out.length) {
      out.push("spatial-intelligence");
    }
    return uniq(out);
  }

  function grade(e) {
    var g = String(e.grading || "").toLowerCase();
    if (g.indexOf("n/a") === 0 || g.indexOf("comparative") === 0) {
      return { label: "Method self-eval", tone: "faint" };
    }
    if (g.indexOf("deterministic") !== -1) {
      return { label: "Deterministic", tone: "good" };
    }
    if (g.indexOf("simulation") !== -1 || g.indexOf("fea") !== -1 || g.indexOf("comsol") !== -1) {
      return { label: "Solver / simulation", tone: "control" };
    }
    if (g.indexOf("vlm-judge") !== -1 || g.indexOf("llm-judge") !== -1) {
      return { label: "LLM / VLM judge", tone: "mid" };
    }
    if (g.indexOf("human") !== -1) {
      return { label: "Human preference", tone: "mid" };
    }
    if (g.indexOf("task") !== -1 && g.indexOf("completion") !== -1) {
      return { label: "Task completion", tone: "mid" };
    }
    if (g.indexOf("mixed") !== -1) {
      return { label: "Mixed", tone: "mid" };
    }
    return { label: e.grading || "—", tone: "faint" };
  }

  function relation(e) {
    if (e.name === "MakerBench-HWE") {
      return "This is the anchor benchmark around which the index is organized.";
    }
    if (String(e.scope || "").toLowerCase().indexOf("physics") !== -1) {
      return "Physics-flavoured neighbor; useful for cross-checking structural/validation claims.";
    }
    if (String(e.scope || "").toLowerCase().indexOf("assembly") !== -1) {
      return "System-level assembly perspective; complements MakerBench's per-part manufacturability checks.";
    }
    if (String(e.scope || "").toLowerCase().indexOf("manufactur") !== -1) {
      return "Shares manufacturing intent and constraints, often at different granularity.";
    }
    if ((e.type || "").toLowerCase().indexOf("dataset") !== -1) {
      return "Dataset / method resource; not a direct one-to-one benchmark score companion.";
    }
    return "Potential comparator lane for broader field coverage and protocol design.";
  }

  function linkCell(entry) {
    var links = [];
    if (entry.source) {
      links.push('<a href="' + esc(safeUrl(entry.source)) + '" target="_blank" rel="noopener">' + esc(host(entry.source)) + "</a>");
    }
    if (entry.source_alt) {
      links.push('<a href="' + esc(safeUrl(entry.source_alt)) + '" target="_blank" rel="noopener">' + esc(host(entry.source_alt)) + "</a>");
    }
    if (!links.length) {
      return "—";
    }
    return links.join(" · ");
  }

  function selectEl(id, label, opts) {
    var wrap = document.createElement("div");
    wrap.className = "lscape-picker";
    var lab = document.createElement("label");
    lab.setAttribute("for", id);
    lab.textContent = label;
    var sel = document.createElement("select");
    sel.id = id;
    var all = document.createElement("option");
    all.value = "";
    all.textContent = "All";
    sel.appendChild(all);
    opts.forEach(function (o) {
      var op = document.createElement("option");
      op.value = o;
      op.textContent = o;
      sel.appendChild(op);
    });
    wrap.appendChild(lab);
    wrap.appendChild(sel);
    return wrap;
  }

  function normalize(entry) {
    var axis = [];
    if (Array.isArray(entry.axis)) {
      axis = entry.axis.slice(0);
    } else if (typeof entry.axis === "string" && entry.axis.trim()) {
      axis = [entry.axis.trim()];
    }
    if (!axis.length) {
      axis = deriveAxis(entry);
    }
    return {
      axis: uniq(axis),
      _grade: grade(entry),
      _relation: relation(entry),
      _links: linkCell(entry),
    };
  }

  function buildControls(entries) {
    var axes = uniq(entries.reduce(function (acc, e) {
      return acc.concat(e.axis);
    }, []));
    var grades = uniq(entries.map(function (e) { return e._grade.label; }));

    var axisEl = selectEl("lscape-axis", "Axis", axes);
    var gradeEl = selectEl("lscape-grade", "Grading", grades);
    var rc = document.createElement("label");
    rc.className = "lscape-check";
    rc.innerHTML = '<input type="checkbox" id="lscape-recent"> Recently flagged';
    rc.title = "Entries the latest sweep flagged as recent";

    var search = document.createElement("input");
    search.type = "search";
    search.id = "lscape-q";
    search.placeholder = "Search index…";
    search.className = "lscape-search";

    CONTROLS.appendChild(axisEl);
    CONTROLS.appendChild(gradeEl);
    CONTROLS.appendChild(rc);
    CONTROLS.appendChild(search);
    return {
      axisEl: axisEl,
      gradeEl: gradeEl,
      recentEl: document.getElementById("lscape-recent"),
      qEl: search,
    };
  }

  var state = { axis: "", grading: "", recent: false, q: "" };

  function render(entries) {
    var rows = entries.filter(function (e) {
      if (state.axis && e.axis.indexOf(state.axis) === -1) return false;
      if (state.grading && e._grade.label !== state.grading) return false;
      if (state.recent && !e.recent) return false;
      if (state.q) {
        var q = state.q;
        var hay = (e.name + " " + (e.framing_own || "") + " " + (e.grading_detail || "") + " " + (e.scope || "") + " " + (e._relation || "")).toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    });

    rows.sort(function (a, b) {
      if ((b.is_self ? 1 : 0) - (a.is_self ? 1 : 0)) {
        return (b.is_self ? 1 : 0) - (a.is_self ? 1 : 0);
      }
      if ((b.recent ? 1 : 0) !== (a.recent ? 1 : 0)) {
        return (b.recent ? 1 : 0) - (a.recent ? 1 : 0);
      }
      return String(a.name).localeCompare(String(b.name));
    });

    if (!rows.length) {
      ROOT.innerHTML = '<p class="muted-note">No benchmarks match these filters.</p>';
      return;
    }

    var html = '<div class="lscape-tablewrap"><table class="lscape"><thead><tr>' +
      '<th>Project</th><th>Axis / category</th><th>What it measures</th>' +
      '<th>Inputs &rarr; outputs</th><th>Grading method</th><th>Freshness</th>' +
      '<th>Links</th><th>How MakerBench relates</th>' +
      '</tr></thead><tbody>';

    rows.forEach(function (e) {
      var io = (e.input_modality || []).slice(0, 3).join(", ");
      if (e.output_representation && e.output_representation.length) {
        if (io) {
          io += " → " + e.output_representation.slice(0, 2).join(", ");
        } else {
          io = e.output_representation.slice(0, 2).join(", ");
        }
      }
      html += '<tr class="lscape-row' + (e.is_self ? " lscape-self" : "") + '">' +
        '<td class="lscape-name">' +
          '<a href="' + esc(safeUrl(e.source)) + '" target="_blank" rel="noopener">' + esc(e.name) + '</a>' +
          (e.recent ? ' <span class="lscape-new" title="Flagged in the latest sweep as a major-revision entry">NEW</span>' : "") +
          (e.is_self ? ' <span class="lscape-mine">this benchmark</span>' : "") +
        '</td>' +
        '<td>' + esc(labelAxis(e.axis)) + '</td>' +
        '<td class="lscape-measures">' + esc(e.framing_own || "") + '</td>' +
        '<td class="lscape-io">' + esc(io || "—") + '</td>' +
        '<td><span class="lscape-grade tone-' + e._grade.tone + '">' + esc(e._grade.label) + '</span></td>' +
        '<td>' + (e.recent ? "new" : "") + ' ' + esc(e.date || "") + '</td>' +
        '<td class="lscape-links">' + e._links + '</td>' +
        '<td class="lscape-rel">' + esc(e._relation) + '</td>' +
      '</tr>';
    });
    html += '</tbody></table></div>';
    html += '<p class="muted-note lscape-count">' + rows.length + ' of ' + entries.length + ' projects shown.</p>';
    ROOT.innerHTML = html;
  }

  fetch("data/landscape.json", { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (data) {
      var entries = (data.entries || []).map(function (entry) {
        var derived = normalize(entry);
        Object.keys(derived).forEach(function (k) { entry[k] = derived[k]; });
        return entry;
      });
      if (META) {
        var sd = data.sweep && data.sweep.date ? data.sweep.date : "";
        META.textContent = data.count + " projects" + (sd ? " · last sweep " + sd : "");
      }

      var controls = buildControls(entries);
      controls.axisEl.addEventListener("change", function (e) {
        state.axis = e.target.value;
        render(entries);
      });
      controls.gradeEl.addEventListener("change", function (e) {
        state.grading = e.target.value;
        render(entries);
      });
      controls.recentEl.addEventListener("change", function (e) {
        state.recent = e.target.checked;
        render(entries);
      });
      controls.qEl.addEventListener("input", function (e) {
        state.q = e.target.value.trim().toLowerCase();
        render(entries);
      });
      render(entries);
    })
    .catch(function (err) {
      ROOT.innerHTML = '<p class="muted-note">Could not load <code>data/landscape.json</code> — run <code>python site/landscape_data.py</code> first. (' + esc(err.message) + ")</p>";
    });
})();
