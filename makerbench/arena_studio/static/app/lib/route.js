// Hash routes: #/<screen>/<arg>...?key=value. Every screen is linkable.

function safeDecode(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function parseHash(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const [pathPart, queryPart = ""] = raw.split("?");
  const segments = pathPart.split("/").filter(Boolean).map(safeDecode);
  return {
    screen: segments[0] || "runs",
    args: segments.slice(1),
    query: Object.fromEntries(new URLSearchParams(queryPart)),
  };
}

export function buildHash(screen, args = [], query = {}) {
  const path = [screen, ...args].map((part) => encodeURIComponent(part)).join("/");
  const entries = Object.entries(query).filter(
    ([, value]) => value !== undefined && value !== null && value !== "",
  );
  const search = new URLSearchParams(entries).toString();
  return `#/${path}${search ? `?${search}` : ""}`;
}
