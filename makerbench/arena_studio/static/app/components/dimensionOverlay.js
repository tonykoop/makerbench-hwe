import { useEffect, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { addPick, distanceMm, formatMm, hotspotPosition, isClick, overlayRows, pickText, wallHotspot } from "../lib/dimensions.js";

// Measure overlay for the workbench 3D preview (#975). Never used on blind
// vote surfaces: measurements there could bias a vote.
//
// `element` is the loaded <model-viewer>. Hotspots are its slotted children,
// positioned in model units (mm); picking uses its positionAndNormalFromPoint.

function syncHotspots(element, hotspots) {
  const wanted = new Map(hotspots.map((spot) => [spot.slot, spot]));
  for (const node of [...element.querySelectorAll(":scope > .measure-hotspot")]) {
    if (!wanted.has(node.slot)) node.remove();
  }
  for (const spot of hotspots) {
    let node = element.querySelector(`:scope > .measure-hotspot[slot="${spot.slot}"]`);
    if (!node) {
      node = document.createElement("span");
      node.className = "measure-hotspot";
      node.setAttribute("slot", spot.slot);
      node.setAttribute("role", "img");
      element.appendChild(node);
    }
    node.dataset.kind = spot.kind;
    node.dataset.position = spot.position;
    node.textContent = spot.text;
    node.setAttribute("aria-label", spot.label);
  }
}

export function DimensionOverlay({ element, url }) {
  const [state, setState] = useState({ status: "loading", data: null, error: "" });
  const [picking, setPicking] = useState(false);
  const [points, setPoints] = useState([]);
  const down = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading", data: null, error: "" });
    setPoints([]);
    api(url)
      .then((data) => !cancelled && setState({ status: "ready", data, error: "" }))
      .catch((err) => !cancelled && setState({ status: "error", data: null, error: err.message }));
    return () => {
      cancelled = true;
    };
  }, [url]);

  // Hotspots: the min-wall marker plus the picked points.
  const wall = state.status === "ready" ? wallHotspot(state.data) : null;
  const wallValue = state.status === "ready" ? overlayRows(state.data).find((row) => row.key === "wall") : null;
  useEffect(() => {
    if (!element) return undefined;
    const spots = [];
    if (wall) {
      spots.push({ slot: "hotspot-measure-wall", kind: "wall", position: wall.position, text: "", label: `Min wall, ${wallValue?.text}` });
    }
    points.forEach((point, index) => {
      const name = index === 0 ? "A" : "B";
      spots.push({ slot: `hotspot-measure-${name.toLowerCase()}`, kind: "pick", position: hotspotPosition(point), text: name, label: `Point ${name}` });
    });
    syncHotspots(element, spots);
    return undefined;
  }, [element, wall?.position, wallValue?.text, points]);

  useEffect(() => () => element && syncHotspots(element, []), [element]);

  // Picking pauses the turntable so the model holds still under the cursor.
  useEffect(() => {
    if (!element || !picking) return undefined;
    const rotating = element.autoRotate;
    element.autoRotate = false;
    element.classList.add("is-picking");
    const onDown = (event) => {
      down.current = { x: event.clientX, y: event.clientY };
    };
    const onUp = (event) => {
      const up = { x: event.clientX, y: event.clientY };
      const press = down.current;
      down.current = null;
      if (!isClick(press, up) || typeof element.positionAndNormalFromPoint !== "function") return;
      const hit = element.positionAndNormalFromPoint(event.clientX, event.clientY);
      if (hit?.position) setPoints((current) => addPick(current, hit.position));
    };
    element.addEventListener("pointerdown", onDown);
    element.addEventListener("pointerup", onUp);
    return () => {
      element.removeEventListener("pointerdown", onDown);
      element.removeEventListener("pointerup", onUp);
      element.classList.remove("is-picking");
      element.autoRotate = rotating;
    };
  }, [element, picking]);

  const rows = state.status === "ready" ? overlayRows(state.data) : [];
  const distance = points.length === 2 ? distanceMm(points[0], points[1]) : null;
  return html`
    <section class="measure-overlay" aria-label="Measurements">
      <h4>Measurements</h4>
      ${state.status === "loading" && html`<p class="hint" role="status">Measuring the compiled mesh…</p>`}
      ${state.status === "error" && html`<p class="check-note check-error" role="note">Measurements unavailable: ${state.error}</p>`}
      ${state.status === "ready" &&
      html`<dl class="measure-list">
        ${rows.map(
          (item) => html`<div key=${item.key} class="measure-row" data-metric=${item.key} data-ok=${item.ok ? "true" : "false"}>
            <dt>${item.label}</dt>
            <dd>${item.text}${item.note && html` <span class="hint">(${item.note})</span>`}</dd>
          </div>`,
        )}
      </dl>`}
      <div class="measure-tools">
        <button
          type="button"
          class="button button-quiet"
          aria-pressed=${picking ? "true" : "false"}
          disabled=${!element}
          onClick=${() => {
            setPicking(!picking);
            setPoints([]);
          }}
        >
          Measure distance
        </button>
        <button type="button" class="button button-quiet" disabled=${!points.length} onClick=${() => setPoints([])}>Clear</button>
        <span class="measure-readout" role="status" data-distance=${distance == null ? "" : String(distance)}>
          ${picking ? pickText(points) : distance == null ? "Pointer only: pick two points on the model." : `Distance A–B: ${formatMm(distance)}`}
        </span>
      </div>
    </section>
  `;
}
