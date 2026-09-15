import { useMemo } from "preact/hooks";

import { html } from "../html.js";
import { Empty } from "../components/states.js";
import { VoteSession } from "../components/voteSession.js";
import { runVoteEndpoints } from "../lib/voteEndpoints.js";

export function VoteScreen({ route, voter }) {
  const runId = route.args[0] || null;
  const endpoints = useMemo(() => (runId ? runVoteEndpoints(runId) : null), [runId]);
  return html`
    <div class="screen screen-vote">
      <h1 tabindex="-1">Blind voting</h1>
      ${runId
        ? html`
            <p class="lede">
              Run <strong>${runId}</strong>, voting as <strong>${voter}</strong>. Entrants stay hidden
              until your vote is saved.
            </p>
            <${VoteSession} key=${`${runId}|${voter}`} endpoints=${endpoints} voter=${voter} />
          `
        : html`
            <${Empty} title="Choose a run to vote on">
              <p><a href="#/runs">Pick a run on the Runs screen</a>, then start blind voting from its summary.</p>
            <//>
          `}
    </div>
  `;
}
