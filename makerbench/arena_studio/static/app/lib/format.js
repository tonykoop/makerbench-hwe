export function formatWhen(iso) {
  if (!iso) return "Not recorded";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function plural(count, one, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`;
}

// Real run logs carry no `grade` block (#720), so the API's compiled/manifold
// counts are silently zero on real runs. Shown as unknown instead (Tony, Q4).
export const UNKNOWN_GRADE_COUNT = "Unknown";
