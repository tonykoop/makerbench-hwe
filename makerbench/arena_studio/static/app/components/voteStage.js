import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { webgl2Available } from "../lib/webgl.js";
import {
  DEFECT_FLAGS,
  DISPOSITION_FLAGS,
  toggleFlag,
  voteActionForKey,
} from "../lib/voteKeys.js";
import { ModelViewer } from "./modelViewer.js";
import { Turntable } from "./turntable.js";

const SIDES = [
  { side: "left", name: "Candidate A", key: "A" },
  { side: "right", name: "Candidate B", key: "B" },
];

function Plate({ side, name, candidate, viewMode, onViewerFailure, flags, onToggleFlag }) {
  const flagged = flags[side] || [];
  return html`
    <figure class="plate" aria-label=${name}>
      <figcaption class="plate-name">${name}</figcaption>
      <div class="plate-view">
        ${viewMode === "3d"
          ? html`<${ModelViewer}
              src=${candidate.model3d_path}
              label=${`${name}, 3D model`}
              onFailure=${onViewerFailure}
            />`
          : html`<${Turntable} frames=${candidate.frames} still=${candidate.render_path} label=${name} />`}
      </div>
      <fieldset class="defects">
        <legend>Problems with ${name}</legend>
        ${DEFECT_FLAGS.map(
          (flag, index) => html`
            <label key=${flag.id}>
              <input
                type="checkbox"
                checked=${flagged.includes(flag.id)}
                onChange=${() => onToggleFlag(side, flag.id)}
                aria-keyshortcuts=${side === "left" ? `${index + 1}` : `Shift+${index + 1}`}
              />
              <span>${flag.label}</span>
              <kbd>${side === "left" ? "" : "⇧"}${index + 1}</kbd>
            </label>
          `,
        )}
        ${DISPOSITION_FLAGS.map(
          (flag) => html`
            <label key=${flag.id}>
              <input
                type="checkbox"
                checked=${flagged.includes(flag.id)}
                onChange=${() => onToggleFlag(side, flag.id)}
              />
              <span>${flag.label}</span>
            </label>
          `,
        )}
      </fieldset>
    </figure>
  `;
}

function ShortcutHelp({ dialogRef }) {
  const rows = [
    ["A or L", "Candidate A is better"],
    ["B or R", "Candidate B is better"],
    ["T or D", "Draw"],
    ["S", "Skip to another pair"],
    ["U", "Undo your last vote"],
    ["1 to 3", "Flag a problem on Candidate A"],
    ["Shift+1 to 3", "Flag a problem on Candidate B"],
    ["V", "Switch between turntable and 3D orbit"],
    ["Arrows, space", "Turn or pause the focused turntable"],
  ];
  return html`
    <dialog ref=${dialogRef} class="shortcut-help" aria-labelledby="shortcut-help-title">
      <h2 id="shortcut-help-title">Voting shortcuts</h2>
      <table class="data-table">
        <tbody>
          ${rows.map(
            ([keys, action]) => html`<tr key=${keys}><th scope="row"><kbd>${keys}</kbd></th><td>${action}</td></tr>`,
          )}
        </tbody>
      </table>
      <form method="dialog"><button class="button" type="submit">Close</button></form>
    </dialog>
  `;
}

