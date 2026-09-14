import { useEffect, useRef } from "preact/hooks";

import { html } from "../html.js";

// <model-viewer> probes for WebGL the moment the element exists, logging errors
// on GPU-less sessions even when hidden (#722). So the script loads, and the
// element is created, only after someone switches a WebGL-capable browser to 3D.
let loading = null;

export function loadModelViewer() {
  if (!loading) loading = import("/static/assets/model-viewer.min.js");
  return loading;
}

export function ModelViewer({ src, label, onFailure }) {
  const host = useRef(null);
  const failure = useRef(onFailure);
  failure.current = onFailure;

  useEffect(() => {
    let element = null;
    let cancelled = false;
    // model-viewer re-dispatches WebGL context loss as an "error" event on the
    // element itself; a window listener can't see it through the shadow root (#710).
    const onError = () => failure.current?.();
    loadModelViewer()
      .then(() => {
        if (cancelled || !host.current) return;
        element = document.createElement("model-viewer");
        element.setAttribute("src", src);
        element.setAttribute("alt", label);
        element.setAttribute("camera-controls", "");
        element.setAttribute("auto-rotate", "");
        element.setAttribute("interaction-prompt", "none");
        element.addEventListener("error", onError);
        host.current.appendChild(element);
      })
      .catch(onError);
    return () => {
      cancelled = true;
      if (element) {
        element.removeEventListener("error", onError);
        element.remove();
      }
    };
  }, [src]);

  return html`<div class="model-host" ref=${host}></div>`;
}
