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
export function sectionController(element) {
  const renderer = symbolValue(element, "renderer")?.threeRenderer;
  const scene = symbolValue(element, "scene");
  const model = scene?.model;
  const Box3 = scene?.boundingBox?.constructor;
  if (!renderer || typeof renderer.localClippingEnabled !== "boolean" || !model?.traverse
    || typeof Box3 !== "function" || typeof scene.queueRender !== "function") {
    throw new Error("The pinned viewer's clipping bridge is unavailable.");
  }
  const materials = new Map();
  model.traverse((node) => {
    if (!node.isMesh) return;
    for (const material of [].concat(node.material || [])) {
      if (!material?.isMaterial || !("clippingPlanes" in material)) {
        throw new Error("The viewer material does not support clipping.");
      }
      materials.set(material, { planes: material.clippingPlanes, side: material.side, shadows: material.clipShadows });
    }
  });
  if (!materials.size) throw new Error("The model has no clippable materials.");

  // One plane object, shared by every material and updated in place. Three
  // copies its `normal` and `constant` into view space on every frame.
  const plane = { normal: { x: 0, y: 0, z: -1 }, constant: 0 };
  let current = SECTION_OFF;
  let state = null;

  const apply = () => {
    if (!sectionActive(current)) return;
    const next = sectionPlane(boxArrays(new Box3().setFromObject(model)), current);
    if (!next) return;
    plane.normal = next.normal;
    plane.constant = next.constant;
    state = next;
  };

  return {
    // Re-run on camera changes: model-viewer can move the model in world space.
    refresh() {
      if (!sectionActive(current)) return;
      apply();
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
      }
      if (active !== wasActive) {
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
