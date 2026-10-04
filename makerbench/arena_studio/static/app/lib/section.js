// Section plane for the 3D orbit viewer (#973).
//
// Pure helpers (axis/offset -> plane) plus a narrow bridge into the unchanged,
// locally vendored model-viewer bundle, like ./wireframe.js. model-viewer has
// no clipping API. Its element keeps the shared Three renderer and its scene
// under the "renderer" and "scene" symbols; Three clips any material whose
// `clippingPlanes` is set once the renderer enables local clipping. The vendor
// hash and the real-browser pixel test guard this boundary.

export const SECTION_AXES = ["x", "y", "z"];
export const SECTION_OFF = { axis: "", offset: 50, flip: false };

const UNIT = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const DOUBLE_SIDE = 2; // THREE.DoubleSide: show the inside of a cut wall.

export function sectionActive(section) {
  return Boolean(section && SECTION_AXES.includes(section.axis));
}

export function clampOffset(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 50;
  return Math.min(100, Math.max(0, Math.round(number)));
}

// Three keeps the side where dot(normal, p) + constant >= 0. By default the
// plane removes everything above `offset` percent of the model's extent along
// the axis, so the camera looks into the cut; `flip` keeps the other side.
// A tiny margin past each end lets 0% and 100% show nothing / the whole model.
export function sectionPlane(box, section) {
  if (!sectionActive(section)) return null;
  const index = SECTION_AXES.indexOf(section.axis);
  const lo = box.min[index];
  const hi = box.max[index];
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi < lo) return null;
  const margin = Math.max((hi - lo) * 1e-4, 1e-6);
  const at = lo - margin + ((hi - lo + 2 * margin) * clampOffset(section.offset)) / 100;
  const sign = section.flip ? 1 : -1;
  const [x, y, z] = UNIT[section.axis].map((component) => component * sign || 0);
  return { normal: { x, y, z }, constant: -sign * at, position: at - lo, extent: hi - lo };
}

// Carry a plane from the model's own frame into world space. `elements` is a
// column-major 4x4 (Three's Matrix4.elements). Normals transform by the
// inverse transpose of the linear part, so non-uniform scale stays correct.
export function transformPlane(local, elements) {
  const e = elements;
  const a = [[e[0], e[4], e[8]], [e[1], e[5], e[9]], [e[2], e[6], e[10]]];
  const c = [
    [a[1][1] * a[2][2] - a[1][2] * a[2][1], a[1][2] * a[2][0] - a[1][0] * a[2][2], a[1][0] * a[2][1] - a[1][1] * a[2][0]],
    [a[0][2] * a[2][1] - a[0][1] * a[2][2], a[0][0] * a[2][2] - a[0][2] * a[2][0], a[0][1] * a[2][0] - a[0][0] * a[2][1]],
    [a[0][1] * a[1][2] - a[0][2] * a[1][1], a[0][2] * a[1][0] - a[0][0] * a[1][2], a[0][0] * a[1][1] - a[0][1] * a[1][0]],
  ];
  const det = a[0][0] * c[0][0] + a[0][1] * c[0][1] + a[0][2] * c[0][2];
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null;
  const n = [local.normal.x, local.normal.y, local.normal.z];
  const point = n.map((component) => -local.constant * component);
  const world = a.map((row, i) => row[0] * point[0] + row[1] * point[1] + row[2] * point[2] + e[12 + i]);
  const m = c.map((row) => (row[0] * n[0] + row[1] * n[1] + row[2] * n[2]) / det);
  const length = Math.hypot(m[0], m[1], m[2]);
  const [x, y, z] = m.map((component) => component / length + 0);
  return { normal: { x, y, z }, constant: -(x * world[0] + y * world[1] + z * world[2]) };
}

function symbolValue(object, description) {
  for (let current = object; current; current = Object.getPrototypeOf(current)) {
    const key = Object.getOwnPropertySymbols(current).find((symbol) => symbol.description === description);
    if (key) return object[key];
  }
  return undefined;
}

function boxArrays(box) {
  return { min: [box.min.x, box.min.y, box.min.z], max: [box.max.x, box.max.y, box.max.z] };
}

