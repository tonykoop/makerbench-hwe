// Unit tests for the Control Room New Run form rules (#1114).
// Run: node --test tests/js/

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  blockers,
  cellReason,
  chooseInstrument,
  clampTurns,
  duration,
  freshForm,
  parseVariants,
  payload,
  togglePick,
  warnings,
} from "../../makerbench/arena_studio/static/app/lib/newrun.js";

const CATALOG = {
  instruments: [
    { id: "sambuca", name: "Sambuca", assembly: false, brief: "Boat body." },
    { id: "tongue_drum_wood_assembly", name: "Tongue drum", assembly: true, brief: "Six panels." },
  ],
  tools: [
    { id: "cadam", seat: false, cells: { fable: null, opus: null, sonnet: null, sol: null } },
    { id: "freecad", seat: false, cells: { fable: "only OpenRouter", opus: null, sonnet: null, sol: null } },
    { id: "solidworks", seat: true, cells: { fable: "only OpenRouter", opus: null, sonnet: null, sol: null } },
    { id: "fusion", seat: true, cells: { fable: "only OpenRouter", opus: null, sonnet: null, sol: null } },
  ],
};

test("presets pick runnable cells and a fitting instrument", () => {
  const matchup = freshForm(CATALOG, "matchup");
  assert.equal(matchup.instrument, "sambuca");
  assert.deepEqual(matchup.entrants, ["cadam:fable", "freecad:opus", "solidworks:sol", "fusion:sonnet"]);
  assert.equal(freshForm(CATALOG, "assembly").instrument, "tongue_drum_wood_assembly");
  assert.equal(cellReason(CATALOG, "freecad:fable"), "only OpenRouter");
  assert.equal(cellReason(CATALOG, "nope:x"), "Unknown entrant");
});

test("choosing an instrument resets its brief and references, and leaves the preset", () => {
  const form = { ...freshForm(CATALOG), references: [{ name: "a", data: "x" }] };
  const next = chooseInstrument(form, CATALOG, "tongue_drum_wood_assembly");
  assert.equal(next.brief, "Six panels.");
  assert.equal(next.deliverable, "assembly");
  assert.deepEqual(next.references, []);
  assert.equal(next.preset, null);
  assert.equal(chooseInstrument(form, CATALOG, "sambuca"), form);
});

test("picks toggle and the payload is what the server validates", () => {
  let form = freshForm(CATALOG, "sweep");
  form = togglePick(form, "cadam:sonnet");
  assert.deepEqual(form.entrants, ["freecad:opus"]);
  assert.match(blockers(form, null)[0], /at least two entrants/);
  form = togglePick(form, "fusion:opus");
  assert.deepEqual(blockers(form, null), []);
  assert.deepEqual(Object.keys(payload(form)).sort(), [
    "brief", "cap_usd", "deliverable", "entrants", "instrument", "references", "stop_early", "turns", "variants",
  ]);
});

test("small rules read plainly", () => {
  assert.deepEqual(parseVariants(" D4, A4 ,D4,, "), ["D4", "A4"]);
  assert.equal(clampTurns("12"), 8);
  assert.equal(clampTurns("0"), 1);
  assert.equal(duration(3900), "1 h 5 min");
  assert.equal(duration(null), "unknown");
  const assembly = { ...freshForm(CATALOG, "assembly"), entrants: ["cadam:opus", "solidworks:sol", "fusion:opus"] };
  const notes = warnings(assembly, CATALOG, { openrouter_complete: false, unknown_cost: ["cadam:opus"] });
  assert.equal(notes.length, 4);
  assert.match(blockers(assembly, { over_cap: true })[0], /over your \$2\.00 cap/);
});

