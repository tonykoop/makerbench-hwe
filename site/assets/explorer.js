// explorer.html v2.0.0 — Spatial Visualizer Sandbox controller (mb#165).
//
// Drives the 3-pane spatial sandbox: a Data Matrix tree (left), a Three.js
// Spatial Viewport with a pluggable layer interface (center), and a Parametric
// Engine readout (right). Everything is data-driven from site/data/explorer.json
// (built by site/build_data.py) and a context switcher swaps the whole layout by
// repo/dir without any code change.
//
// Like site/assets/viewer.js and inspect-run.js, geometry is loaded ONLY from
// the submission-only mesh manifest (mb#107) — never oracle/private geometry —
// and the page degrades gracefully: with WebGL off, the CDN unavailable, or a
// context that ships no assets, the panes stay informative and never break.
//
// three.js is pinned via the importmap injected in explorer.html (three@0.160.0).

import * as THREE from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const DATA_URL = "data/explorer.json";

// --- small deterministic helpers ------------------------------------------
// Seeded PRNG so the procedural overlay/splat layers render identically every
// load — consistent with the site's static, deterministic aesthetic.
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function fmt(v, digits, suffix) {
  if (v == null || (typeof v === "number" && !isFinite(v))) return "—";
  if (typeof v === "number") return v.toFixed(digits == null ? 2 : digits) + (suffix || "");
  return String(v);
}

function themeColors() {
  // Matches the explorer.html workshop palette (walnut / cedar / lapis).
  return { bg: 0x161310, mesh: 0xb98b47, edge: 0x4a3b28, force: 0x1e3a8a, splat: 0xd4a017 };
}

// ===========================================================================
// Pane 2 — Spatial Viewport with a pluggable layer interface.
//
// A layer is { id, label, needsAsset, build(group, ctx, asset) }. build() fills
// a THREE.Group; the viewport mounts/unmounts groups as the user toggles layers.
// New repos can register their own layers without touching the panes.
// ===========================================================================
const LAYERS = {
  // Mesh viewer — the submitted part as a solid + edges. Falls back to a
  // wireframe "twin" placeholder for scaffold contexts that ship no asset.
  mesh: {
    id: "mesh",
    label: "Mesh viewer",
    needsAsset: false,
    build(group, ctx, asset, viewport) {
      const c = themeColors();
      if (asset && asset.mesh) {
        const loader = new STLLoader();
        loader.load(
          asset.mesh,
          (geo) => {
            geo.computeVertexNormals();
            geo.center();
            const mat = new THREE.MeshStandardMaterial({
              color: c.mesh, metalness: 0.1, roughness: 0.65, flatShading: false,
            });
            const mesh = new THREE.Mesh(geo, mat);
            const edges = new THREE.LineSegments(
              new THREE.EdgesGeometry(geo, 30),
              new THREE.LineBasicMaterial({ color: c.edge })
            );
            group.add(mesh, edges);
            viewport.frame(geo);
          },
          undefined,
          () => viewport.note(`Could not load ${asset.mesh} — canvas degraded.`)
        );
      } else {
        // Placeholder digital-twin cube for asset-less (scaffold) contexts.
        const geo = new THREE.BoxGeometry(40, 40, 40);
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(geo),
          new THREE.LineBasicMaterial({ color: c.mesh })
        );
        group.add(edges);
        viewport.frame(geo);
        viewport.note("No submitted asset in this context — placeholder twin shown.");
      }
    },
  },

  // StreamForce-style force overlay — a deterministic force-vector field over
  // the part envelope. Real per-vertex force conditioning lands with the force
  // test lanes; here it's a reproducible scaffold the layout can host today.
  force: {
    id: "force",
    label: "Force overlay",
    needsAsset: false,
    build(group, ctx) {
      const c = themeColors();
      const rng = mulberry32(0x57f0 + (ctx.id || "x").length);
      const N = 7;
      const span = 60;
      for (let i = 0; i < N; i++) {
        for (let j = 0; j < N; j++) {
          const x = (i / (N - 1) - 0.5) * span;
          const z = (j / (N - 1) - 0.5) * span;
          const origin = new THREE.Vector3(x, -28 + rng() * 6, z);
          const dir = new THREE.Vector3(
            (rng() - 0.5) * 0.5, 1, (rng() - 0.5) * 0.5
          ).normalize();
          const len = 14 + rng() * 16;
          group.add(new THREE.ArrowHelper(dir, origin, len, c.force, len * 0.3, len * 0.18));
        }
      }
    },
  },

  // AnchorWorld FPV / exogenous-4D-splat — a deterministic point-splat cloud
  // standing in for the 4D capture; toggling it is the FPV-context entry point.
  fpv: {
    id: "fpv",
    label: "FPV / 4D splat",
    needsAsset: false,
    build(group, ctx) {
      const c = themeColors();
      const rng = mulberry32(0x4d52 + (ctx.id || "y").length);
      const count = 900;
      const pos = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        // A swirled shell — deterministic, reads as a captured splat field.
        const t = i / count;
        const r = 28 + Math.sin(t * 40) * 6;
        const a = t * Math.PI * 18;
        pos[i * 3] = Math.cos(a) * r + (rng() - 0.5) * 8;
        pos[i * 3 + 1] = (t - 0.5) * 60 + (rng() - 0.5) * 6;
        pos[i * 3 + 2] = Math.sin(a) * r + (rng() - 0.5) * 8;
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      group.add(new THREE.Points(
        geo,
        new THREE.PointsMaterial({ color: c.splat, size: 1.6, sizeAttenuation: true })
      ));
    },
  },
};

