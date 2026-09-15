import { html } from "../html.js";
import { useResource } from "../hooks/useResource.js";

function serverStatus(health) {
  if (health.status === "ready") return { state: "ok", text: `Server ${health.data.version}` };
  if (health.status === "error") return { state: "error", text: "Server not responding" };
  return { state: "loading", text: "Checking server…" };
}

export function StudioHeader({ runs, runId, onSelectRun, voter, onVoterChange }) {
  const health = useResource("/api/health");
  const status = serverStatus(health);
  const options = runs.status === "ready" ? runs.data.runs : [];
  let placeholder = "No runs found";
  if (runs.status === "loading") placeholder = "Loading runs…";
  else if (runs.status === "error") placeholder = "Runs unavailable";
  else if (options.length) placeholder = "Choose a run";

  return html`
    <header class="studio-header">
      <a class="wordmark" href="#/runs">Arena Studio</a>
      <div class="header-controls">
        <label class="field">
          <span class="field-label">Run</span>
          <select
            value=${runId || ""}
            disabled=${options.length === 0}
            onChange=${(event) => onSelectRun(event.currentTarget.value || null)}
          >
            <option value="">${placeholder}</option>
            ${options.map(
              (run) => html`<option key=${run.run_id} value=${run.run_id}>${run.run_id}</option>`,
            )}
          </select>
        </label>
        <label class="field">
          <span class="field-label">Voting as</span>
          <input
            type="text"
            value=${voter}
            size="10"
            autocomplete="off"
            spellcheck="false"
            onChange=${(event) => onVoterChange(event.currentTarget.value)}
          />
        </label>
        <p class="server-status" role="status" data-state=${status.state}>${status.text}</p>
      </div>
    </header>
  `;
}
