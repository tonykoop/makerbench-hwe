import { html } from "../html.js";
import { WINNER_TEXT } from "../lib/voteKeys.js";

function objectiveText(objective) {
  if (!objective) return "No mesh-gate result recorded";
  const rate = Number(objective.objective_pass_rate);
  const pass = Number.isFinite(rate) ? `${Math.round(rate * 100)}% objective pass` : "Objective result recorded";
  return objective.passed === false ? `${pass}, mesh gate failed` : pass;
}

// Shown only after the server has stored this voter's vote: the judge-panel
// route 404s before that, and the screen never requests it earlier (#737).
export function RevealPanel({ reveal, onRetry }) {
  if (reveal.status === "idle" || reveal.status === "hidden") return null;
  if (reveal.status === "loading") {
    return html`<p class="state state-loading" role="status" aria-busy="true">Revealing your last vote…</p>`;
  }
  if (reveal.status === "error") {
    return html`
      <div class="state state-error" role="alert">
        <p class="state-title">Couldn't reveal your last vote. ${reveal.error?.message}</p>
        <button type="button" class="button button-quiet" onClick=${onRetry}>Try again</button>
      </div>
    `;
  }
  const data = reveal.data;
  const sides = [
    ["Candidate A", data.left],
    ["Candidate B", data.right],
  ];
  return html`
    <section class="reveal" aria-labelledby="reveal-title">
      <h2 id="reveal-title">Your last vote, revealed</h2>
      <p>You picked ${WINNER_TEXT[data.human_winner] || data.human_winner}.</p>
      <dl class="reveal-sides">
        ${sides.map(
          ([name, side]) => html`
            <div key=${name}>
              <dt>${name}</dt>
              <dd class="reveal-model">${side?.model_id || "Unknown entrant"}</dd>
              <dd>${objectiveText(side?.objective)}</dd>
            </div>
          `,
        )}
      </dl>
      <p class="hint">
        ${data.judge
          ? `The VLM judge (${data.judge.judge_model_id}) picked ${WINNER_TEXT[data.judge.winner] || data.judge.winner}.`
          : "No VLM judge verdict recorded for this pair yet."}
      </p>
    </section>
  `;
}
