import { useEffect, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { cameraStrings, camerasMatch, canCompare3d, checkGrid, fieldOfViewFor, leaderIndex, sharedFovLimits } from "../lib/matchupCompare.js";
import { symbolValue } from "../lib/section.js";
import { webgl2Available } from "../lib/webgl.js";
import { DimensionOverlay } from "./dimensionOverlay.js";
import { ModelViewer } from "./modelViewer.js";

// Side-by-side compare of two matchup trials (#974): one shared camera for
// both 3D views, and the per-check chips in one aligned grid. The matchup
// results already name the entrants, so nothing here is blind.

function readCamera(element) {
  const orbit = element.getCameraOrbit();
  const target = element.getCameraTarget();
  return {
    theta: orbit.theta, phi: orbit.phi, radius: orbit.radius,
    x: target.x, y: target.y, z: target.z,
    fov: element.getFieldOfView(),
  };
}

function applyCamera(element, camera) {
  const strings = cameraStrings(camera, camera, camera.fov);
  if (!strings) return;
  element.cameraOrbit = strings.orbit;
  element.cameraTarget = strings.target;
  // getFieldOfView() is the rendered vertical FOV; the field-of-view property
  // is before this model's framing adjustment (the pinned scene bridge, as in
  // lib/section.js). Without the bridge, the setting is copied as is.
  const scene = symbolValue(element, "scene");
  const setting = fieldOfViewFor(camera.fov, scene?.idealAspect, scene?.aspect);
  element.fieldOfView = `${setting ?? camera.fov}deg`;
  element.jumpCameraToGoal();
}

// Whichever view the person last touched drives the other (the view that
// frames the larger model leads until then). Any camera change on the other
// view, including its own auto-framing, is pulled back to the driver's
// camera, and a view that already matches is left alone, so nothing loops.
function useSyncedCameras(elements) {
  const [synced, setSynced] = useState(false);
  const [a, b] = elements;
  useEffect(() => {
    setSynced(false);
    if (!a || !b) return undefined;
    const views = [a, b];
    const radii = views.map((element) => element.getCameraOrbit().radius);
    let driver = views[Math.max(0, leaderIndex(radii))];
    // Both views share one camera, so neither may clamp it to its own model's
    // auto limits.
    const far = Math.max(...radii.filter(Number.isFinite), 1) * 8;
    for (const element of views) {
      element.minCameraOrbit = "auto auto 0m";
      element.maxCameraOrbit = `auto auto ${far}m`;
    }
    // Zoom moves the field of view, which each view widens by its own model's
    // framing, so the views share one rendered-FOV range (#998 review).
    const limitFov = () => {
      const frames = views.map((element) => symbolValue(element, "scene"));
      const shared = sharedFovLimits(frames);
      views.forEach((element, index) => {
        const own = shared?.settings[index];
        element.minFieldOfView = `${own?.min ?? 1}deg`;
        element.maxFieldOfView = `${own?.max ?? 90}deg`;
        element.dataset.fovRange = shared ? `${shared.min} ${shared.max}` : "";
      });
    };
    limitFov();
    const align = (follower) => {
      const camera = readCamera(driver);
      if (!camerasMatch(camera, readCamera(follower), 1e-6)) applyCamera(follower, camera);
    };
    align(views[0] === driver ? views[1] : views[0]);
    const listeners = views.map((element, index) => {
      const other = views[1 - index];
      const take = () => {
        driver = element;
      };
      const changed = () => align(driver === element ? other : element);
      element.addEventListener("pointerdown", take);
      element.addEventListener("wheel", take, { passive: true });
      element.addEventListener("keydown", take);
      element.addEventListener("camera-change", changed);
      return () => {
        element.removeEventListener("pointerdown", take);
        element.removeEventListener("wheel", take);
        element.removeEventListener("keydown", take);
        element.removeEventListener("camera-change", changed);
      };
    });
    // The widening depends on each view's aspect, which a resize changes.
    const onResize = () => {
      limitFov();
      align(views[0] === driver ? views[1] : views[0]);
    };
    window.addEventListener("resize", onResize);
    setSynced(true);
    return () => {
      window.removeEventListener("resize", onResize);
      listeners.forEach((remove) => remove());
    };
  }, [a, b]);
  return synced;
}

function CheckChips({ trials }) {
  const rows = checkGrid(trials);
  if (!rows.length) return html`<p class="hint">No build checks were recorded for these trials.</p>`;
  return html`
    <div class="table-scroll">
      <table class="data-table compare-checks">
        <caption>Build checks, aligned by check</caption>
        <thead>
          <tr>
            <th scope="col">Check</th>
            ${trials.map((trial) => html`<th scope="col" key=${trial.trial_id}>${trial.entrant}</th>`)}
          </tr>
        </thead>
        <tbody>
          ${rows.map(
            (row) => html`
              <tr key=${row.check} data-check=${row.check} data-differs=${row.differs ? "true" : "false"}>
                <th scope="row">${row.label}${row.differs && html` <span class="visually-hidden">(results differ)</span>`}</th>
                ${row.cells.map(
                  (cell, index) => html`<td key=${index}><span class="check-chip" data-state=${cell.state}>${cell.text}</span></td>`,
                )}
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
  `;
}

export function MatchupCompare({ trials, onClose, closeRef }) {
  const [view, setView] = useState("renders");
  const [failed, setFailed] = useState(false);
  const [elements, setElements] = useState([null, null]);
  const headingRef = useRef(null);
  const can3d = canCompare3d(trials, webgl2Available()) && !failed;
  const synced = useSyncedCameras(view === "3d" ? elements : [null, null]);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const setElement = (index) => (element) =>
    setElements((current) => (current[index] === element ? current : current.map((item, i) => (i === index ? element : item))));
  const reason = !webgl2Available()
    ? "3D needs WebGL; the recorded renders are shown instead."
    : !trials.every((trial) => trial.mesh_url)
      ? "3D needs both trials' meshes; the recorded renders are shown instead."
      : failed
        ? "The 3D view could not start; the recorded renders are shown instead."
        : "";

  return html`
    <section class="matchup-compare" aria-labelledby="matchup-compare-title" data-synced=${synced ? "true" : "false"}>
      <div class="compare-head">
        <h3 id="matchup-compare-title" tabindex="-1" ref=${headingRef}>
          Compare ${trials[0].entrant} and ${trials[1].entrant}
        </h3>
        <button type="button" class="button button-quiet" ref=${closeRef} onClick=${onClose}>Close compare</button>
      </div>
      <div class="view-toggle" role="group" aria-label="Compare view">
        <button type="button" class="button button-quiet" aria-pressed=${view === "renders"} onClick=${() => setView("renders")}>Renders</button>
        <button
          type="button"
          class="button button-quiet"
          aria-pressed=${view === "3d"}
          aria-disabled=${can3d ? "false" : "true"}
          onClick=${() => can3d && setView("3d")}
        >
          3D, one camera
        </button>
        ${reason && html`<span class="hint">${reason}</span>`}
      </div>
      ${view === "3d" && can3d && html`<p class="hint" role="note">Orbit, zoom or pan either view; the other follows.</p>`}
      <div class="compare-columns">
        ${trials.map(
          (trial, index) => html`
            <figure class="compare-column" key=${trial.trial_id} aria-label=${trial.entrant}>
              <figcaption><strong>${trial.entrant}</strong> · ${trial.instrument_id} · seed ${trial.seed}</figcaption>
              ${view === "3d" && can3d
                ? html`<div class="compare-stage">
                      <${ModelViewer}
                        src=${trial.mesh_url}
                        label=${`${trial.entrant}, 3D model`}
                        autoRotate=${false}
                        onElement=${setElement(index)}
                        onFailure=${() => {
                          setFailed(true);
                          setView("renders");
                        }}
                      />
                    </div>
                    ${elements[index] && trial.dimensions_url
                      && html`<${DimensionOverlay} element=${elements[index]} url=${trial.dimensions_url} />`}`
                : trial.render_url
                  ? html`<img class="matchup-render compare-render" src=${trial.render_url} alt=${`Render for ${trial.entrant}`} />`
                  : html`<p class="hint">Render not available</p>`}
            </figure>
          `,
        )}
      </div>
      <${CheckChips} trials=${trials} />
    </section>
  `;
}
