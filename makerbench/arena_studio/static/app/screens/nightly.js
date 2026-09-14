import { useCallback, useEffect, useMemo, useState } from "preact/hooks";

import { html } from "../html.js";
import { api } from "../lib/api.js";
import { buildHash } from "../lib/route.js";
import {
  budgetSummary,
  formatAge,
  formatUsd,
  jobNotes,
  leaseKind,
  leaseText,
  nightlyStatusText,
  shouldPoll,
} from "../lib/nightly.js";
import { morningVoteEndpoints } from "../lib/voteEndpoints.js";
import { useResource } from "../hooks/useResource.js";
import { Empty, ErrorState, Loading } from "../components/states.js";
import { VoteSession } from "../components/voteSession.js";

const POLL_MS = 10000;
const DEFAULT_QUEUE = "runs/nightly-cad-queue.json";

function NoQueue() {
  return html`
    <${Empty} title="No nightly queue yet">
      <p>Studio reads <code>${DEFAULT_QUEUE}</code> in this checkout. The nightly scheduler writes it.</p>
    <//>
  `;
}

// Keeps the last good view on screen while it refreshes, so polling never blanks the page.
function useNightlyView() {
  const [state, setState] = useState({ status: "loading", data: null, error: null, at: null });
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    let cancelled = false;
    api("/api/nightly/queue")
      .then((data) => !cancelled && setState({ status: "ready", data, error: null, at: new Date() }))
      .catch((error) => !cancelled && setState((prev) => ({ ...prev, status: "error", error })));
    return () => {
      cancelled = true;
    };
  }, [tick]);
  const live = shouldPoll(state.data);
  useEffect(() => {
    if (!live) return undefined;
    const timer = setTimeout(refresh, POLL_MS);
    return () => clearTimeout(timer);
  }, [live, state, refresh]);
  return { ...state, refresh, live };
}

function LeasePanel({ lease }) {
  return html`
    <section class="panel lease-panel" aria-labelledby="lease-title">
      <h2 id="lease-title">Lease</h2>
      <p class="lease-status" data-kind=${leaseKind(lease)}>${leaseText(lease)}</p>
      <dl class="facts">
        <div><dt>State</dt><dd>${lease.status}</dd></div>
        <div><dt>Process</dt><dd>${lease.pid ?? "None"}</dd></div>
        <div><dt>Last heartbeat</dt><dd>${formatAge(lease.age_s)}</dd></div>
        <div><dt>Lock file</dt><dd><code>${lease.lock_path}</code></dd></div>
      </dl>
    </section>
  `;
}

function JobsPanel({ view }) {
  const jobs = view.jobs || [];
  return html`
    <section class="panel nightly-jobs" aria-labelledby="nightly-jobs-title">
      <h2 id="nightly-jobs-title">Jobs</h2>
      <p class="hint">Queue file <code>${view.queue_path}</code> (${view.queue_schema || "unknown schema"}).</p>
      ${jobs.length === 0
        ? html`<${Empty} title="The queue has no jobs" />`
        : html`
            <div class="table-scroll">
              <table class="data-table nightly-table">
                <caption class="visually-hidden">Nightly jobs and their budgets</caption>
                <thead>
                  <tr>
                    <th scope="col">Job</th>
                    <th scope="col">Instrument</th>
                    <th scope="col">Status</th>
                    <th scope="col">Run</th>
                    <th scope="col" class="num">Entrants</th>
                    <th scope="col">Budget</th>
                  </tr>
                </thead>
                <tbody>
                  ${jobs.map((job) => {
                    const budget = budgetSummary(job);
                    const notes = jobNotes(job);
                    return html`
                      <tr key=${job.job_id} data-job=${job.job_id}>
                        <th scope="row">
                          ${job.job_id}
                          ${notes.length > 0 &&
                          html`<ul class="job-notes">
                            ${notes.map((note) => html`<li key=${note}>${note}</li>`)}
                          </ul>`}
                        </th>
                        <td>${job.instrument_id}</td>
                        <td>
                          ${nightlyStatusText(job.status)}
                          ${job.orphaned && html` <span class="badge-stalled">Stalled</span>`}
                        </td>
                        <td>${job.run_id || "Not started"}</td>
                        <td class="num">${job.entrant_count}</td>
                        <td class="budget-cell" data-kind=${budget.kind}>
                          ${budget.text}
                          ${(job.budget?.outcomes || []).length > 0 &&
                          html`<details class="charges">
                            <summary>${job.budget.outcomes.length} charges</summary>
                            <ul>
                              ${job.budget.outcomes.map(
                                (outcome, index) => html`<li key=${index}>${outcome.entrant_id}: ${formatUsd(outcome.cost_usd)}</li>`,
                              )}
                            </ul>
                          </details>`}
                        </td>
                      </tr>
                    `;
                  })}
                </tbody>
              </table>
            </div>
          `}
    </section>
  `;
}

