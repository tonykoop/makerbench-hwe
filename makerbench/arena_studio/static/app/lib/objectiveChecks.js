// The ONE way Studio reads a recorded objective. Wall thickness ("min_wall") was removed from
// arena scoring on 2026-10-16; older recorded results may still carry it, so screens never read a
// stored pass rate or the raw checks directly: they go through these helpers (a guard test fails
// on any other read in the Studio JavaScript).

export const RETIRED_CHECKS = ["min_wall"];

const isNumber = (value) => typeof value === "number" && Number.isFinite(value);

// The recorded checks without any retired one; works on {name: value} maps.
export function retainedChecks(checks) {
  if (!checks || typeof checks !== "object") return {};
  return Object.fromEntries(Object.entries(checks).filter(([name]) => !RETIRED_CHECKS.includes(name)));
}

// The pass rate of a recorded block, rescored from its retained checks (numeric, 0..1);
// a block without them keeps its stored number, else null.
export function objectiveRate(block) {
  const checks = retainedChecks(block?.sub_scores || block?.gates);
  const values = Object.values(checks);
  if (values.length && values.every((value) => isNumber(value) && value >= 0 && value <= 1)) {
    return values.reduce((sum, value) => sum + value, 0) / values.length;
  }
  const stored = block?.objective_pass_rate;
  return isNumber(stored) ? stored : null;
}