export function VoteStage({
  pair,
  flags,
  onFlagsChange,
  onVote,
  onSkip,
  onUndo,
  busy,
  canUndo,
  canSkip,
  focusToken = 0,
}) {
  const webgl = useMemo(() => webgl2Available(), []);
  const [viewMode, setViewMode] = useState("turntable");
  const [viewerNotice, setViewerNotice] = useState("");
  const helpRef = useRef(null);
  const firstVoteRef = useRef(null);

  // Controls outside the stage (the Undo button) unmount after use; put the
  // keyboard user back on the vote controls instead of losing focus to <body>.
  useEffect(() => {
    if (focusToken) firstVoteRef.current?.focus();
  }, [focusToken]);
  const has3d = Boolean(pair.left.model3d_path && pair.right.model3d_path);
  const can3d = webgl && has3d && !viewerNotice;

  useEffect(() => {
    if (viewMode === "3d" && !has3d) setViewMode("turntable");
  }, [pair.pair_id]);

  const fallBack = () => {
    setViewMode("turntable");
    setViewerNotice("3D orbit stopped working in this browser, so the turntable is back.");
  };

  const toggleView = () => {
    if (viewMode === "3d") setViewMode("turntable");
    else if (can3d) setViewMode("3d");
  };

  const toggle = (side, flag) => onFlagsChange(toggleFlag(flags, side, flag));

  // One document-level handler for this stage, reading the latest props.
  const latest = useRef(null);
  latest.current = { busy, canUndo, canSkip, onVote, onSkip, onUndo, toggle, toggleView };
  useEffect(() => {
    const onKeyDown = (event) => {
      if (helpRef.current?.open) return;
      const action = voteActionForKey(event);
      if (!action) return;
      const current = latest.current;
      if (action.type === "vote" && !current.busy) current.onVote(action.winner);
      else if (action.type === "skip" && current.canSkip && !current.busy) current.onSkip();
      else if (action.type === "undo" && current.canUndo && !current.busy) current.onUndo();
      else if (action.type === "flag") current.toggle(action.side, action.flag);
      else if (action.type === "toggle3d") current.toggleView();
      else if (action.type === "help") helpRef.current?.showModal();
      else return;
      event.preventDefault();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  let viewerNote = viewerNotice;
  if (!viewerNote && !webgl) {
    viewerNote = "3D orbit needs WebGL, which this browser doesn't have. The turntable shows every angle.";
  } else if (!viewerNote && !has3d) {
    viewerNote = "No 3D models for this pair.";
  }

  return html`
    <section class="stage" aria-label="Blind comparison" data-pair-id=${pair.pair_id}>
      <div class="stage-toolbar">
        <p class="stage-context">
          ${pair.meta?.instrument_id || "Unknown instrument"}, seed ${pair.meta?.seed ?? "?"}
        </p>
        <div class="view-toggle" role="group" aria-label="Viewer">
          <button
            type="button"
            aria-pressed=${viewMode === "turntable" ? "true" : "false"}
            onClick=${() => setViewMode("turntable")}
          >
            Turntable
          </button>
          <button
            type="button"
            aria-pressed=${viewMode === "3d" ? "true" : "false"}
            aria-keyshortcuts="V"
            aria-describedby="viewer-note"
            disabled=${!can3d && viewMode !== "3d"}
            onClick=${toggleView}
          >
            3D orbit
          </button>
        </div>
        <p id="viewer-note" class="viewer-note" role="status">${viewerNote}</p>
      </div>

      <div class="plates">
        ${SIDES.map(
          ({ side, name }) => html`
            <${Plate}
              key=${side}
              side=${side}
              name=${name}
              candidate=${pair[side]}
              viewMode=${viewMode}
              onViewerFailure=${fallBack}
              flags=${flags}
              onToggleFlag=${toggle}
            />
          `,
        )}
      </div>

      <div class="vote-bar" role="group" aria-label="Cast your vote">
        <button
          type="button"
          class="vote"
          ref=${firstVoteRef}
          aria-keyshortcuts="A L"
          aria-disabled=${busy ? "true" : "false"}
          onClick=${() => !busy && onVote("left")}
        >
          <kbd>A</kbd> A is better
        </button>
        <button
          type="button"
          class="vote vote-draw"
          aria-keyshortcuts="T D"
          aria-disabled=${busy ? "true" : "false"}
          onClick=${() => !busy && onVote("draw")}
        >
          <kbd>T</kbd> Draw
        </button>
        <button
          type="button"
          class="vote"
          aria-keyshortcuts="B R"
          aria-disabled=${busy ? "true" : "false"}
          onClick=${() => !busy && onVote("right")}
        >
          <kbd>B</kbd> B is better
        </button>
        <span class="vote-bar-gap"></span>
        <button
          type="button"
          class="button-quiet"
          aria-keyshortcuts="S"
          aria-disabled=${busy || !canSkip ? "true" : "false"}
          onClick=${() => !busy && canSkip && onSkip()}
        >
          <kbd>S</kbd> Skip
        </button>
        <button type="button" class="button-quiet" aria-keyshortcuts="Shift+/" onClick=${() => helpRef.current?.showModal()}>
          <kbd>?</kbd> Shortcuts
        </button>
      </div>
      <${ShortcutHelp} dialogRef=${helpRef} />
    </section>
  `;
}
