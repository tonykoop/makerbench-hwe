"""Confined, bounded Live snapshots. No model calls or secret-file reads."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .. import nightly_cad
from ..run_log_io import atomic_write_json, file_lock, live_status_rows
from . import control_room as cr

MAX_BYTES = 2 * 1024 * 1024


def safe(path: Path) -> Path:
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Live paths must not pass through symlinks")
    return path


def read(path: Path) -> dict:
    safe(path)
    if not path.exists():
        return {}
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Live metadata exceeds the read limit")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Invalid Live metadata")
    return value


def clean(text) -> str:
    text = str(text or "")
    for key, value in os.environ.items():
        if value and key.endswith(('_TOKEN', '_KEY', '_PASSWORD')):
            text = text.replace(value, '[redacted]')
    text = re.sub(r'(?i)(bearer\s+|(?:token|api_key|password)\s*[=:]\s*)\S+', r'\1[redacted]', text)
    return re.sub(r'(?:[A-Za-z]:[\\/]|/)[^\s"\']+', '[path]', text)


def elapsed(start):
    try:
        return max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds()))
    except (ValueError, TypeError, AttributeError):
        return None


class LiveStore:
    def __init__(self, root, queue, repo):
        self.root, self.queue, self.repo = map(Path, (root, queue, repo))

    def folders(self):
        safe(self.root)
        if not self.root.exists():
            return []
        # Match both direct scheduled runs and the legacy runs/nightly layout.
        candidates = list(self.root.iterdir())
        legacy = self.root / 'nightly'
        if legacy.is_dir() and not legacy.is_symlink():
            candidates += list(legacy.iterdir())
        return sorted((p for p in candidates if p.is_dir() and not p.is_symlink()
                       and not p.name.startswith('.') and (p / 'nightly-state.json').is_file()), reverse=True)

    def folder(self, run_id):
        return next((p for p in self.folders() if p.name == run_id), None)

    def artifact(self, run, raw):
        if not isinstance(raw, str) or not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            qualified = self.repo / path
            path = qualified if qualified.absolute().is_relative_to(run.absolute()) else run / path
        safe(path)
        return path if path.resolve().is_relative_to(run.resolve()) and path.is_file() else None

    def transcript(self, run, entrant, row):
        paths = []
        gen = (row.get('result') or {}).get('gen') or {}
        for key in ('transcript_path',):
            p = self.artifact(run, gen.get(key))
            if p:
                paths.append(p)
        # Entrant IDs from metadata are never used as glob patterns.
        gen_root = run / 'gen'
        if gen_root.is_dir():
            safe(gen_root)
            for directory in gen_root.iterdir():
                if directory.name.endswith('__' + entrant):
                    safe(directory)
                    paths.extend(p for p in directory.glob('build_transcript.txt') if p.is_file())
        if re.fullmatch(r'[A-Za-z0-9_.-]+', entrant) and entrant not in {'.', '..'}:
            stream = run / 'cadam' / entrant / 'cadam-stream.bin'
            if stream.is_file():
                paths.append(safe(stream))
        if paths:
            path = safe(paths[0])
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - MAX_BYTES))
                return clean(stream.read(MAX_BYTES).decode(errors='replace'))
        return clean(row.get('error') or (row.get('result') or {}).get('error') or
                     json.dumps({'status': row.get('status', 'queued'), 'turns': (row.get('result') or {}).get('turns', [])}))

    def view(self):
        queue = read(self.queue)
        jobs = queue.get('jobs') or []
        folders = self.folders()
        states = [(p, read(p / 'nightly-state.json')) for p in folders]
        current = next(((p, s) for p, s in states if s.get('status') == 'running'), None)
        if current is None:
            return {'job': None, 'next': [j.get('instrument_id') for j in jobs if j.get('status', 'queued') == 'queued']}
        run, state = current
        log = read(run / 'run_log.json')
        config = log.get('config') or {}
        job = next((j for j in jobs if j.get('job_id') == state.get('job_id')), {})
        entrants = {e['entrant_id']: e for e in job.get('entrants', [])}
        ids = config.get('model_ids') or list(entrants)
        rows = {r.get('model_id'): r for r in live_status_rows(log)}
        active = state.get('active_entrant') or next((eid for eid in ids if eid not in rows), None)
        lanes = []
        for eid in ids:
            row, entry = rows.get(eid, {}), entrants.get(eid, {})
            result = row.get('result') or {}
            status = ('scored' if row.get('status') == 'scored' else 'failed') if row else ('running' if eid == active else 'queued')
            route = (row.get('meta') or {}).get('route')
            if not route and entry:
                route = nightly_cad.route_label(nightly_cad.NightlyEntrant.from_dict(entry))
            turns = result.get('turns') or (row.get('meta') or {}).get('turns') or []
            if not turns and status == 'running':
                gen_root = run / 'gen'
                if gen_root.is_dir():
                    safe(gen_root)
                    for directory in gen_root.iterdir():
                        if directory.name.endswith('__' + eid):
                            safe(directory)
                            turns = [read(p) for p in sorted(directory.glob('turn-*/turn.json'))]
            timeline = [{'turn': t.get('turn', i + 1), 'state': 'ok' if (t.get('objective') or {}).get('passed') else 'bad'} for i, t in enumerate(turns)]
            if not timeline and row:
                timeline = [{'turn': 1, 'state': 'ok' if (result.get('objective') or {}).get('passed') else 'bad'}]
            if status == 'running':
                timeline.append({'turn': len(timeline) + 1, 'state': 'now'})
            for n in range(len(timeline) + 1, (min(int(job.get('turns', 1)), 10) + 1) if status in {'queued', 'running'} else 0):
                timeline.append({'turn': n, 'state': 'todo'})
            artifacts = result.get('artifacts') or {}
            preview = self.artifact(run, artifacts.get('png_path') or next((t.get('render_path') for t in reversed(turns) if t.get('render_path')), None))
            native = self.artifact(run, result.get('native_path'))
            text = self.transcript(run, eid, row)
            lanes.append({'id': eid, 'trial_id': row.get('trial_id'), 'state': status, 'route': clean(route or eid),
                          'turns': timeline, 'elapsed_s': row.get('wall_time_s') if row else elapsed(state.get('entrant_started_utc')) if status == 'running' else None,
                          'cost_source': 'CLI subscription' if str(route).startswith(('Codex', 'Claude Code')) else 'recorded charge' if eid in cr._charges(state) else 'not recorded',
                          'cost_usd': cr._charges(state).get(eid), 'tail': text.splitlines()[-6:],
                          'has_preview': preview is not None, 'preview_path': preview.relative_to(run).as_posix() if preview else None, 'has_native': native is not None,
                          'inferred_active': status == 'running' and not state.get('active_entrant'), 'backend': entry.get('backend'), 'live': entry.get('kind') == 'live'})
        lock = read(self.root / '.nightly-cad.lock')
        pid = lock.get('pid')
        alive = None
        if type(pid) is int and pid > 0 and lock.get('host') == socket.gethostname():
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = None
        lights = [{'label': f'{name.title()} CLI', 'detail': 'on PATH · sign-in unknown' if shutil.which(name) else 'not on PATH'} for name in ('claude', 'codex')]
        for binary in ('FreeCAD', 'openscad', 'blender'):
            lights.append({'label': binary, 'detail': 'on PATH' if shutil.which(binary) else 'not on PATH · configured runtimes may differ'})
        for backend, label in [('fusion-live', 'Fusion seat'), ('solidworks-live', 'SolidWorks seat')]:
            building = any(lane['state'] == 'running' and lane['backend'] == backend for lane in lanes)
            lights.append({'label': label, 'detail': "building · don't touch" if building else 'idle / presence unknown'})
        tonight = [(p, s) for p, s in states if p.name[:8] == run.name[:8]]
        night_rows = [r for p, _ in tonight for r in live_status_rows(read(p / 'run_log.json'))]
        return {'job': {'run_id': run.name, 'job_id': state.get('job_id'), 'instrument': job.get('instrument_id') or (config.get('instrument_ids') or ['Unknown'])[0],
                        'deliverable': job.get('deliverable', 'single'), 'position': next((i + 1 for i, j in enumerate(jobs) if j is job), None), 'total': len(jobs),
                        'started': state.get('started_utc') or cr._night_stamp(run.name), 'elapsed_s': elapsed(state.get('started_utc') or cr._night_stamp(run.name)),
                        'finished': len(rows), 'spend': (state.get('budget') or {}).get('spent_usd'), 'cap': (state.get('budget') or {}).get('limit_usd'),
                        'stop_requested': state.get('stop_requested') is True}, 'lanes': lanes, 'lights': lights,
                'next': [j.get('instrument_id') for j in jobs if j.get('status', 'queued') == 'queued'],
                'lock': {'pid': pid, 'alive': alive},
                'totals': {'jobs_done': sum(s.get('status') in {'votable', 'complete'} for _, s in tonight),
                           'scored': sum(r.get('status') == 'scored' for r in night_rows), 'valid': sum((r.get('result') or {}).get('render_ok') is True for r in night_rows), 'failed': sum(r.get('status') != 'scored' for r in night_rows)}}

    def stop(self, run_id):
        safe(self.queue)
        safe(self.queue.with_name(self.queue.name + '.lock'))
        with file_lock(self.queue):
            run = self.folder(run_id)
            if run is None:
                raise ValueError('Unknown run')
            path = run / 'nightly-state.json'
            state = read(path)
            if state.get('status') != 'running':
                raise ValueError('This run is no longer running')
            state['stop_requested'] = True
            atomic_write_json(path, state)
        return {'stop_requested': True}


def capture(backend):
    """Bridge capture_view is GET /view. Never stage/confirm or change the camera."""
    prefix, port = ('HWE_FUSION', '8766') if backend == 'fusion-live' else ('HWE_SW', '8767')
    host = os.environ.get(prefix + '_HOST', '127.0.0.1')
    port = os.environ.get(prefix + '_PORT', port)
    req = urllib.request.Request(f'http://{host}:{port}/view?width=640&height=480')
    token = os.environ.get(prefix + '_TOKEN')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read(8 * MAX_BYTES + 1)
    if len(raw) > 8 * MAX_BYTES:
        raise ValueError('Capture exceeds the size limit')
    data = json.loads(raw)
    image = base64.b64decode(data['image_base64'], validate=True)
    if not image.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Bridge did not return a PNG capture')
    return image
