// Narrow bridge to the unchanged, locally vendored model-viewer bundle.
// Its glTF material API has no wireframe setter. The pinned scene-graph wrappers
// retain Three materials and their render-invalidation callback under these
// symbols. The vendor hash and real browser draw-mode tests guard this boundary.
function symbolValue(object, description) {
  const key = Object.getOwnPropertySymbols(object).find((symbol) => symbol.description === description);
  return key ? object[key] : undefined;
}

export function wireframeController(model) {
  const originals = new Map();
  const updates = [];
  for (const wrapper of model?.materials || []) {
    if (wrapper.isActive === false) continue;
    const materials = symbolValue(wrapper, "correlatedObjects");
    const update = symbolValue(wrapper, "onUpdate");
    if (!(materials instanceof Set) || !materials.size || typeof update !== "function") {
      throw new Error("The pinned viewer's material bridge is unavailable.");
    }
    for (const material of materials) {
      if (!material?.isMaterial || typeof material.wireframe !== "boolean") {
        throw new Error("The viewer material does not support wireframe rendering.");
      }
      originals.set(material, material.wireframe);
    }
    updates.push(() => update.call(wrapper));
  }
  if (!originals.size) throw new Error("The model has no usable materials.");
  const refresh = () => updates.forEach((update) => update());
  return {
    set(enabled) {
      for (const material of originals.keys()) {
        material.wireframe = enabled;
        material.needsUpdate = true;
      }
      refresh();
    },
    restore() {
      for (const [material, original] of originals) {
        material.wireframe = original;
        material.needsUpdate = true;
      }
      refresh();
    },
  };
}
