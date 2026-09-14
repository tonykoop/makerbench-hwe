import { useCallback, useEffect, useState } from "preact/hooks";

import { api } from "../lib/api.js";

// Loads one API path. While loading or after an error, `data` is null: stale
// data is never shown as if it were current.
export function useResource(path) {
  const [state, setState] = useState({
    status: path ? "loading" : "idle",
    data: null,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!path) {
      setState({ status: "idle", data: null, error: null });
      return undefined;
    }
    const controller = new AbortController();
    setState({ status: "loading", data: null, error: null });
    api(path, { signal: controller.signal })
      .then((data) => setState({ status: "ready", data, error: null }))
      .catch((error) => {
        if (error?.name !== "AbortError") {
          setState({ status: "error", data: null, error });
        }
      });
    return () => controller.abort();
  }, [path, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, reload };
}