function createViewport(host) {
  const canvasBox = host.querySelector(".vp-canvas");
  const noteBox = host.querySelector(".vp-note");
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (e) {
    renderer = null;
  }
  if (!renderer) {
    noteBox.textContent =
      "WebGL unavailable — viewport disabled. The Data Matrix and Parametric Engine remain.";
    return null;
  }
  const c = themeColors();
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(c.bg);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 4000);
  camera.position.set(70, 55, 90);
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const key = new THREE.DirectionalLight(0xffffff, 0.8);
  key.position.set(60, 90, 60);
  scene.add(key);
  scene.add(new THREE.GridHelper(160, 16, 0x4a3b28, 0x2c241a));

  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  canvasBox.appendChild(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  const groups = {}; // layerId -> THREE.Group

  function resize() {
    const w = canvasBox.clientWidth || 1;
    const h = canvasBox.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvasBox);
  resize();

  (function loop() {
    requestAnimationFrame(loop);
    controls.update();
    renderer.render(scene, camera);
  })();

  return {
    note: (msg) => { noteBox.textContent = msg || ""; },
    frame(geo) {
      geo.computeBoundingSphere();
      const r = (geo.boundingSphere && geo.boundingSphere.radius) || 40;
      camera.position.set(r * 1.8, r * 1.4, r * 2.2);
      controls.target.set(0, 0, 0);
      controls.update();
    },
    setLayer(layerId, on, ctx, asset) {
      const spec = LAYERS[layerId];
      if (!spec) return;
      if (on) {
        if (groups[layerId]) return;
        const g = new THREE.Group();
        spec.build(g, ctx, asset, this);
        scene.add(g);
        groups[layerId] = g;
      } else if (groups[layerId]) {
        scene.remove(groups[layerId]);
        groups[layerId] = null;
      }
    },
    clear() {
      for (const id of Object.keys(groups)) {
        if (groups[id]) { scene.remove(groups[id]); groups[id] = null; }
      }
    },
  };
}

// ===========================================================================
// Pane 1 — Data Matrix tree (assets · 4D coordinates · telemetry).
// ===========================================================================
function treeNode(label, count) {
  const det = el("details", "tree-branch");
  det.open = false;
  const sum = el("summary");
  sum.appendChild(el("span", "tree-label", label));
  if (count != null) sum.appendChild(el("span", "tree-count", String(count)));
  det.appendChild(sum);
  return det;
}

