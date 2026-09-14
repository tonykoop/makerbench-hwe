/* Agreement Studio extras (#699 D2): bootstrap-CI bars, per-family filter, outlier
   call-outs. Loaded after studio.js and reuses its `currentRun` global (both are
   plain classic scripts sharing one top-level scope) -- doesn't redeclare it or
   touch studio.js's own tables, so it can't collide with cedar's C1/C2/C3/C4 work. */

const OUTLIER_RANK_DELTA_THRESHOLD = 2;

async function loadAgreementStudioExtras() {
  if (!currentRun) return;
  const container = document.getElementById('agreementStudioExtras');
  if (!container) return;

  try {
    const [ciRes, detailedRes, familiesRes] = await Promise.all([
      fetch(`/api/runs/${currentRun}/leaderboard/ci`),
      fetch(`/api/runs/${currentRun}/agreement/detailed`),
      fetch(`/api/runs/${currentRun}/agreement/families`),
    ]);
    for (const res of [ciRes, detailedRes, familiesRes]) {
      if (!res.ok) {
        throw new Error(`${res.url} returned ${res.status}`);
      }
    }
    const ciData = await ciRes.json();
    const detailed = await detailedRes.json();
    const families = (await familiesRes.json()).families || {};

    renderCiBars(ciData.leaderboard || []);
    renderSmallSampleCaveat(detailed.agreement);
    renderOutliers(detailed.rankings || []);
    populateFamilyFilter(families);
  } catch (e) {
    container.querySelector('.agreement-extras-header + *').textContent =
      'Agreement Studio extras unavailable: ' + e.message;
  }
}

function renderCiBars(rows) {
  const el = document.getElementById('ciBars');
  if (!el) return;
  const rated = rows.filter(r => r.games > 0);
  if (rated.length === 0) {
    el.innerHTML = '<div class="ci-unknown">No rated entrants yet.</div>';
    return;
  }

  const known = rated.filter(r => r.ci_low !== null && r.ci_high !== null);
  const lo = known.length ? Math.min(...known.map(r => r.ci_low)) : Math.min(...rated.map(r => r.rating));
  const hi = known.length ? Math.max(...known.map(r => r.ci_high)) : Math.max(...rated.map(r => r.rating));
  const span = Math.max(1, hi - lo);
  const pct = (v) => Math.min(100, Math.max(0, ((v - lo) / span) * 100));

  el.innerHTML = rated.map(row => {
    const entrant = escapeHtml(row.entrant);
    if (row.ci_low === null || row.ci_high === null) {
      return `<div class="ci-row"><div class="ci-entrant">${entrant}</div>` +
        `<div class="ci-unknown">insufficient votes for a bootstrap CI</div><div></div></div>`;
    }
    const left = pct(row.ci_low);
    const width = Math.max(1, pct(row.ci_high) - left);
    const point = pct(row.rating);
    return `<div class="ci-row" title="95% CI ${row.ci_low.toFixed(1)}-${row.ci_high.toFixed(1)}">` +
      `<div class="ci-entrant">${entrant}</div>` +
      `<div class="ci-track"><div class="ci-bar" style="left:${left}%;width:${width}%"></div>` +
      `<div class="ci-point" style="left:${point}%"></div></div>` +
      `<div>${row.rating.toFixed(1)}</div></div>`;
  }).join('');
}

function renderSmallSampleCaveat(agreement) {
  const el = document.getElementById('smallSampleCaveat');
  if (!el) return;
  if (agreement && agreement.small_sample) {
    el.textContent = '⚠️ ' + agreement.caveat;
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}

function renderOutliers(rankings) {
  const el = document.getElementById('outlierCallouts');
  if (!el) return;
  const outliers = rankings.filter(
    r => r.rank_delta !== null && Math.abs(r.rank_delta) >= OUTLIER_RANK_DELTA_THRESHOLD
  );
  if (outliers.length === 0) {
    el.innerHTML = '<span class="ci-unknown">No rank outliers at this threshold.</span>';
    return;
  }
  el.innerHTML = outliers.map(r =>
    `<span class="outlier-badge">${escapeHtml(r.entrant)}: rank delta ${r.rank_delta > 0 ? '+' : ''}${r.rank_delta}</span>`
  ).join('');
}

function populateFamilyFilter(families) {
  const select = document.getElementById('familyFilterSelect');
  if (!select) return;
  const names = Object.keys(families).sort();
  const previous = select.value;

  // Built via DOM properties, not string-templated HTML: escapeHtml() only
  // safely encodes for text-node insertion, not for a value landing inside
  // a double-quoted HTML attribute (a `"` in a family name could break out
  // of the `value="..."` attribute -- #727 R2 finding).
  select.textContent = '';
  const allOption = document.createElement('option');
  allOption.value = '';
  allOption.textContent = 'All families';
  select.appendChild(allOption);
  for (const name of names) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }

  select.value = names.includes(previous) ? previous : '';
  select.onchange = () => renderFamilyLeaderboard(families, select.value);
  renderFamilyLeaderboard(families, select.value);
}

function renderFamilyLeaderboard(families, familyName) {
  const el = document.getElementById('familyLeaderboard');
  if (!el) return;
  if (!familyName) {
    el.innerHTML = '';
    return;
  }
  const family = families[familyName];
  const rows = (family && family.leaderboard) || [];
  if (rows.length === 0) {
    el.innerHTML = `<div class="ci-unknown">No rated entrants for "${escapeHtml(familyName)}" yet.</div>`;
    return;
  }
  el.innerHTML = `<table><thead><tr><th>Rank</th><th>Entrant</th><th>Rating</th><th>W-L-D</th></tr></thead><tbody>` +
    rows.map(r => `<tr><td>#${r.rank}</td><td><b>${escapeHtml(r.entrant)}</b></td>` +
      `<td>${r.rating.toFixed(1)}</td><td>${r.wins}-${r.losses}-${r.draws}</td></tr>`).join('') +
    `</tbody></table>`;
}

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = String(value);
  return div.innerHTML;
}
