/* DoE "what-if" budget slider (#697 S2, stretch).

A lightweight, read-only exploration widget: as the reviewer drags the
budget slider, it recomputes -- purely client-side, over one already-fetched
preview -- how many instrument x model cells would be affordable at that
per-cell budget. It never calls /api/doe/queue and never writes a queue
file; it's an estimate explorer, not a launcher.

Loaded after studio.js/agreement.js and reuses the shared `currentRun`
global (classic scripts share one top-level scope).
*/

let _doeWhatIfCells = [];

async function loadDoeWhatIf() {
  const el = document.getElementById('doeWhatIfCard');
  if (!el) return;

  try {
    const [tasksRes, summaryRes] = await Promise.all([
      fetch('/api/tasks'),
      currentRun ? fetch(`/api/runs/${currentRun}/summary`) : Promise.resolve(null),
    ]);
    if (!tasksRes.ok) {
      throw new Error(`${tasksRes.url} returned ${tasksRes.status}`);
    }
    if (summaryRes && !summaryRes.ok) {
      throw new Error(`${summaryRes.url} returned ${summaryRes.status}`);
    }
    const tasks = (await tasksRes.json()).tasks || [];
    const models = summaryRes ? (await summaryRes.json()).models || [] : [];

    if (tasks.length === 0 || models.length === 0) {
      document.getElementById('doeWhatIfSummary').innerHTML =
        '<span class="doe-whatif-note">Select a run with entrants to preview a DoE what-if matrix.</span>';
      return;
    }

    const instruments = tasks.map(t => t.id).join(',');
    const modelIds = models.join(',');
    // Kept to level L1 / seed 0 to keep this an instant client-side estimate,
    // not a full multi-level DoE build -- see #697 D3 for the real matrix builder.
    const params = new URLSearchParams({ instruments, models: modelIds, levels: 'L1', seeds: '0' });
    const res = await fetch(`/api/doe/preview?${params}`);
    if (!res.ok) {
      throw new Error(`${res.url} returned ${res.status}`);
    }
    const data = await res.json();
    _doeWhatIfCells = data.cells || [];

    renderDoeWhatIf();
  } catch (e) {
    // Built via DOM properties, not string-templated innerHTML: e.message
    // can carry content this page doesn't control (a server error string),
    // so it must land as text, never as parsed HTML (#731 R2 finding).
    const note = document.createElement('span');
    note.className = 'doe-whatif-note';
    note.textContent = `DoE what-if preview unavailable: ${e.message}`;
    document.getElementById('doeWhatIfSummary').replaceChildren(note);
  }
}

function renderDoeWhatIf() {
  const slider = document.getElementById('doeBudgetSlider');
  const summary = document.getElementById('doeWhatIfSummary');
  if (!slider || !summary) return;

  const budget = parseFloat(slider.value);
  document.getElementById('doeBudgetLabel').textContent = `$${budget.toFixed(2)} per cell`;

  let affordable = 0;
  let unknownCost = 0;
  let overBudget = 0;
  for (const cell of _doeWhatIfCells) {
    const cost = cell.estimate ? cell.estimate.cost_usd : null;
    if (cost === null || cost === undefined) {
      unknownCost += 1; // never assumed $0 -- tracked separately, on purpose
    } else if (cost <= budget) {
      affordable += 1;
    } else {
      overBudget += 1;
    }
  }

  summary.innerHTML = `
    <div class="doe-whatif-stat"><div class="value">${affordable}</div><div class="label">Affordable</div></div>
    <div class="doe-whatif-stat"><div class="value">${overBudget}</div><div class="label">Over budget</div></div>
    <div class="doe-whatif-stat"><div class="value">${unknownCost}</div><div class="label">Unknown cost</div></div>
    <div class="doe-whatif-stat"><div class="value">${_doeWhatIfCells.length}</div><div class="label">Total cells (L1, seed 0)</div></div>
  `;
}

function initDoeWhatIfSlider() {
  const slider = document.getElementById('doeBudgetSlider');
  if (!slider) return;
  slider.addEventListener('input', renderDoeWhatIf);
}

document.addEventListener('DOMContentLoaded', initDoeWhatIfSlider);