function renderDataMatrix(host, ctx, onPickAsset) {
  host.innerHTML = "";
  const dm = ctx.data_matrix;
  if (!dm) {
    host.appendChild(
      el("p", "pane-pending",
        `${ctx.label} is a context scaffold — its Data Matrix wires to: ` +
        (ctx.data_sources || []).join(", ") + ".")
    );
    return;
  }

  // Assets branch — clickable; selecting one loads it into the viewport.
  const assets = dm.assets || [];
  const aBranch = treeNode("Assets · submitted parts", assets.length);
  aBranch.open = true;
  if (!assets.length) {
    aBranch.appendChild(el("p", "tree-empty", "No submitted meshes yet."));
  }
  assets.forEach((a, i) => {
    const row = el("button", "asset-row");
    row.type = "button";
    row.appendChild(el("span", "asset-task", a.task_id || "?"));
    row.appendChild(el("span", "asset-model", a.model_identifier || ""));
    const sc = el("span", "asset-score", a.score != null ? `L${a.score}` : "—");
    row.appendChild(sc);
    row.addEventListener("click", () => {
      host.querySelectorAll(".asset-row.sel").forEach((n) => n.classList.remove("sel"));
      row.classList.add("sel");
      onPickAsset(a);
    });
    if (i === 0) row.classList.add("sel");
    aBranch.appendChild(row);
  });
  host.appendChild(aBranch);

  // 4D coordinate branch — model × CAD × plugin × domain (mb#120).
  const co = dm.coordinates || {};
  const coords = co.coordinates || [];
  if (coords.length) {
    const cBranch = treeNode("4D coordinates · model × CAD × plugin", co.counts?.coordinates ?? coords.length);
    const proven = (co.counts && co.counts.proven) || 0;
    const vac = (co.counts && co.counts.vacancies) || 0;
    cBranch.appendChild(
      el("p", "tree-meta", `${proven} proven · ${vac} vacancies`)
    );
    (co.top_vacancies || []).slice(0, 6).forEach((v, i) => {
      const axes = co.axes || {};
      const lbl = (axis, id) =>
        ((axes[axis] || []).find((x) => x.id === id) || {}).label || id;
      const row = el("div", "coord-row");
      row.appendChild(el("span", "coord-rank", String(i + 1)));
      row.appendChild(el("span", "coord-label",
        `${lbl("model", v.model)} × ${lbl("cad", v.cad)} × ${lbl("plugin", v.plugin)}`));
      row.appendChild(el("span", "coord-score", fmt(v.potential_score ?? v.score, 2)));
      cBranch.appendChild(row);
    });
    host.appendChild(cBranch);
  }

  // Telemetry branch — per-model deployment feed.
  const tel = dm.telemetry || [];
  if (tel.length) {
    const tBranch = treeNode("Telemetry · per-model feed", tel.length);
    tel.forEach((t) => {
      const row = el("div", "tel-row");
      row.appendChild(el("span", "tel-model",
        t.identifier + (t.reasoning_level ? ` [${t.reasoning_level}]` : "")));
      row.appendChild(el("span", "tel-score", fmt(t.overall_mean, 2)));
      row.appendChild(el("span", "tel-time", fmt(t.mean_wall_time_s, 0, "s")));
      tBranch.appendChild(row);
    });
    host.appendChild(tBranch);
  }
}

// ===========================================================================
// Pane 3 — Parametric Engine (feature tree · Arbor log · render-diff).
// ===========================================================================
function renderParametricEngine(host, ctx) {
  host.innerHTML = "";
  const pe = ctx.parametric_engine;
  if (!pe) {
    host.appendChild(
      el("p", "pane-pending",
        `${ctx.label} scaffold — Parametric Engine pending its feature-tree source.`)
    );
    return;
  }

  const ft = pe.feature_tree || [];
  const ftBranch = treeNode("Feature tree · capability axes", ft.length);
  ftBranch.open = true;
  ft.forEach((f) => {
    const node = treeNode(f.title || f.id, (f.task_families || []).length);
    (f.task_families || []).forEach((fam) =>
      node.appendChild(el("div", "feat-leaf", fam)));
    ftBranch.appendChild(node);
  });
  host.appendChild(ftBranch);

  // Arbor hypothesis-tree log (mb#162) — pending slot, never invented.
  const arbor = el("div", "pe-slot");
  arbor.appendChild(el("h4", null, "Arbor hypothesis-tree log"));
  if (pe.arbor_log) {
    const pre = el("pre", "arbor-log");
    pre.textContent = JSON.stringify(pe.arbor_log, null, 2);
    arbor.appendChild(pre);
  } else {
    arbor.appendChild(el("p", "pane-pending",
      "Pending — wires to the Arbor runner (mb#162) once it lands."));
  }
  host.appendChild(arbor);

  // Render-diff / surface-alignment score — pending slot.
  const diff = el("div", "pe-slot");
  diff.appendChild(el("h4", null, "Render-diff · surface alignment"));
  if (pe.render_diff != null) {
    diff.appendChild(el("p", "pe-score", fmt(pe.render_diff, 3)));
  } else {
    diff.appendChild(el("p", "pane-pending",
      "Pending — populated from viewer_export render comparison (mb#107)."));
  }
  host.appendChild(diff);
}

