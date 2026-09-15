// Per-browser conveniences only. Storage can be missing or throw (private
// windows, blocked site data), so every access falls back quietly.

const VOTER_KEY = "arena-studio.voter";
export const DEFAULT_VOTER = "tony";

export function normalizeVoter(value) {
  const trimmed = String(value ?? "").trim();
  return trimmed || DEFAULT_VOTER;
}

export function loadVoter(storage = globalThis.localStorage) {
  try {
    return normalizeVoter(storage?.getItem(VOTER_KEY));
  } catch {
    return DEFAULT_VOTER;
  }
}

export function saveVoter(voter, storage = globalThis.localStorage) {
  try {
    storage?.setItem(VOTER_KEY, normalizeVoter(voter));
  } catch {
    // Not persisted; the choice still applies for this session.
  }
}