test("library filters and search narrow the instrument list", async () => {
  const { filterInstruments, cardDetail } = await import("../../makerbench/arena_studio/static/app/lib/newrun.js");
  const list = [
    { id: "kora", slug: "kora", name: "Kora", family: "string", cad: "none", subtype: "harp-lute", variants: 3 },
    { id: "handpan", slug: "handpan", name: "Handpan", family: "idiophone", cad: "inline-glb", variants: 1 },
    { id: "erhu-packet", slug: "erhu-packet", name: "Erhu Starter", family: "string", cad: "none", variants: 1 },
  ];
  assert.deepEqual(filterInstruments(list, { cad: "none" }).map((i) => i.id), ["kora", "erhu-packet"]);
  assert.deepEqual(filterInstruments(list, { cad: "all", family: "string" }, "erhu").map((i) => i.id), ["erhu-packet"]);
  assert.deepEqual(filterInstruments(list, {}, "HARP").map((i) => i.id), ["kora"]);
  assert.equal(cardDetail({ family_label: "Strings", subtype: "harp-lute", variants: 3 }), "Strings · harp-lute · 3 variants");
});

test("variant chips toggle into a sweep", async () => {
  const { toggleVariant } = await import("../../makerbench/arena_studio/static/app/lib/newrun.js");
  let form = { variants: [], preset: "matchup" };
  form = toggleVariant(form, "TD-8");
  form = toggleVariant(form, "TD-10");
  assert.deepEqual(form.variants, ["TD-8", "TD-10"]);
  assert.equal(form.preset, null);
  assert.deepEqual(toggleVariant(form, "TD-8").variants, ["TD-10"]);
});

test("autofill fills once per instrument and never overwrites edits", async () => {
  const { applyAutofill, payload } = await import("../../makerbench/arena_studio/static/app/lib/newrun.js");
  const suggestion = { text: "Model the kora.", images: ["hero-render.png"] };
  let form = { instrument: "kora", brief: "registry", references: [], variants: [], entrants: [], turns: 5, cap_usd: 2 };
  form = applyAutofill(form, "kora", suggestion);
  assert.equal(form.brief, "Model the kora.");
  assert.deepEqual(payload(form).references, [{ repo_image: "hero-render.png" }]);
  form = { ...form, brief: "Model the kora, but taller." };
  assert.equal(applyAutofill(form, "kora", suggestion).brief, "Model the kora, but taller.");
  assert.equal(applyAutofill(form, "lyre", suggestion), form); // a stale response for another instrument
});

test("fill-the-gaps order rules", async () => {
  const { gapCandidates, moveNight, nightDate } = await import("../../makerbench/arena_studio/static/app/lib/newrun.js");
  const catalog = {
    instruments: [{ id: "a", family: "drum" }, { id: "b", family: "string" }, { id: "c", family: "drum" }],
    gaps: { without_cad: ["a", "b", "c"] },
  };
  assert.deepEqual(gapCandidates(catalog, "drum"), ["a", "c"]);
  assert.deepEqual(moveNight(["a", "b", "c"], "c", -1), ["a", "c", "b"]);
  assert.equal(nightDate("2026-10-31", 1), "2026-11-01");
  assert.equal(nightDate("bad", 1), "");
});

test("a slow first suggestion keeps a brief typed and an image added while it loaded (#1108 review)", async () => {
  const { applyAutofill, chooseInstrument, editBrief } = await import("../../makerbench/arena_studio/static/app/lib/newrun.js");
  const catalog = { instruments: [{ id: "kora", brief: "registry" }, { id: "drum", brief: "drum" }] };
  const suggestion = { text: "Model the kora.", images: ["hero-render.png"] };
  let form = chooseInstrument({ instrument: "", references: [], variants: [] }, catalog, "kora");
  form = editBrief(form, "My own kora brief.");
  form = { ...form, references: [{ name: "sketch.png", data: "data:image/png;base64,AA" }] };
  form = applyAutofill(form, "kora", suggestion);
  assert.equal(form.brief, "My own kora brief.");
  assert.deepEqual(form.references.map((r) => r.name), ["sketch.png", "hero-render.png"]);
  // Kora -> drum -> kora before any drum suggestion: kora fills again, without a stale marker.
  form = chooseInstrument(chooseInstrument(form, catalog, "drum"), catalog, "kora");
  assert.equal(form.autofill_for, null);
  assert.equal(applyAutofill(form, "kora", suggestion).brief, "Model the kora.");
});
