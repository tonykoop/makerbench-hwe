import { useEffect, useRef } from "preact/hooks";

import { html } from "../html.js";
import { wireframeController } from "../lib/wireframe.js";

// <model-viewer> probes for WebGL the moment the element exists, logging errors
// on GPU-less sessions even when hidden (#722). So the script loads, and the
// element is created, only after someone switches a WebGL-capable browser to 3D.
let loading = null;

export function loadModelViewer() {
  if (!loading) loading = import("/static/assets/model-viewer.min.js");
  return loading;
}

export function ModelViewer({ src, label, onFailure, wireframe = false, onReady }) {
  const host = useRef(null);
  const controller = useRef(null);
  const enabled = useRef(wireframe);
  enabled.current = wireframe;
  const ready = useRef(onReady);
  ready.current = onReady;
  const failure = useRef(onFailure);
  failure.current = onFailure;

  useEffect(() => {
    let element = null;
    let cancelled = false;
    // model-viewer re-dispatches WebGL context loss as an "error" event on the
    // element itself; a window listener can't see it through the shadow root (#710).
    const onError = () => failure.current?.();
    const onLoad = () => {
      try {
        controller.current?.restore();
        controller.current = wireframeController(element.model);
        controller.current.set(enabled.current);
        ready.current?.(true);
      } catch {
        onError();
      }
    };
    ready.current?.(false);
    loadModelViewer()
      .then(() => {
        if (cancelled || !host.current) return;
        element = document.createElement("model-viewer");
        element.setAttribute("src", src);
        // Both candidates must load after explicit 3D opt-in, even when a
        // narrow viewport puts the second plate below the fold.
        element.setAttribute("loading", "eager");
        element.setAttribute("alt", label);
        element.setAttribute("camera-controls", "");
        element.setAttribute("auto-rotate", "");
        element.setAttribute("interaction-prompt", "none");
        element.addEventListener("error", onError);
        element.addEventListener("load", onLoad);
        host.current.appendChild(element);
      })
      .catch(onError);
    return () => {
      cancelled = true;
      if (element) {
        element.removeEventListener("error", onError);
        element.removeEventListener("load", onLoad);
        controller.current?.restore();
        controller.current = null;
        element.remove();
      }
    };
  }, [src]);

  useEffect(() => {
    try {
      controller.current?.set(wireframe);
    } catch {
      failure.current?.();
    }
  }, [wireframe]);

  return html`<div class="model-host" ref=${host}></div>`;
}