// `element` is a loaded <model-viewer>. Throws before changing anything when
// the pinned bridge is missing, so the caller can fall back to the turntable.
//
// The plane lives in the model's own frame (#985 review): auto-rotate and
// framing move the model in world space without a camera-change event, so
// each mesh re-projects the plane through the model's current world matrix
// just before it draws. The cut turns with the model and never drifts.
export function sectionController(element) {
  const renderer = symbolValue(element, "renderer")?.threeRenderer;
  const scene = symbolValue(element, "scene");
  const model = scene?.model;
  const Box3 = scene?.boundingBox?.constructor;
  const Matrix4 = model?.matrixWorld?.constructor;
  if (!renderer || typeof renderer.localClippingEnabled !== "boolean" || !model?.traverse
    || typeof Box3 !== "function" || typeof scene.queueRender !== "function"
    || typeof Matrix4 !== "function" || typeof model.updateWorldMatrix !== "function"
    || model.matrixWorld.elements?.length !== 16) {
    throw new Error("The pinned viewer's clipping bridge is unavailable.");
  }
  const materials = new Map();
  const meshes = [];
  model.traverse((node) => {
    if (!node.isMesh) return;
    if (!node.geometry || typeof node.geometry.computeBoundingBox !== "function") {
      throw new Error("The viewer mesh has no measurable geometry.");
    }
    meshes.push(node);
    for (const material of [].concat(node.material || [])) {
      if (!material?.isMaterial || !("clippingPlanes" in material)) {
        throw new Error("The viewer material does not support clipping.");
      }
      materials.set(material, { planes: material.clippingPlanes, side: material.side, shadows: material.clipShadows });
    }
  });
  if (!materials.size) throw new Error("The model has no clippable materials.");

  // One plane object, shared by every material and updated in place. Three
  // copies its `normal` and `constant` into view space for each draw.
  const plane = { normal: { x: 0, y: 0, z: -1 }, constant: 0 };
  let local = null;
  let current = SECTION_OFF;
  let state = null;
  const hooks = new Map();

  // The model's extent in its own frame, so the slider means the same
  // physical cut whatever the turntable angle.
  const localBox = () => {
    model.updateWorldMatrix(true, true);
    const inverse = new Matrix4().copy(model.matrixWorld).invert();
    const box = new Box3().makeEmpty();
    for (const mesh of meshes) {
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      const relative = new Matrix4().multiplyMatrices(inverse, mesh.matrixWorld);
      box.union(new Box3().copy(mesh.geometry.boundingBox).applyMatrix4(relative));
    }
    return box.isEmpty() ? null : boxArrays(box);
  };

  const sync = () => {
    if (!local) return;
    const world = transformPlane(local, model.matrixWorld.elements);
    if (!world) return;
    plane.normal = world.normal;
    plane.constant = world.constant;
  };

  const hook = (on) => {
    for (const mesh of meshes) {
      if (on && !hooks.has(mesh)) {
        const original = mesh.onBeforeRender;
        hooks.set(mesh, original);
        mesh.onBeforeRender = function onBeforeRender(...args) {
          sync();
          return original?.apply(this, args);
        };
      } else if (!on && hooks.has(mesh)) {
        mesh.onBeforeRender = hooks.get(mesh);
        hooks.delete(mesh);
      }
    }
  };

  const apply = () => {
    const box = localBox();
    const next = box && sectionPlane(box, current);
    if (!next) return;
    local = { normal: next.normal, constant: next.constant };
    sync();
    state = next;
  };

  return {
    refresh() {
      if (!sectionActive(current)) return;
      sync();
      scene.queueRender();
    },
    set(section) {
      const wasActive = sectionActive(current);
      current = { ...SECTION_OFF, ...section, offset: clampOffset(section?.offset) };
      const active = sectionActive(current);
      if (active) {
        renderer.localClippingEnabled = true;
        apply();
      } else {
        state = null;
        local = null;
      }
      if (active !== wasActive) {
        hook(active);
        for (const [material, original] of materials) {
          material.clippingPlanes = active ? [plane] : original.planes;
          material.side = active ? DOUBLE_SIDE : original.side;
          material.clipShadows = active ? true : original.shadows;
          material.needsUpdate = true;
        }
      }
      scene.queueRender();
      return state;
    },
    restore() {
      this.set(SECTION_OFF);
    },
  };
}
