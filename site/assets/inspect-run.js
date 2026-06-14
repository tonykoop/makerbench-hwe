// Inspect-a-Run 3D viewer (#107).
//
// The deep-dive viewer embedded by the per-run explorer.html (#104) and the
// dual-league dashboard (#98). It renders the *submitted* artifact and overlays
// the grader verdict spatially:
//
//   * Solid          — the part as designed.
//   * Wall-thickness — a heat-map shader. Per-vertex thickness is estimated by
//                      casting a ray inward (along -normal) and measuring the
//                      distance to the far wall, then ramped blue (thick) → red
//                      (thin). Vertices under the DFM `data-min-wall` threshold
//                      are clamped hot so thin walls read at a glance.
//   * Interference   — translucent markers at the interference zones the grader
//                      reported (`data-interference`). When the verdict flags
//                      interference but no zone geometry was supplied, the mode
//                      says so rather than inventing a location.
//   * Wireframe      — edges only.
//
// Like site/assets/viewer.js, geometry is loaded ONLY from the artifact the run
// submitted (a sibling file in the run dir); no oracle geometry is ever touched.
// three.js is pinned via the importmap injected alongside this module, and every
// failure mode degrades to the server-rendered verdict/trace panels that sit
// next to the canvas — the page stays honest with WebGL off, a STEP artifact
// that can't be tessellated in-browser, or an oversized mesh.

import * as THREE from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// Above this vertex count the O(V·tris) thickness raycast gets too slow for a
// page render, so the heat-map mode is disabled (solid still works). The
// parametric parts MakerBench grades are naturally well under this.
const HEATMAP_VERTEX_BUDGET = 30000;

function themeColors() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  return {
    bg: dark ? 0x15181d : 0xf6f7f9,
    mesh: dark ? 0xc9ced6 : 0x8a8f98, // neutral so heat-map colors read true
    edge: dark ? 0x3a3f47 : 0xc9ced6,
    zone: 0xd23b2a,
  };
}

function note(host, message) {
  const el = document.createElement("div");
  el.className = "mb-inspect-note";
  el.textContent = message;
  host.appendChild(el);
}

function parseJSONAttr(host, name) {
  const raw = host.getAttribute(name);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

// Blue (thick) → cyan → yellow → red (thin). `t` in [0,1], 1 == thinnest.
function heatColor(t) {
  const x = Math.max(0, Math.min(1, t));
  const stops = [
    [0.0, [0.16, 0.30, 0.70]],
    [0.4, [0.10, 0.66, 0.62]],
    [0.7, [0.95, 0.78, 0.18]],
    [1.0, [0.82, 0.18, 0.12]],
  ];
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [a0, c0] = stops[i - 1];
      const [a1, c1] = stops[i];
      const f = (x - a0) / (a1 - a0 || 1);
      return [
        c0[0] + (c1[0] - c0[0]) * f,
        c0[1] + (c1[1] - c0[1]) * f,
        c0[2] + (c1[2] - c0[2]) * f,
      ];
    }
  }
  return stops[stops.length - 1][1];
}

// Estimate per-vertex wall thickness by raycasting inward against the part.
// Returns { thickness: Float32Array, min, max } or null if it can't be computed.
function computeThickness(geometry, mesh) {
  const pos = geometry.getAttribute("position");
  const nrm = geometry.getAttribute("normal");
  if (!pos || !nrm) return null;
  const n = pos.count;
  if (n > HEATMAP_VERTEX_BUDGET) return null;

  const raycaster = new THREE.Raycaster();
  raycaster.far = Infinity;
  const thickness = new Float32Array(n);
  const origin = new THREE.Vector3();
  const dir = new THREE.Vector3();
  geometry.computeBoundingSphere();
  const eps = (geometry.boundingSphere?.radius || 1) * 1e-3;

  let min = Infinity;
  let max = 0;
  for (let i = 0; i < n; i++) {
    origin.set(pos.getX(i), pos.getY(i), pos.getZ(i));
    dir.set(nrm.getX(i), nrm.getY(i), nrm.getZ(i)).normalize().negate();
    // Step slightly inside so the ray doesn't immediately re-hit its own vertex.
    origin.addScaledVector(dir, eps);
    raycaster.set(origin, dir);
    const hits = raycaster.intersectObject(mesh, false);
    let t = 0;
    for (const h of hits) {
      if (h.distance > eps) {
        t = h.distance + eps;
        break;
      }
    }
    thickness[i] = t;
    if (t > 0) {
      if (t < min) min = t;
      if (t > max) max = t;
    }
  }
  if (!isFinite(min) || max <= 0) return null;
  return { thickness, min, max };
}

