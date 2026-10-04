// Side-by-side matchup compare (#974). Pure helpers: no DOM, no fetch;
// unit-tested under node in tests/js/matchup_compare_lib.test.mjs.

// Gate order as the run summary records it; declared extras follow.
export const CHECK_ORDER = [
  "renders",
  "watertight",
  "nonzero_volume",
  "body_count",
  "fits_envelope",
  "min_wall",
  "topology",
  "interfaces",
];

const STATE_TEXT = { pass: "Pass", fail: "Fail", unknown: "Not recorded", absent: "Not checked" };

export function checkState(gates, name) {
  if (!gates || !Object.prototype.hasOwnProperty.call(gates, name)) return "absent";
  const value = gates[name];
  if (typeof value !== "number" || !Number.isFinite(value)) return "unknown";
  return value >= 1 ? "pass" : "fail";
}

// One row per check, one cell per entrant, in the same order for everyone,
// so a chip for "min_wall" always sits beside the other entrant's "min_wall".
export function checkGrid(trials) {
  const list = trials || [];
  const seen = new Set(list.flatMap((trial) => Object.keys(trial?.gates || {})));
  const extras = [...seen].filter((name) => !CHECK_ORDER.includes(name)).sort();
  const names = [...CHECK_ORDER.filter((name) => seen.has(name)), ...extras];
  return names.map((check) => {
    const cells = list.map((trial) => {
      const state = checkState(trial?.gates, check);
      return { state, text: STATE_TEXT[state] };
    });
    const decided = cells.filter((cell) => cell.state === "pass" || cell.state === "fail");
    const differs = decided.length > 1 && decided.some((cell) => cell.state !== decided[0].state);
    return { check, label: check.replaceAll("_", " "), cells, differs };
  });
}

// Choose up to `limit` trials; picking another replaces the oldest choice.
export function toggleSelection(selected, id, limit = 2) {
  if (selected.includes(id)) return selected.filter((item) => item !== id);
  const next = [...selected, id];
  return next.length > limit ? next.slice(next.length - limit) : next;
}

export function canCompare3d(trials, webgl) {
  return Boolean(webgl) && (trials || []).length === 2 && trials.every((trial) => Boolean(trial?.mesh_url));
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// model-viewer camera attribute strings from its getters' values.
export function cameraStrings(orbit, target, fieldOfViewDeg) {
  if (!orbit || ![orbit.theta, orbit.phi, orbit.radius].every(finite)) return null;
  if (!target || ![target.x, target.y, target.z].every(finite) || !finite(fieldOfViewDeg)) return null;
  return {
    orbit: `${orbit.theta}rad ${orbit.phi}rad ${orbit.radius}m`,
    target: `${target.x}m ${target.y}m ${target.z}m`,
    fov: `${fieldOfViewDeg}deg`,
  };
}

// The view that frames the larger model leads the first sync, so both fit.
export function leaderIndex(radii) {
  let best = -1;
  (radii || []).forEach((radius, index) => {
    if (finite(radius) && (best < 0 || radius > radii[best])) best = index;
  });
  return best;
}

export function camerasMatch(a, b, tolerance = 1e-6) {
  if (!a || !b) return false;
  const close = (x, y) => Math.abs(x - y) <= tolerance * Math.max(1, Math.abs(x), Math.abs(y));
  return close(a.theta, b.theta) && close(a.phi, b.phi) && close(a.radius, b.radius)
    && close(a.x, b.x) && close(a.y, b.y) && close(a.z, b.z) && close(a.fov, b.fov);
}

// model-viewer widens each view's vertical field of view by its own model's
// framing: tan(v/2) = tan(f/2) * max(1, idealAspect / aspect). To give the
// follower the driver's real vertical FOV `v`, set its field-of-view to `f`.
export function fieldOfViewFor(verticalDeg, idealAspect, aspect) {
  if (![verticalDeg, idealAspect, aspect].every(finite) || aspect <= 0 || verticalDeg <= 0) return null;
  const widen = Math.max(1, idealAspect / aspect);
  return (2 * Math.atan(Math.tan((verticalDeg / 2) * (Math.PI / 180)) / widen) * 180) / Math.PI;
}