// ===========================================================================
// Viewport layer toggles (Pane 2 chrome).
// ===========================================================================
function renderLayerToggles(host, ctx, viewport, getAsset) {
  host.innerHTML = "";
  if (!viewport) return;
  const exposed = (ctx.viewport && ctx.viewport.layers) || ["mesh"];
  const def = (ctx.viewport && ctx.viewport.default_layer) || exposed[0];
  exposed.forEach((id) => {
    const spec = LAYERS[id];
    if (!spec) return;
    const btn = el("button", "vp-layer", spec.label);
    btn.type = "button";
    const on = id === def;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) viewport.setLayer(id, true, ctx, getAsset());
    btn.addEventListener("click", () => {
      const now = btn.getAttribute("aria-pressed") === "true";
      btn.setAttribute("aria-pressed", now ? "false" : "true");
      viewport.setLayer(id, !now, ctx, getAsset());
    });
    host.appendChild(btn);
  });
}

// ===========================================================================
// Boot.
// ===========================================================================
async function boot() {
  const matrixHost = document.getElementById("pane-matrix");
  const engineHost = document.getElementById("pane-engine");
  const togglesHost = document.getElementById("vp-layers");
  const ctxHost = document.getElementById("context-switch");
  const summaryHost = document.getElementById("context-summary");
  const viewport = createViewport(document.getElementById("pane-viewport"));

  let data;
  try {
    data = await (await fetch(DATA_URL)).json();
  } catch (e) {
    matrixHost.appendChild(el("p", "pane-pending",
      "Could not load explorer.json — run `python site/build_data.py`."));
    return;
  }

  const contexts = data.contexts || [];
  let active = contexts.find((c) => c.id === data.active_context) || contexts[0];
  let currentAsset = null;

  function selectContext(ctx) {
    active = ctx;
    currentAsset = (ctx.data_matrix && (ctx.data_matrix.assets || [])[0]) || null;
    ctxHost.querySelectorAll(".ctx-btn").forEach((b) =>
      b.setAttribute("aria-pressed", b.dataset.id === ctx.id ? "true" : "false"));
    summaryHost.innerHTML = "";
    summaryHost.appendChild(el("span", "ctx-repo", ctx.repo || ctx.id));
    summaryHost.appendChild(el("span", "ctx-text", ctx.summary || ""));
    if (ctx.scaffold) summaryHost.appendChild(el("span", "ctx-tag", "scaffold"));

    renderDataMatrix(matrixHost, ctx, (a) => {
      currentAsset = a;
      if (viewport) { viewport.clear(); }
      renderLayerToggles(togglesHost, ctx, viewport, () => currentAsset);
    });
    renderParametricEngine(engineHost, ctx);
    if (viewport) {
      viewport.clear();
      viewport.note("");
    }
    renderLayerToggles(togglesHost, ctx, viewport, () => currentAsset);
  }

  // Context switcher.
  contexts.forEach((ctx) => {
    const btn = el("button", "ctx-btn", ctx.label);
    btn.type = "button";
    btn.dataset.id = ctx.id;
    btn.addEventListener("click", () => selectContext(ctx));
    ctxHost.appendChild(btn);
  });

  selectContext(active);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
