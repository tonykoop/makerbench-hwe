// viewer.js — Inspect-a-Run 3D viewer for the HF Space (#98).
//
// Orbit/zoom/wireframe over the *submitted* artifact only. Renders .stl and
// .glb/.gltf directly; STEP B-Reps are tessellated to .glb server-side
// (app.py + geometry_convert.py) before they reach this module, so the loader
// here only ever sees a mesh. three.js is pinned via the page importmap.
//
// Adapted from the GitHub Pages viewer (site/assets/viewer.js). Degrades
// gracefully: any failure leaves the static note in place.

import * as THREE from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const ACCENT = 0xd6562b;
const EDGE = 0xc9ced6;

function frame(object, camera, controls) {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  object.position.sub(center); // recenter at origin
  const radius = Math.max(size.x, size.y, size.z, 1e-3);
  camera.position.set(radius * 1.6, radius * 1.2, radius * 1.8);
  camera.near = radius / 100;
  camera.far = radius * 100;
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.update();
}

export async function mountViewer(host, url, kind, wireBtn) {
  const note = host.querySelector(".viewer-note");
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (err) {
    if (note) note.textContent = "WebGL unavailable — use the download link.";
    return;
  }
  const w = host.clientWidth || 480;
  const h = host.clientHeight || 340;
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(w, h);
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 5000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x555555, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 0.7);
  key.position.set(1, 1.5, 1);
  scene.add(key);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  let mesh;
  function addMesh(geometryOrGroup, isGroup) {
    const material = new THREE.MeshStandardMaterial({
      color: ACCENT, metalness: 0.1, roughness: 0.7, flatShading: true,
    });
    if (isGroup) {
      mesh = geometryOrGroup;
      mesh.traverse((c) => { if (c.isMesh) c.material = material; });
    } else {
      geometryOrGroup.computeVertexNormals();
      mesh = new THREE.Mesh(geometryOrGroup, material);
    }
    scene.add(mesh);
    frame(mesh, camera, controls);
    if (note) note.remove();
  }

  try {
    if (kind === "stl") {
      const geo = await new STLLoader().loadAsync(url);
      addMesh(geo, false);
    } else {
      const gltf = await new GLTFLoader().loadAsync(url);
      addMesh(gltf.scene, true);
    }
  } catch (err) {
    if (note) note.textContent = "Could not load the artifact mesh.";
    return;
  }

  let wire = false;
  if (wireBtn) {
    wireBtn.addEventListener("click", () => {
      wire = !wire;
      mesh.traverse
        ? mesh.traverse((c) => { if (c.isMesh) c.material.wireframe = wire; })
        : (mesh.material.wireframe = wire);
      wireBtn.classList.toggle("active", wire);
    });
  }

  (function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  })();

  window.addEventListener("resize", () => {
    const nw = host.clientWidth, nh = host.clientHeight;
    if (!nw || !nh) return;
    camera.aspect = nw / nh;
    camera.updateProjectionMatrix();
    renderer.setSize(nw, nh);
  });
}
