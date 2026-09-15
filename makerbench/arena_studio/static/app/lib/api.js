// Every Studio request goes through api(): same-origin paths only, JSON in and
// out, and a non-2xx response is always an error, never data.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const SERVER_UNREACHABLE =
  "The Studio server isn't responding. Check that `makerbench arena studio` is still running.";

export function errorMessage(status, bodyText) {
  if (status === 0) return SERVER_UNREACHABLE;
  let detail = "";
  try {
    const parsed = JSON.parse(bodyText);
    if (typeof parsed?.detail === "string") {
      detail = parsed.detail;
    } else if (Array.isArray(parsed?.detail)) {
      detail = parsed.detail
        .map((item) => item?.msg)
        .filter(Boolean)
        .join("; ");
    }
  } catch {
    detail = String(bodyText || "").trim().slice(0, 200);
  }
  if (!detail) return `The server returned HTTP ${status}.`;
  return status === 404 ? detail : `${detail} (HTTP ${status})`;
}

export function isLocalPath(path) {
  return typeof path === "string" && path.startsWith("/") && !path.startsWith("//");
}

export async function api(path, { method = "GET", body, signal } = {}) {
  if (!isLocalPath(path)) {
    throw new ApiError(0, `Refusing a request outside this Studio server: ${path}`);
  }
  let response;
  try {
    response = await fetch(path, {
      method,
      signal,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new ApiError(0, SERVER_UNREACHABLE);
  }
  const text = await response.text();
  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(response.status, text));
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? JSON.parse(text) : text;
}
