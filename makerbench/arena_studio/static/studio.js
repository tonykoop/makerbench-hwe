    let currentRun = '';
    let currentPair = null;
    let allTasks = [];
    let activeFamilyFilter = 'all';

    function switchTab(tabId, el) {
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

      const btn = el || document.querySelector(`[data-tab="${tabId}"]`);
      if (btn) btn.classList.add('active');

      const pane = document.getElementById('pane-' + tabId);
      if (pane) pane.classList.add('active');

      if (tabId === 'launcher') initLauncher();
      if (tabId === 'arena') loadQueue();
      if (tabId === 'leaderboard') { loadLeaderboard(); loadAgreementStudioExtras(); }
      if (tabId === 'tasks') { loadTasks(); loadDoeWhatIf(); }
    }

    async function init() {
      const res = await fetch('/api/runs');
      const data = await res.json();
      const sel = document.getElementById('runSelect');
      sel.innerHTML = '';
      if (data.runs.length === 0) {
        sel.innerHTML = '<option value="">No runs found</option>';
      } else {
        data.runs.forEach(r => {
          const opt = document.createElement('option');
          opt.value = r.run_id;
          opt.textContent = `${r.run_id} (${r.votes_count} votes, ${r.models.length} models)`;
          sel.appendChild(opt);
        });
        currentRun = data.runs[0].run_id;
      }

      // Fetch all tasks once
      try {
        const taskRes = await fetch('/api/tasks');
        const taskData = await taskRes.json();
        allTasks = taskData.tasks || [];
      } catch (e) {
        console.error('Failed to preload tasks', e);
      }

      loadOverview();
    }

    function onRunChanged() {
      currentRun = document.getElementById('runSelect').value;
      loadOverview();
    }

    async function loadOverview() {
      if (!currentRun) return;
      const res = await fetch(`/api/runs/${currentRun}/summary`);
      const data = await res.json();
      document.getElementById('statVotes').textContent = data.votes_count;
      document.getElementById('statModels').textContent = data.models ? data.models.length : 0;
      document.getElementById('statInstruments').textContent = data.instruments ? data.instruments.length : 0;
      document.getElementById('statTrials').textContent = data.trials_count;
      document.getElementById('runConfigJson').textContent = JSON.stringify(data.config || {}, null, 2);

      loadLeaderboard();
    }

    /* Launcher Logic */
    async function initLauncher() {
      if (!allTasks || allTasks.length === 0) {
        try {
          const taskRes = await fetch('/api/tasks');
          const taskData = await taskRes.json();
          allTasks = taskData.tasks || [];
        } catch (e) {
          console.error('Failed to fetch tasks', e);
        }
      }
      if (!document.getElementById('launchRunId').value) {
        const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
        document.getElementById('launchRunId').value = `rounds_${today}_opus5_fable`;
      }
      renderLauncherTasks();

      // Pre-check 3 initial tasks if none selected
      if (document.querySelectorAll('input[name="launchTask"]:checked').length === 0) {
        const cbs = document.querySelectorAll('input[name="launchTask"]');
        for (let i = 0; i < Math.min(3, cbs.length); i++) {
          cbs[i].checked = true;
        }
        updateTaskCount();
      }
    }

    function filterTasksByFamily(family, el) {
      activeFamilyFilter = family;
      document.querySelectorAll('#familyPills .pill').forEach(p => p.classList.remove('active'));
      if (el) el.classList.add('active');
      renderLauncherTasks();
    }

    function renderLauncherTasks() {
      const query = (document.getElementById('taskSearch').value || '').toLowerCase();
      const list = document.getElementById('launcherTaskList');
      list.innerHTML = '';

      const filtered = allTasks.filter(t => {
        const matchesFam = activeFamilyFilter === 'all' || t.family === activeFamilyFilter;
        const matchesQ = !query || t.id.toLowerCase().includes(query) || (t.display_name || '').toLowerCase().includes(query);
        return matchesFam && matchesQ;
      });

      filtered.forEach(t => {
        const div = document.createElement('div');
        div.className = 'task-item';
        div.onclick = (e) => {
          inspectTask(t.id);
          if (e.target.tagName !== 'INPUT') {
            const cb = div.querySelector('input[type="checkbox"]');
            cb.checked = !cb.checked;
            updateTaskCount();
          }
        };
        div.innerHTML = `
          <label style="pointer-events: none;">
            <input type="checkbox" name="launchTask" value="${t.id}" onchange="updateTaskCount()" style="pointer-events: auto;">
            <span><b>${t.display_name || t.id}</b> <small style="color: var(--text-muted);">(${t.id})</small></span>
          </label>
          <span class="badge badge-sub">🖼️ Ref Ready</span>
        `;
        list.appendChild(div);
      });
      updateTaskCount();
      if (filtered.length > 0 && !activeInspectTask) {
        inspectTask(filtered[0].id);
      }
    }

    let activeInspectTask = null;

    async function inspectTask(taskId) {
      activeInspectTask = taskId;
      try {
        const res = await fetch(`/api/tasks/${taskId}/reference`);
        const data = await res.json();
        document.getElementById('refTaskName').innerHTML = `<b>${data.task_id}</b> (${data.family}, ${data.envelope_mm.join('×')}mm)`;
        const badge = document.getElementById('refStatusBadge');
        if (data.approved) {
          badge.className = 'badge badge-sub';
          badge.textContent = 'Approved';
        } else {
          badge.className = 'badge badge-paused';
          badge.textContent = 'Needs Inspection';
        }
      } catch (e) {
        console.error('Failed to inspect task reference', e);
      }
    }

    async function approveActiveReference() {
      if (!activeInspectTask) {
        const checked = document.querySelector('input[name="launchTask"]:checked');
        if (checked) activeInspectTask = checked.value;
        else return;
      }
      await fetch(`/api/tasks/${activeInspectTask}/approve?approved=true`, { method: 'POST' });
      inspectTask(activeInspectTask);
      alert(`Visual reference for '${activeInspectTask}' marked as APPROVED.`);
    }

    async function copyAgyPrompt() {
      if (!activeInspectTask) {
        const checked = document.querySelector('input[name="launchTask"]:checked');
        if (checked) activeInspectTask = checked.value;
        else return;
      }
      const res = await fetch(`/api/tasks/${activeInspectTask}/prompt-reference`);
      const data = await res.json();
      navigator.clipboard.writeText(data.prompt_cmd);
      alert('Copied agy generation command to clipboard:\n\n' + data.prompt_cmd);
    }

    function updateTaskCount() {
      const checked = document.querySelectorAll('input[name="launchTask"]:checked').length;
      document.getElementById('taskSelectionCount').textContent = `${checked} selected`;
    }

    function selectAllTasks(selectAll) {
      document.querySelectorAll('input[name="launchTask"]').forEach(cb => cb.checked = selectAll);
      updateTaskCount();
    }

    function applyPreset(preset) {
      if (preset === 'opus_fable') {
        document.querySelectorAll('input[name="launchModel"]').forEach(cb => {
          cb.checked = (cb.value === 'claude-opus-5' || cb.value === 'cadam-fable-5.1');
        });
        document.getElementById('launchBackend').value = 'openscad';
        document.getElementById('launchTier').value = 'image';
        document.getElementById('launchRunId').value = `rounds_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}_opus5_vs_fable`;
        selectAllTasks(false);
        const preferred = ['trumpet-sheetmetal', 'hammered-dulcimer', 'ocarina'];
        document.querySelectorAll('input[name="launchTask"]').forEach(cb => {
          if (preferred.includes(cb.value)) cb.checked = true;
        });
        updateTaskCount();
      } else if (preset === 'solidworks_live') {
        document.getElementById('launchBackend').value = 'solidworks-live';
        document.getElementById('launchTier').value = 'image';
        document.querySelectorAll('input[name="launchModel"]').forEach(cb => {
          cb.checked = (cb.value === 'claude-opus-5' || cb.value === 'codex');
        });
        document.getElementById('launchRunId').value = `rounds_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}_sw_live`;
      } else if (preset === 'fusion_live') {
        document.getElementById('launchBackend').value = 'fusion-live';
        document.getElementById('launchTier').value = 'image';
        document.querySelectorAll('input[name="launchModel"]').forEach(cb => {
          cb.checked = (cb.value === 'claude-opus-5' || cb.value === 'agy-gemini');
        });
        document.getElementById('launchRunId').value = `rounds_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}_fusion_live`;
      }
    }

    async function dispatchCompetition() {
      const runId = document.getElementById('launchRunId').value.trim() || `rounds_${Date.now()}`;
      const tasks = Array.from(document.querySelectorAll('input[name="launchTask"]:checked')).map(cb => cb.value);
      const models = Array.from(document.querySelectorAll('input[name="launchModel"]:checked')).map(cb => cb.value);
      const backend = document.getElementById('launchBackend').value;
      const tier = document.getElementById('launchTier').value;
      const levels = Array.from(document.querySelectorAll('input[name="launchLevel"]:checked')).map(cb => cb.value);
      const concurrency = parseInt(document.getElementById('launchConcurrency').value, 10);
      const maxTurns = parseInt(document.getElementById('launchMaxTurns').value, 10);
      const timeout = parseInt(document.getElementById('launchTimeout').value, 10);
      const seed = parseInt(document.getElementById('launchSeed').value, 10);

      if (tasks.length === 0) {
        alert('Please select at least one task/instrument to compete.');
        return;
      }
      if (models.length === 0) {
        alert('Please select at least one model entrant.');
        return;
      }

      const terminal = document.getElementById('launchTerminal');
      terminal.textContent = `[${new Date().toISOString()}] Dispatching competition '${runId}'...\n`;

      const payload = {
        run_id: runId,
        instruments: tasks,
        models: models,
        backend: backend,
        context_tier: tier,
        levels: levels,
        concurrency: concurrency,
        max_turns: maxTurns,
        timeout_s: timeout,
        seed: seed
      };

      try {
        const res = await fetch('/api/competitions/launch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!data.success) {
          terminal.textContent += `[GATEKEEPER WARNING] ${data.error}\n`;
          alert(data.error);
          return;
        }
        terminal.textContent += `[OK] ${data.message}\n`;
        terminal.textContent += `Run Path: ${data.path}\n`;
        terminal.textContent += `Streaming active job logs...\n\n`;

        const pollInterval = setInterval(async () => {
          const logRes = await fetch(`/api/competitions/${runId}/logs?tail=30`);
          const logData = await logRes.json();
          if (logData.lines && logData.lines.length > 0) {
            terminal.textContent = logData.lines.join('\n');
            terminal.scrollTop = terminal.scrollHeight;
          }
        }, 2000);

        setTimeout(init, 3000);
      } catch (err) {
        terminal.textContent += `[ERROR] Failed to launch: ${err.message}\n`;
      }
    }

    /* Dual-Mode Viewport & WebGL Check (Story #698) */
    let webglSupported = false;
    let activeViewerMode = 'turntable';

    function checkWebGLSupport() {
      try {
        const canvas = document.createElement('canvas');
        webglSupported = !!(window.WebGL2RenderingContext && (canvas.getContext('webgl2') || canvas.getContext('experimental-webgl2')));
      } catch (e) {
        webglSupported = false;
      }
      return webglSupported;
    }

    function setViewerMode(mode) {
      if (mode === 'webgl') {
        if (!checkWebGLSupport()) {
          document.getElementById('webglNotice').style.display = 'block';
          document.getElementById('webglNotice').textContent = '⚠️ WebGL2 hardware acceleration is unavailable in current browser/RDP session. Active mode: zero-WebGL 24-frame turntable.';
          return;
        }
      }
      activeViewerMode = mode;
      document.getElementById('webglNotice').style.display = 'none';
      document.getElementById('btnTurntableMode').classList.toggle('active', mode === 'turntable');
      document.getElementById('btnWebglMode').classList.toggle('active', mode === 'webgl');
      if (currentPair) {
        setupTurntable('imgLeft', currentPair.left.frames, currentPair.left.render_path);
        setupTurntable('imgRight', currentPair.right.frames, currentPair.right.render_path);
      }
    }

    /* Agreement Studio & Analytics (Story #699) */
    function renderScatterPlot(rankings, rho) {
      const svg = document.getElementById('scatterSvg');
      if (!svg || !rankings || rankings.length === 0) return;

      const valid = rankings.filter(r => r.subjective_elo != null && r.objective_pass_rate != null);
      if (valid.length === 0) {
        svg.innerHTML = '<text x="230" y="110" fill="#94a3b8" text-anchor="middle" font-size="12">Insufficient dual-scoreline data to plot scatter.</text>';
        return;
      }

      const minElo = Math.min(...valid.map(r => r.subjective_elo)) - 50;
      const maxElo = Math.max(...valid.map(r => r.subjective_elo)) + 50;
      const eloSpan = Math.max(1, maxElo - minElo);

      const padL = 45, padR = 25, padT = 20, padB = 35;
      const w = 460 - padL - padR;
      const h = 220 - padT - padB;

      let html = `
        <line x1="${padL}" y1="${padT + h}" x2="${padL + w}" y2="${padT + h}" stroke="#263345" stroke-width="1.5" />
        <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + h}" stroke="#263345" stroke-width="1.5" />
        <line x1="${padL + w/2}" y1="${padT}" x2="${padL + w/2}" y2="${padT + h}" stroke="#263345" stroke-dasharray="3,3" />
        <line x1="${padL}" y1="${padT + h/2}" x2="${padL + w}" y2="${padT + h/2}" stroke="#263345" stroke-dasharray="3,3" />
        <text x="${padL + w/2}" y="${220 - 8}" fill="#94a3b8" font-size="10" text-anchor="middle">Objective Pass Rate (%) →</text>
        <text x="12" y="${padT + h/2}" fill="#94a3b8" font-size="10" text-anchor="middle" transform="rotate(-90 12,${padT + h/2})">Elo →</text>
      `;

      valid.forEach((row, i) => {
        const x = padL + (row.objective_pass_rate * w);
        const y = padT + h - (((row.subjective_elo - minElo) / eloSpan) * h);
        const color = i % 2 === 0 ? '#38bdf8' : '#86efac';
        html += `
          <g>
            <circle cx="${x}" cy="${y}" r="6" fill="${color}" stroke="#0f141c" stroke-width="1.5">
              <title>${row.entrant}: Elo ${row.subjective_elo.toFixed(1)}, Pass Rate ${(row.objective_pass_rate * 100).toFixed(0)}%</title>
            </circle>
            <text x="${x + 8}" y="${y + 4}" fill="#e2e8f0" font-size="10" font-weight="600">${row.entrant}</text>
          </g>
        `;
      });

      svg.innerHTML = html;
    }

    async function loadLeaderboard() {
      if (!currentRun) return;
      const [eloRes, agrRes] = await Promise.all([
        fetch(`/api/runs/${currentRun}/leaderboard`),
        fetch(`/api/runs/${currentRun}/agreement`)
      ]);
      const eloData = await eloRes.json();
      const agrData = await agrRes.json();

      const eloBody = document.querySelector('#tableElo tbody');
      eloBody.innerHTML = '';
      (eloData.leaderboard || []).forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>#${row.rank}</td><td><b>${row.entrant}</b></td><td>${row.rating.toFixed(1)}</td><td>${row.wins}-${row.losses}-${row.draws}</td><td>${row.games}</td>`;
        eloBody.appendChild(tr);
      });

      const unrated = eloData.unrated_entrants || [];
      const unratedBox = document.getElementById('unratedContainer');
      if (unrated.length > 0) {
        unratedBox.style.display = 'block';
        document.getElementById('unratedList').innerHTML = unrated.map(e => `<span class="badge" style="background:#1e293b; color:#94a3b8; margin-right:4px;">${e}</span>`).join(' ');
      } else {
        unratedBox.style.display = 'none';
      }

      const agrBody = document.querySelector('#tableAgreement tbody');
      agrBody.innerHTML = '';
      let rhoVal = null;
      if (agrData.agreement && agrData.agreement.rho !== undefined) {
        rhoVal = agrData.agreement.rho;
      } else if (agrData.pairwise_correlations && agrData.pairwise_correlations['subjective_x_objective'] !== undefined) {
        rhoVal = agrData.pairwise_correlations['subjective_x_objective'];
      }
      document.getElementById('agreementMetric').textContent = `Spearman Rank Correlation (ρ): ${rhoVal !== null ? rhoVal.toFixed(3) : 'N/A'}`;

      (agrData.rankings || []).forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td><b>${row.entrant}</b></td><td>${row.subjective_rank ?? '—'}</td><td>${row.objective_rank ?? '—'}</td><td>${row.objective_pass_rate !== null ? (row.objective_pass_rate * 100).toFixed(0) + '%' : '—'}</td>`;
        agrBody.appendChild(tr);
      });

      renderScatterPlot(agrData.rankings, rhoVal);
    }

    async function exportWinnersAction() {
      if (!currentRun) return;
      try {
        const res = await fetch(`/api/runs/${currentRun}/export-winners`, { method: 'POST' });
        const data = await res.json();
        alert(`Exported ${data.exported_count} winning CAD models into instruments/ repository!`);
      } catch (e) {
        alert('Failed to export winners: ' + e.message);
      }
    }

    function exportReportAction() {
      if (!currentRun) return;
      window.open(`/api/runs/${currentRun}/export-report?format=markdown`, '_blank');
    }

    async function loadQueue() {
      if (!currentRun) return;
      const res = await fetch(`/api/runs/${currentRun}/queue`);
      const data = await res.json();
      document.getElementById('voteProgress').textContent = `Voted ${data.done} of ${data.total} pairs`;
      if (!data.has_next) {
        document.getElementById('voteStage').innerHTML = '<div style="margin: auto; text-align: center; padding: 60px;"><h3>All queued pairs voted! 🎉</h3><p style="margin-top: 8px; color: var(--text-muted);">Check the Leaderboard tab to see updated Elo ratings.</p></div>';
        return;
      }
      currentPair = data.current_pair;
      setupTurntable('imgLeft', currentPair.left.frames, currentPair.left.render_path);
      setupTurntable('imgRight', currentPair.right.frames, currentPair.right.render_path);
    }

    function setupTurntable(imgId, frames, fallback) {
      const img = document.getElementById(imgId);
      if (frames && frames.length > 0) {
        let frameIdx = 0;
        img.src = frames[frameIdx];
        let dragging = false;
        let startX = 0;
        const container = img.parentElement;
        container.onmousedown = (e) => { dragging = true; startX = e.clientX; };
        window.onmouseup = () => { dragging = false; };
        container.onmousemove = (e) => {
          if (!dragging) return;
          const delta = e.clientX - startX;
          if (Math.abs(delta) > 15) {
            frameIdx = (frameIdx + (delta > 0 ? 1 : -1) + frames.length) % frames.length;
            img.src = frames[frameIdx];
            startX = e.clientX;
          }
        };
      } else {
        img.src = fallback || '';
      }
    }

    async function castVote(winner) {
      if (!currentPair) return;
      const leftFlags = Array.from(document.querySelectorAll('#flagsLeft input:checked')).map(cb => cb.value);
      const rightFlags = Array.from(document.querySelectorAll('#flagsRight input:checked')).map(cb => cb.value);

      await fetch(`/api/runs/${currentRun}/vote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pair_id: currentPair.pair_id,
          winner: winner,
          voter: 'tony',
          flags: { left: leftFlags, right: rightFlags }
        })
      });

      document.querySelectorAll('.flags-box input').forEach(cb => cb.checked = false);
      loadQueue();
    }

    async function loadTasks() {
      const res = await fetch('/api/tasks');
      const data = await res.json();
      const tbody = document.querySelector('#tableTasks tbody');
      tbody.innerHTML = '';
      data.tasks.forEach(t => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td><code>${t.id}</code></td><td><b>${t.display_name}</b></td><td><span class="badge badge-sub">${t.family}</span></td><td>${t.task_kind}</td><td>${(t.envelope_mm || []).join(' × ')}</td>`;
        tbody.appendChild(tr);
      });
    }

    document.addEventListener('keydown', (e) => {
      if (!currentPair || e.target.tagName === 'INPUT') return;
      if (e.key === 'l' || e.key === 'ArrowLeft') castVote('left');
      if (e.key === 'd' || e.key === 'ArrowDown') castVote('draw');
      if (e.key === 'r' || e.key === 'ArrowRight') castVote('right');
    });

    window.onload = init;