export function NightlyScreen() {
  const view = useNightlyView();
  let body;
  if (view.status === "loading" && !view.data) body = html`<${Loading} label="Reading the nightly queue…" />`;
  else if (!view.data && view.error?.status === 404) body = html`<${NoQueue} />`;
  else if (!view.data) body = html`<${ErrorState} error=${view.error} onRetry=${view.refresh} />`;
  else {
    body = html`
      ${view.status === "error" &&
      html`<p class="panel-status is-error" role="alert">Couldn't refresh. Showing the last reading. ${view.error.message}</p>`}
      <${LeasePanel} lease=${view.data.lease} />
      <${JobsPanel} view=${view.data} />
    `;
  }
  return html`
    <div class="screen screen-nightly">
      <h1 tabindex="-1">Nightly cockpit</h1>
      <div class="lede-row">
        <p class="lede">What the nightly CAD queue is doing. Read-only: nothing here changes the queue or its lease.</p>
        <div class="refresh">
          ${view.at && html`<span class="hint">Read at ${view.at.toLocaleTimeString()}${view.live ? ", refreshing every 10 s" : ""}</span>`}
          <button type="button" class="button button-quiet" onClick=${view.refresh}>Refresh</button>
        </div>
      </div>
      ${body}
    </div>
  `;
}

function BundleList() {
  const bundles = useResource("/api/morning/queue");
  if (bundles.status === "loading" || bundles.status === "idle") return html`<${Loading} label="Looking for morning bundles…" />`;
  if (bundles.status === "error") {
    return bundles.error?.status === 404
      ? html`<${NoQueue} />`
      : html`<${ErrorState} error=${bundles.error} onRetry=${bundles.reload} />`;
  }
  const list = bundles.data.bundles || [];
  if (list.length === 0) {
    return html`
      <${Empty} title="No bundles ready for review">
        <p>A bundle appears here once the nightly run finalizes a job's morning bundle.</p>
      <//>
    `;
  }
  return html`
    <div class="table-scroll">
      <table class="data-table bundles">
        <caption class="visually-hidden">Morning bundles ready for blind review</caption>
        <thead>
          <tr>
            <th scope="col">Job</th>
            <th scope="col">Instrument</th>
            <th scope="col">Run</th>
            <th scope="col" class="num">Valid candidates</th>
            <th scope="col" class="num">Failed</th>
            <th scope="col" class="num">Cost</th>
            <th scope="col"><span class="visually-hidden">Review</span></th>
          </tr>
        </thead>
        <tbody>
          ${list.map(
            (bundle) => html`
              <tr key=${bundle.job_id}>
                <th scope="row">${bundle.job_id}</th>
                <td>${bundle.instrument_id}</td>
                <td>${bundle.run_id}</td>
                <td class="num measure">${bundle.valid_candidate_count ?? "Unknown"}</td>
                <td class="num">${bundle.failed_candidate_count ?? "Unknown"}</td>
                <td class="num measure">${formatUsd(bundle.cost_usd)}</td>
                <td><a class="button button-quiet" href=${buildHash("morning", [bundle.job_id])}>Review blind</a></td>
              </tr>
            `,
          )}
        </tbody>
      </table>
    </div>
  `;
}

export function MorningScreen({ route, voter }) {
  const jobId = route.args[0] || null;
  const endpoints = useMemo(() => (jobId ? morningVoteEndpoints(jobId) : null), [jobId]);
  return html`
    <div class="screen screen-morning screen-vote">
      <h1 tabindex="-1">Morning review</h1>
      ${jobId
        ? html`
            <p class="lede">
              Nightly job <strong>${jobId}</strong>, voting as <strong>${voter}</strong>. Entrants stay hidden until your
              vote is saved. Morning votes can't be undone.
            </p>
            <p><a href="#/morning">All morning bundles</a></p>
            <${VoteSession} key=${`${jobId}|${voter}`} endpoints=${endpoints} voter=${voter} />
          `
        : html`
            <p class="lede">Nightly bundles ready for blind review. Choose one to vote on its pairs.</p>
            <${BundleList} />
          `}
    </div>
  `;
}
