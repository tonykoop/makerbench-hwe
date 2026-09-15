import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyBody,
  changedParams,
  envelopeHint,
  formatValue,
  groupParameters,
  modelSummary,
  parseInput,
  rangeText,
  stateText,
  unitText,
  valuesEqual,
} from "../../makerbench/arena_studio/static/app/lib/params.js";

const number = (name, value, extra = {}) => ({
  name, kind: "number", value, raw: String(value), editable: true, state: "editable", notes: [], ...extra,
});

const MODEL = {
  backend: "openscad",
  parameters: [
    number("w_mm", 10, { group: "Body", unit: "mm", notes: ["range not declared"] }),
    number("h_mm", 12, { group: "Body", unit: "mm", range: { min: 5, max: 20, step: null } }),
    number("depth", 4, { group: "Body", notes: ["range not declared", "unit unknown"] }),
    { name: "derived_mm", kind: "derived", value: null, raw: "w_mm * 2", editable: false, state: "derived", group: "Body", notes: ["derived, edit it in the code"] },
    { name: "dup", kind: "number", value: 2, raw: "2", editable: false, state: "reassigned", notes: ["assigned more than once, edit it in the code"] },
    { name: "hollow", kind: "bool", value: false, raw: "false", editable: true, state: "editable", notes: [] },
    { name: "label", kind: "string", value: "a", raw: '"a"', editable: true, state: "editable", options: ["a", "b", "c"], notes: [] },
    { name: "scale_v", kind: "vector", value: [1, 2, 3], raw: "[1, 2, 3]", editable: true, state: "editable", notes: ["unit unknown"] },
    number("$fn", 24, { state: "special", notes: ["range not declared", "unit unknown"] }),
  ],
  limitations: [],
};

test("groups follow the source order and specials land under Advanced", () => {
  const groups = groupParameters(MODEL);
  assert.deepEqual(groups.map((g) => g.name), ["Body", "Parameters", "Advanced"]);
  assert.deepEqual(groups[0].params.map((p) => p.name), ["w_mm", "h_mm", "depth", "derived_mm"]);
  assert.deepEqual(groups[2].params.map((p) => p.name), ["$fn"]);
  assert.deepEqual(groupParameters(null), []);
});

test("unit, state and range text are honest and never guessed", () => {
  assert.equal(unitText(MODEL.parameters[0]), "mm");
  assert.equal(unitText(MODEL.parameters[2]), "unit unknown");
  assert.equal(stateText(MODEL.parameters[2]), "range not declared; unit unknown");
  assert.equal(stateText({ state: "derived", notes: [] }), "derived, edit it in the code");
  assert.equal(stateText({ state: "reassigned", notes: [] }), "assigned more than once, edit it in the code");
  assert.equal(rangeText(MODEL.parameters[1]), "5 to 20");
  assert.equal(rangeText({ range: { min: 0, max: 1, step: 0.5 } }), "0 to 1, step 0.5");
  assert.equal(rangeText(MODEL.parameters[6]), "one of a, b, c");
  assert.equal(rangeText(MODEL.parameters[0]), "");
});

test("envelope shows as context only next to mm numbers", () => {
  assert.equal(envelopeHint(MODEL.parameters[0], [100, 100, 100]), "Envelope 100 × 100 × 100 mm");
  assert.equal(envelopeHint(MODEL.parameters[2], [100, 100, 100]), "");
  assert.equal(envelopeHint(MODEL.parameters[0], null), "");
  assert.equal(envelopeHint(MODEL.parameters[7], [1, 2, 3]), "");
});

test("number input is refused when non-finite, out of range or off the option list", () => {
  assert.deepEqual(parseInput(MODEL.parameters[0], "12.5"), { ok: true, value: 12.5 });
  assert.deepEqual(parseInput(MODEL.parameters[0], "1e3"), { ok: true, value: 1000 });
  assert.equal(parseInput(MODEL.parameters[0], "abc").ok, false);
  assert.equal(parseInput(MODEL.parameters[0], "").ok, false);
  assert.equal(parseInput(MODEL.parameters[0], "Infinity").ok, false);
  assert.equal(parseInput(MODEL.parameters[0], "NaN").ok, false);
  assert.match(parseInput(MODEL.parameters[1], "21").error, /between 5 and 20/);
  assert.match(parseInput(MODEL.parameters[1], "4.99").error, /between 5 and 20/);
  assert.deepEqual(parseInput(MODEL.parameters[1], "20"), { ok: true, value: 20 });
  assert.match(parseInput({ ...MODEL.parameters[0], options: [1, 2] }, "3").error, /one of 1, 2/);
});

test("bool, string and vector inputs keep their kind and length", () => {
  assert.deepEqual(parseInput(MODEL.parameters[5], true), { ok: true, value: true });
  assert.equal(parseInput(MODEL.parameters[5], "true").ok, false);
  assert.deepEqual(parseInput(MODEL.parameters[6], "b"), { ok: true, value: "b" });
  assert.match(parseInput(MODEL.parameters[6], "z").error, /one of a, b, c/);
  assert.match(parseInput({ ...MODEL.parameters[6], options: null }, "two\nlines").error, /single line/);
  assert.deepEqual(parseInput(MODEL.parameters[7], ["1", "2.5", "-3"]), { ok: true, value: [1, 2.5, -3] });
  assert.match(parseInput(MODEL.parameters[7], ["1", "2"]).error, /length of 3/);
  assert.match(parseInput(MODEL.parameters[7], ["1", "x", "3"]).error, /finite numbers/);
});

test("derived and reassigned parameters are never accepted", () => {
  assert.equal(parseInput(MODEL.parameters[3], "5").ok, false);
  assert.equal(parseInput(MODEL.parameters[4], "5").ok, false);
  assert.equal(parseInput(null, "5").ok, false);
});

test("changes list only real differences, in source order, and apply sends only those", () => {
  const values = { $fn: 24, w_mm: 10, h_mm: 15, label: "a", scale_v: [1, 2, 3], hollow: true };
  assert.deepEqual(changedParams(MODEL, values), [
    { name: "h_mm", before: 12, after: 15 },
    { name: "hollow", before: false, after: true },
  ]);
  assert.deepEqual(applyBody(MODEL, values), { h_mm: 15, hollow: true });
  assert.deepEqual(changedParams(MODEL, {}), []);
  assert.ok(valuesEqual([1, 2], [1, 2]) && !valuesEqual([1, 2], [1, 3]) && !valuesEqual(1, true) && valuesEqual(1.0, 1));
});

test("value formatting and the model summary", () => {
  assert.equal(formatValue([1, 2.5, 3]), "[1, 2.5, 3]");
  assert.equal(formatValue(true), "true");
  assert.equal(formatValue(null), "");
  assert.equal(modelSummary(MODEL), "9 parameters, 7 editable.");
  assert.match(modelSummary({ parameters: [] }), /No parameters found/);
});
