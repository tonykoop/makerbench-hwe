import { useCallback, useEffect, useRef, useState } from "preact/hooks";

import { html } from "../html.js";
import { Empty, ErrorState, Loading } from "../components/states.js";
import { RevealPanel } from "../components/reveal.js";
import { VoteStage } from "../components/voteStage.js";
import { api } from "../lib/api.js";
import { identityLeaks } from "../lib/anonymity.js";
import { emptyFlags, flagsPayload, WINNER_TEXT } from "../lib/voteKeys.js";

const UNDO_WINDOW_MS = 8000;
const enc = encodeURIComponent;

function VoteSession({ runId, voter }) {
  const [queue, setQueue] = useState({ status: "loading", data: null, error: null });
  const [skip, setSkip] = useState(0);
  const [busy, setBusy] = useState(false);
  const [flags, setFlags] = useState(emptyFlags);
  const [lastVote, setLastVote] = useState(null);
  const [reveal, setReveal] = useState({ status: "idle" });
  const [message, setMessage] = useState("");
  const [focusToken, setFocusToken] = useState(0);
  const alive = useRef(true);
  useEffect(() => () => (alive.current = false), []);

  const loadQueue = useCallback(
    async (skipValue) => {
      setBusy(true);
      try {
        const data = await api(`/api/runs/${enc(runId)}/queue?voter=${enc(voter)}&skip=${skipValue}`);
        if (alive.current) setQueue({ status: "ready", data, error: null });
      } catch (error) {
        if (alive.current) setQueue({ status: "error", data: null, error });
      } finally {
        if (alive.current) setBusy(false);
      }
    },
    [runId, voter],
  );

  useEffect(() => {
    loadQueue(skip);
  }, [loadQueue, skip]);

  // The undo offer lasts a few seconds; the server itself has no deadline.
  useEffect(() => {
    if (!lastVote) return undefined;
    const timer = setTimeout(() => setLastVote(null), UNDO_WINDOW_MS);
    return () => clearTimeout(timer);
  }, [lastVote]);

  const loadReveal = async (pairId) => {
    setReveal({ status: "loading", pairId });
    try {
      const data = await api(
        `/api/runs/${enc(runId)}/judge-panel?pair_id=${enc(pairId)}&voter=${enc(voter)}`,
      );
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
      await api(`/api/runs/${enc(runId)}/vote`, {
        method: "POST",
        body: { pair_id: pair.pair_id, winner, voter, flags: flagsPayload(flags) },
      });
    } catch (error) {
      setBusy(false);
      setMessage(`Vote not saved. ${error.message}`);
      return;
    }
    setLastVote({ pairId: pair.pair_id });
    setFlags(emptyFlags());
    setMessage(`Vote saved: ${WINNER_TEXT[winner]}.`);
    loadReveal(pair.pair_id);
    if (skip === 0) await loadQueue(0);
    else setSkip(0);
  };

  const undo = async () => {
    if (!lastVote || busy) return;
    setBusy(true);
    try {
      await api(`/api/runs/${enc(runId)}/undo-vote`, {
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
      <${Empty} title="You've voted on every pair in this run">
        <p>Votes are saved in the run's <code>votes.blind.jsonl</code>.</p>
        <p><a href="#/runs/${enc(runId)}">Back to the run</a></p>
      <//>
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
        canUndo=${Boolean(lastVote)}
        canSkip=${skippable > 1}
        focusToken=${focusToken}
      />
    `;
  }

  return html`
    ${body}
    <div class="vote-feedback" role="status" aria-live="polite">
      ${message && html`<span>${message}</span>`}
      ${lastVote &&
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

export function VoteScreen({ route, voter }) {
  const runId = route.args[0] || null;
  return html`
    <div class="screen screen-vote">
      <h1 tabindex="-1">Blind voting</h1>
      ${runId
        ? html`
            <p class="lede">
              Run <strong>${runId}</strong>, voting as <strong>${voter}</strong>. Entrants stay hidden
              until your vote is saved.
            </p>
            <${VoteSession} key=${`${runId}|${voter}`} runId=${runId} voter=${voter} />
          `
        : html`
            <${Empty} title="Choose a run to vote on">
              <p><a href="#/runs">Pick a run on the Runs screen</a>, then start blind voting from its summary.</p>
            <//>
          `}
    </div>
  `;
}
