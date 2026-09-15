// Blind-vote anonymity, client side. The server already strips identity from a
// pair before the vote (#711, #735); the stage refuses to render any pair that
// still carries it, so a future server regression can't reach the screen.

const IDENTITY_KEYS = new Set([
  "model_id",
  "trial_id",
  "candidate_id",
  "entrant",
  "entrant_id",
  "provenance",
  "reveal",
]);

// Blind assets are per-pair aliases only: /runs/<run>/vote_pages/blind/<alias>
// or /api/morning/<job>/assets/blind/<alias>.
const BLIND_ASSET = /^\/(?:runs\/[^/]+\/vote_pages|api\/morning\/[^/]+\/assets)\/blind\/[^/]+$/;

export function isBlindAssetUrl(url) {
  return typeof url === "string" && BLIND_ASSET.test(url);
}

// Returns a count and the offending field paths, never the offending values:
// those may be the very model names being kept out of the page.
export function identityLeaks(pair) {
  const fields = [];
  const walk = (value, path) => {
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${path}[${index}]`));
    } else if (value && typeof value === "object") {
      for (const [key, item] of Object.entries(value)) {
        if (IDENTITY_KEYS.has(key)) fields.push(`${path}.${key}`);
        walk(item, `${path}.${key}`);
      }
    }
  };
  walk(pair, "pair");
  for (const side of ["left", "right"]) {
    const candidate = pair?.[side] || {};
    const urls = [candidate.render_path, candidate.model3d_path, ...(candidate.frames || [])];
    urls.forEach((url, index) => {
      if (url && !isBlindAssetUrl(url)) fields.push(`pair.${side}.asset[${index}]`);
    });
  }
  return fields;
}
