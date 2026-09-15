import { useCallback, useEffect, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { Empty, ErrorState, Loading } from "./states.js";
import { RevealPanel } from "./reveal.js";
import { VoteStage } from "./voteStage.js";
import { api } from "../lib/api.js";
import { identityLeaks } from "../lib/anonymity.js";
import { emptyFlags, flagsPayload, WINNER_TEXT } from "../lib/voteKeys.js";

const UNDO_WINDOW_MS = 8000;

// One blind voting session against `endpoints` (see lib/voteEndpoints.js).
export function VoteSession({ endpoints, voter }) {
  const [queue, setQueue] = useState({ status: "loading", data: null, error: null });
  const [skip, setSkip] = useState(0);
  const [busy, setBusy] = useState(false);
  const [flags, setFlags] = useState(emptyFlags);
  const [lastVote, setLastVote] = useState(null);
  const [reveal, setReveal] = useState({ status: "idle" });
  const [message, setMessage] = useState("");
  const [focusToken, setFocusToken] = useState(0);
  const alive = useRef(true);
  const [afterVote, setAfterVote] = useState(0);
  const doneRef = useRef(null);
  useEffect(() => () => (alive.current = false), []);
  const canUndoAtAll = Boolean(endpoints.undo);

  const loadQueue = useCallback(
    async (skipValue) => {
      setBusy(true);
      try {
        const data = await api(endpoints.queue(voter, skipValue));
        if (alive.current) setQueue({ status: "ready", data, error: null });
      } catch (error) {
        if (alive.current) setQueue({ status: "error", data: null, error });
      } finally {
        if (alive.current) setBusy(false);
      }
    },
    [endpoints, voter],
  );

  useEffect(() => {
    loadQueue(skip);
  }, [loadQueue, skip]);

  // Voting the last pair (of a run or a morning bundle) unmounts the stage and the
  // control that had focus. Put focus on the "voted every pair" state, not <body>.
  useEffect(() => {
    if (!afterVote || queue.status !== "ready" || queue.data?.current_pair) return;
    doneRef.current?.focus();
    setAfterVote(0);
  }, [afterVote, queue]);

  // The undo offer lasts a few seconds; the server itself has no deadline.
  useEffect(() => {
    if (!lastVote) return undefined;
    const timer = setTimeout(() => setLastVote(null), UNDO_WINDOW_MS);
    return () => clearTimeout(timer);
  }, [lastVote]);

  const loadReveal = async (pairId) => {
    setReveal({ status: "loading", pairId });
    try {
      const data = await api(endpoints.reveal(pairId, voter));
      if (alive.current) setReveal({ status: "ready", data, pairId });
    } catch (error) {
      if (!alive.current) return;
      setReveal(error.status === 404 ? { status: "hidden" } : { status: "error", error, pairId });
    }
  };

  const pair = queue.data?.current_pair || null;
  const leaks = pair ? identityLeaks(pair) : [];

  const vote = async (winner) => {
    if (!pair || busy || leaks.length) return;
    setBusy(true);
    try {
      await api(endpoints.vote, {
        method: "POST",
        body: { pair_id: pair.pair_id, winner, voter, flags: flagsPayload(flags) },
      });
    } catch (error) {
      setBusy(false);
      setMessage(`Vote not saved. ${error.message}`);
      return;
    }
    if (canUndoAtAll) setLastVote({ pairId: pair.pair_id });
    setFlags(emptyFlags());
    setMessage(`Vote saved: ${WINNER_TEXT[winner]}.`);
    loadReveal(pair.pair_id);
    setAfterVote((value) => value + 1);
    if (skip === 0) await loadQueue(0);
    else setSkip(0);
  };

  const undo = async () => {
    if (!canUndoAtAll || !lastVote || busy) return;
    setBusy(true);
    try {
      await api(endpoints.undo, {
        method: "POST",
        body: { pair_id: lastVote.pairId, voter },
      });
    } catch (error) {
      setBusy(false);
      setMessage(`Undo failed. ${error.message}`);
      return;
    }
    setLastVote(null);
    setReveal({ status: "idle" });
    setFocusToken((value) => value + 1);
    setMessage("Vote undone. That pair is back in your queue.");
    if (skip === 0) await loadQueue(0);
    else setSkip(0);
  };

  const skipPair = () => {
    setFlags(emptyFlags());
    setMessage("Skipped. Nothing was recorded for that pair.");
    setSkip((value) => value + 1);
  };

  let body;
  if (queue.status === "loading" && !queue.data) {
    body = html`<${Loading} label="Loading your voting queue…" />`;
  } else if (queue.status === "error") {
    body = html`<${ErrorState} error=${queue.error} onRetry=${() => loadQueue(skip)} />`;
  } else if (!pair) {
    body = html`
      <div class="vote-done" tabindex="-1" ref=${doneRef}>
        <${Empty} title=${endpoints.doneTitle}>
          <p>${endpoints.doneNote}</p>
          <p><a href=${endpoints.doneHref}>${endpoints.doneLink}</a></p>
        <//>
      </div>
    `;
  } else if (leaks.length) {
    body = html`
      <div class="state state-error" role="alert">
        <p class="state-title">This pair arrived with identifying data, so Studio won't show it.</p>
        <p>
          ${leaks.length} field${leaks.length === 1 ? "" : "s"} would reveal an entrant before your vote.
          Voting is paused; report this as a Studio bug.
        </p>
      </div>
    `;
  } else {
    const { done, total, skippable } = queue.data;
    body = html`
      <div class="vote-progress">
        <label for="vote-progress-bar">Voted ${done} of ${total} pairs</label>
        <progress id="vote-progress-bar" max=${total || 1} value=${done}></progress>
      </div>
      <${VoteStage}
        pair=${pair}
        flags=${flags}
        onFlagsChange=${setFlags}
        onVote=${vote}
        onSkip=${skipPair}
        onUndo=${undo}
        busy=${busy}
        canUndo=${canUndoAtAll && Boolean(lastVote)}
        canSkip=${skippable > 1}
        focusToken=${focusToken}
      />
    `;
  }

  return html`
    ${body}
    <div class="vote-feedback" role="status" aria-live="polite">
      ${message && html`<span>${message}</span>`}
      ${canUndoAtAll &&
      lastVote &&
      html`<button
        type="button"
        class="button button-quiet"
        aria-keyshortcuts="U"
        aria-disabled=${busy ? "true" : "false"}
        onClick=${undo}
      >
        <kbd>U</kbd> Undo
      </button>`}
    </div>
    <${RevealPanel} reveal=${reveal} onRetry=${() => reveal.pairId && loadReveal(reveal.pairId)} />
  `;
}