// Paint a vertex-color buffer from the thickness field. Walls under
// `threshold` mm (when known) are pinned hot regardless of the part's range.
function applyHeatColors(geometry, field, threshold) {
  const n = field.thickness.length;
  const colors = new Float32Array(n * 3);
  const span = field.max - field.min || 1;
  for (let i = 0; i < n; i++) {
    const t = field.thickness[i];
    let hot; // 1 == thinnest/hottest
    if (t <= 0) {
      hot = 0.5; // unmeasured (grazing ray) — neutral
    } else if (threshold && t <= threshold) {
      hot = 1;
    } else {
      hot = 1 - (t - field.min) / span;
    }
    const [r, g, b] = heatColor(hot);
    colors[i * 3] = r;
    colors[i * 3 + 1] = g;
    colors[i * 3 + 2] = b;
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
}

function frameCamera(camera, controls, geometry) {
  geometry.computeBoundingSphere();
  const r = geometry.boundingSphere ? geometry.boundingSphere.radius : 50;
  const dist = r / Math.sin((camera.fov * Math.PI) / 360);
  camera.position.set(dist * 0.7, dist * 0.55, dist * 0.8);
  camera.near = Math.max(r / 1000, 0.01);
  camera.far = dist * 10;
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.maxDistance = dist * 6;
  controls.update();
  return r;
}

function loadGeometry(url, ext, onMesh, onError) {
  if (ext === "glb" || ext === "gltf") {
    new GLTFLoader().load(
      url,
      (gltf) => {
        let found = null;
        gltf.scene.traverse((o) => {
          if (!found && o.isMesh) found = o.geometry;
        });
        if (found) onMesh(found);
        else onError("No mesh in the glTF artifact.");
      },
      undefined,
      () => onError("Could not load the submitted glTF artifact.")
    );
    return;
  }
  new STLLoader().load(
    url,
    (geometry) => onMesh(geometry),
    undefined,
    () => onError("Could not load the submitted mesh.")
  );
}

function initInspect(host) {
  const artifact = host.getAttribute("data-artifact");
  const canvasBox = host.querySelector(".mb-inspect-canvas");
  if (!canvasBox) return;
  if (!artifact) {
    note(canvasBox, "No 3D artifact submitted for this run.");
    return;
  }
  const ext = artifact.split(".").pop().toLowerCase();
  if (ext === "step" || ext === "stp") {
    note(
      canvasBox,
      "STEP artifacts can't be tessellated in-browser — see the verdict and " +
        "trace panels, or download the artifact to inspect it in CAD."
    );
    return;
  }

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (e) {
    note(canvasBox, "3D viewer unavailable (no WebGL in this browser).");
    return;
  }

  const colors = themeColors();
  const minWallAttr = parseFloat(host.getAttribute("data-min-wall"));
  const minWall = isFinite(minWallAttr) ? minWallAttr : null;
  const zones = parseJSONAttr(host, "data-interference") || [];

  const width = canvasBox.clientWidth || 480;
  const height = canvasBox.clientHeight || 380;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height);
  canvasBox.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(colors.bg);
  const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100000);
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const key = new THREE.DirectionalLight(0xffffff, 0.85);
  key.position.set(1, 1.4, 1.1);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.3);
  fill.position.set(-1, -0.6, -0.8);
  scene.add(fill);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  const state = {
    solid: null,
    wire: null,
    zoneGroup: null,
    heatField: null,
    mode: "solid",
  };

  function setMode(mode) {
    if (!state.solid) return;
    state.mode = mode;
    const m = state.solid.material;
    // reset
    state.wire.visible = false;
    if (state.zoneGroup) state.zoneGroup.visible = false;
    m.transparent = false;
    m.opacity = 1;

    if (mode === "wireframe") {
      state.wire.visible = true;
      m.transparent = true;
      m.opacity = 0.12;
      m.vertexColors = false;
      m.color.setHex(colors.mesh);
    } else if (mode === "heatmap" && state.heatField) {
      m.vertexColors = true;
      m.color.setHex(0xffffff);
    } else if (mode === "interference") {
      m.vertexColors = false;
      m.color.setHex(colors.mesh);
      m.transparent = true;
      m.opacity = 0.45;
      if (state.zoneGroup) state.zoneGroup.visible = true;
    } else {
      m.vertexColors = false;
      m.color.setHex(colors.mesh);
    }
    m.needsUpdate = true;
    syncButtons();
  }

  function syncButtons() {
    host.querySelectorAll(".mb-inspect-mode").forEach((b) => {
      const on = b.getAttribute("data-mode") === state.mode;
      b.setAttribute("aria-pressed", String(on));
    });
  }

  function disableMode(mode, reason) {
    const btn = host.querySelector(`.mb-inspect-mode[data-mode="${mode}"]`);
    if (btn) {
      btn.disabled = true;
      btn.title = reason;
    }
  }

  function buildZones(radiusHint) {
    if (!zones.length) return;
    const group = new THREE.Group();
    for (const z of zones) {
      const c = Array.isArray(z.center) ? z.center : [0, 0, 0];
      const r = typeof z.radius === "number" ? z.radius : radiusHint * 0.12;
      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(r, 20, 16),
        new THREE.MeshBasicMaterial({
          color: colors.zone,
          transparent: true,
          opacity: 0.4,
        })
      );
      sphere.position.set(c[0], c[1], c[2]);
      group.add(sphere);
    }
    group.visible = false;
    state.zoneGroup = group;
    scene.add(group);
  }

  loadGeometry(
    artifact,
    ext,
    (geometry) => {
      geometry.computeVertexNormals();
      geometry.center();

      const material = new THREE.MeshStandardMaterial({
        color: colors.mesh,
        metalness: 0.05,
        roughness: 0.7,
      });
      state.solid = new THREE.Mesh(geometry, material);
      scene.add(state.solid);

      state.wire = new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry, 25),
        new THREE.LineBasicMaterial({ color: colors.edge })
      );
      state.wire.visible = false;
      scene.add(state.wire);

      const r = frameCamera(camera, controls, geometry);

      // Heat-map field (best-effort; large meshes opt out).
      state.heatField = computeThickness(geometry, state.solid);
      if (state.heatField) {
        applyHeatColors(geometry, state.heatField, minWall);
        renderLegend(host, state.heatField, minWall);
      } else {
        disableMode("heatmap", "Mesh too large to estimate wall thickness in-browser.");
      }

      buildZones(r);
      if (!zones.length) disableMode("interference", "No interference zones reported by the grader.");

      host.classList.add("is-ready");
      setMode("solid");
    },
    (msg) => note(canvasBox, msg)
  );

  // Mode buttons.
  host.querySelectorAll(".mb-inspect-mode").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      setMode(btn.getAttribute("data-mode"));
    });
  });

  // Verdict cross-link: clicking a verdict check jumps to the matching mode.
  const verdictPanel = host.querySelector(".mb-inspect-verdict");
  if (verdictPanel) {
    verdictPanel.addEventListener("click", (ev) => {
      const card = ev.target.closest("[data-check]");
      if (!card) return;
      const map = {
        "no-interference": "interference",
        "wall-thickness": "heatmap",
      };
      const mode = map[card.getAttribute("data-check")];
      if (mode) {
        const btn = host.querySelector(`.mb-inspect-mode[data-mode="${mode}"]`);
        if (btn && !btn.disabled) setMode(mode);
      }
    });
  }

  function resize() {
    const w = canvasBox.clientWidth || width;
    const h = canvasBox.clientHeight || height;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);

  let running = true;
  function animate() {
    if (!running) return;
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting && !running) {
          running = true;
          animate();
        } else if (!entry.isIntersecting) {
          running = false;
        }
      });
    });
    io.observe(host);
  }
}

function renderLegend(host, field, threshold) {
  const legend = host.querySelector(".mb-inspect-legend");
  if (!legend) return;
  const fmt = (v) => `${v.toFixed(2)} mm`;
  const thr = threshold ? ` · DFM min ${fmt(threshold)}` : "";
  legend.innerHTML =
    `<span class="mb-legend-bar" aria-hidden="true"></span>` +
    `<span class="mb-legend-lo">thick ${fmt(field.max)}</span>` +
    `<span class="mb-legend-hi">thin ${fmt(field.min)}${thr}</span>`;
}

function boot() {
  document.querySelectorAll(".mb-inspect").forEach(initInspect);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

// Exported for unit/headless checks; harmless in the browser.
export { heatColor, computeThickness, applyHeatColors };
