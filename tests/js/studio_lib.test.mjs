// Unit tests for the Studio's framework-free browser modules.
// Run: node --test tests/js/   (wrapped by tests/test_arena_studio_frontend.py)

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
  api,
  ApiError,
  errorMessage,
  isLocalPath,
  SERVER_UNREACHABLE,
} from "../../makerbench/arena_studio/static/app/lib/api.js";
import { plural, formatWhen, UNKNOWN_GRADE_COUNT } from "../../makerbench/arena_studio/static/app/lib/format.js";
import {
  DEFAULT_VOTER,
  loadVoter,
  normalizeVoter,
  saveVoter,
} from "../../makerbench/arena_studio/static/app/lib/prefs.js";
import { buildHash, parseHash } from "../../makerbench/arena_studio/static/app/lib/route.js";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function fakeResponse(status, body, type = "application/json") {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => type },
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  };
}

test("routes default to the runs screen", () => {
  assert.deepEqual(parseHash(""), { screen: "runs", args: [], query: {} });
  assert.deepEqual(parseHash("#/"), { screen: "runs", args: [], query: {} });
});

test("routes round-trip ids with reserved characters", () => {
  const hash = buildHash("vote", ["round 10/a?b"], { skip: 2, empty: "" });
  assert.equal(hash, "#/vote/round%2010%2Fa%3Fb?skip=2");
  assert.deepEqual(parseHash(hash), { screen: "vote", args: ["round 10/a?b"], query: { skip: "2" } });
});

test("routes survive malformed percent-encoding", () => {
  assert.deepEqual(parseHash("#/runs/%E0%A4%A").args, ["%E0%A4%A"]);
});

test("error messages prefer the server's detail", () => {
  assert.equal(errorMessage(404, JSON.stringify({ detail: "Run 'x' not found" })), "Run 'x' not found");
  assert.equal(errorMessage(403, JSON.stringify({ detail: "live arena runs require --allow-live" })),
    "live arena runs require --allow-live (HTTP 403)");
  assert.equal(errorMessage(422, JSON.stringify({ detail: [{ msg: "field required" }, { msg: "bad" }] })),
    "field required; bad (HTTP 422)");
  assert.equal(errorMessage(403, "Cross-origin POST refused"), "Cross-origin POST refused (HTTP 403)");
  assert.equal(errorMessage(500, ""), "The server returned HTTP 500.");
  assert.equal(errorMessage(0, ""), SERVER_UNREACHABLE);
});

test("only same-origin absolute paths are requestable", () => {
  assert.equal(isLocalPath("/api/runs"), true);
  assert.equal(isLocalPath("//attacker.example/api"), false);
  assert.equal(isLocalPath("https://attacker.example/api"), false);
  assert.equal(isLocalPath("api/runs"), false);
});

test("api refuses a non-local path without fetching", async () => {
  let called = false;
  globalThis.fetch = async () => {
    called = true;
  };
  await assert.rejects(api("//attacker.example/x"), ApiError);
  assert.equal(called, false);
});

test("api treats a non-2xx response as an error, never data", async () => {
  globalThis.fetch = async () => fakeResponse(404, { detail: "Run 'gone' not found" });
  await assert.rejects(api("/api/runs/gone/summary"), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 404);
    assert.equal(error.message, "Run 'gone' not found");
    return true;
  });
});

test("api parses JSON and sends JSON bodies same-origin", async () => {
  let seen;
  globalThis.fetch = async (path, init) => {
    seen = { path, init };
    return fakeResponse(200, { success: true });
  };
  assert.deepEqual(await api("/api/runs/r/vote", { method: "POST", body: { winner: "left" } }), { success: true });
  assert.equal(seen.init.credentials, "same-origin");
  assert.equal(seen.init.headers["Content-Type"], "application/json");
  assert.equal(seen.init.body, JSON.stringify({ winner: "left" }));
});

test("api reports an unreachable server plainly", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(api("/api/health"), (error) => error.status === 0 && error.message === SERVER_UNREACHABLE);
});

test("api lets aborts through unchanged", async () => {
  globalThis.fetch = async () => {
    const error = new Error("aborted");
    error.name = "AbortError";
    throw error;
  };
  await assert.rejects(api("/api/health"), (error) => error.name === "AbortError");
});

test("voter preference falls back when storage is missing or throws", () => {
  const memory = new Map();
  const storage = { getItem: (k) => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v) };
  assert.equal(loadVoter(storage), DEFAULT_VOTER);
  saveVoter("  alice  ", storage);
  assert.equal(loadVoter(storage), "alice");
  const broken = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  };
  assert.equal(loadVoter(broken), DEFAULT_VOTER);
  assert.doesNotThrow(() => saveVoter("bob", broken));
  assert.equal(loadVoter(undefined), DEFAULT_VOTER);
  assert.equal(normalizeVoter("   "), DEFAULT_VOTER);
});

test("formatting", () => {
  assert.equal(plural(1, "vote"), "1 vote");
  assert.equal(plural(3, "entry", "entries"), "3 entries");
  assert.equal(formatWhen(null), "Not recorded");
  assert.equal(formatWhen("not-a-date"), "not-a-date");
  assert.equal(UNKNOWN_GRADE_COUNT, "Unknown");
});
