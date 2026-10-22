"""Live fixtures mirror scheduled state/config/row shapes, with synthetic content only."""
import json
import os
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio import control_room_live as live
from makerbench import nightly_cad
from makerbench.run_log_io import atomic_write_json


def build_live_repo(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(root / 'registry.json', {'instruments': []})
    run = root / 'runs' / '20261022T062220Z-fixture-udu'
    run.mkdir(parents=True, exist_ok=True)
    entrants = [dict(entrant_id=e, kind='live' if e == 'running' else 'arena', model_id='stub', backend='fusion-live' if e == 'running' else 'freecad') for e in ('scored', 'failed', 'running', 'queued')]
    atomic_write_json(root / 'runs' / 'nightly-cad-queue.json', {'schema': nightly_cad.SCHEMA, 'jobs': [
        {'job_id': 'fixture-udu', 'instrument_id': 'udu', 'status': 'running', 'turns': 3, 'entrants': entrants},
        {'job_id': 'next', 'instrument_id': 'kena', 'status': 'queued', 'entrants': []}]})
    atomic_write_json(run / 'nightly-state.json', {'schema': nightly_cad.STATE_SCHEMA, 'job_id': 'fixture-udu', 'run_id': run.name, 'status': 'running', 'active_entrant': 'running', 'started_utc': '2026-10-22T06:22:20+00:00', 'budget': {'spent_usd': 0, 'limit_usd': 5, 'charges': [{'entrant_id': 'scored', 'cost_usd': 0}]}})
    Image.new('RGB', (300, 160), '#526a87').save(run / 'preview.png')
    (run / 'fixture.FCStd').write_bytes(b'synthetic native')
    atomic_write_json(run / 'run_log.json', {'config': {'model_ids': [e['entrant_id'] for e in entrants], 'instrument_ids': ['udu']}, 'trials': [
        {'trial_id': 'fixture__scored', 'model_id': 'scored', 'status': 'scored', 'wall_time_s': 46, 'meta': {'route': 'Codex · fixture · FreeCAD'}, 'result': {'render_ok': True, 'native_path': 'fixture.FCStd', 'artifacts': {'png_path': 'preview.png'}, 'objective': {'passed': True}}},
        {'trial_id': 'fixture__failed', 'model_id': 'failed', 'status': 'error', 'error': 'compile failed <script>bad()</script>', 'result': {}}]})
    transcript = run / 'gen' / 'udu__seed0__rep0__running' / 'build_transcript.txt'
    transcript.parent.mkdir(parents=True)
    transcript.write_text('fixture: build started\nfixture: waiting for export\n')
    atomic_write_json(root / 'runs' / '.nightly-cad.lock', {'pid': os.getpid(), 'host': socket.gethostname()})
    return root


@pytest.fixture
def repo(tmp_path):
    return build_live_repo(tmp_path)


def client(repo, **kwargs):
    return TestClient(create_studio_app(repo_root=repo, registry_path=repo / 'registry.json', **kwargs), base_url='http://127.0.0.1')


def test_midflight_read_only_and_actions(repo, monkeypatch):
    opened = []
    api = client(repo, native_opener=opened.append)
    before = {p: p.read_bytes() for p in (repo / 'runs').rglob('*') if p.is_file()}
    data = api.get('/api/live').json()
    assert [lane['state'] for lane in data['lanes']] == ['scored', 'failed', 'running', 'queued']
    assert data['job']['finished'] == 2 and data['next'] == ['kena']
    assert data['lock']['alive'] is True
    assert data['lanes'][2]['tail'][-1] == 'fixture: waiting for export'
    assert data['lanes'][0]['turns'][0]['state'] == 'ok'
    assert all(p.read_bytes() == value for p, value in before.items())
    base = '/api/live/' + data['job']['run_id']
    assert api.get(base + '/render/scored').headers['content-type'] == 'image/png'
    assert api.get(base + '/transcript/running').json()['text'].startswith('fixture:')
    assert api.post(base + '/open/scored', headers={'Origin':'http://127.0.0.1'}).status_code == 200
    assert opened[0].name == 'fixture.FCStd'
    calls = []
    monkeypatch.setattr(live, 'capture', lambda backend: calls.append(backend) or b'png fixture')
    assert api.post(base + '/capture/queued', headers={'Origin':'http://127.0.0.1'}).status_code == 409
    assert api.post(base + '/capture/running', headers={'Origin':'http://127.0.0.1'}).status_code == 200
    assert calls == ['fusion-live']
    assert api.post(base + '/stop', headers={'Origin':'http://127.0.0.1'}).status_code == 403
    writable = client(repo, control_room_queue_writes=True)
    assert writable.post(base + '/stop', headers={'Origin':'http://evil.example'}).status_code == 403
    assert writable.post(base + '/stop', headers={'Origin':'http://127.0.0.1'}).status_code == 200
    assert writable.get('/api/live').json()['job']['stop_requested']


@pytest.mark.parametrize('target', ['nightly-state.json', 'run_log.json', 'preview.png', 'gen'])
def test_symlink_refused(repo, tmp_path, target):
    run = repo / 'runs' / '20261022T062220Z-fixture-udu'
    path = run / target
    moved = tmp_path / ('outside-' + path.name)
    path.rename(moved)
    path.symlink_to(moved)
    assert client(repo).get('/api/live').status_code == 409


def test_path_redaction_and_streamed_turn(repo):
    run = repo / 'runs' / '20261022T062220Z-fixture-udu'
    folder = run / 'gen' / 'udu__seed0__rep0__running' / 'turn-1'
    folder.mkdir()
    atomic_write_json(folder / 'turn.json', {'turn': 1, 'objective': {'passed': False}, 'render_path': 'preview.png'})
    (folder.parent / 'build_transcript.txt').write_text('Authorization: Bearer secret\n/tmp/secret source\n<script>bad</script>')
    lane = client(repo).get('/api/live').json()['lanes'][2]
    assert [t['state'] for t in lane['turns']] == ['bad', 'now', 'todo']
    assert lane['has_preview'] and 'secret' not in '\n'.join(lane['tail'])


def test_runner_finishes_active_then_stops_and_preserves_flag(tmp_path):
    from test_nightly_cad import _queue, _image, _registry, _synthetic_handler
    queue = _queue(tmp_path / 'queue.json', _image(tmp_path / 'ref.png'))
    registry = _registry(tmp_path / 'registry.json')
    root = tmp_path / 'runs'
    calls = []
    def handler(job, entrant, run_dir, registry):
        calls.append(entrant.entrant_id)
        live.LiveStore(root, queue, tmp_path).stop(run_dir.name)
        return _synthetic_handler(job, entrant, run_dir, registry)
    executor = nightly_cad.NightlyExecutor(queue_path=queue, registry_path=registry, output_root=root, handlers={'arena': handler, 'cadam': handler, 'live': handler})
    result = executor.run()
    assert result['status'] == 'stopped'
    assert calls == ['cadam-fable-image']
    assert [r['status'] for r in result['outcomes']] == ['complete', 'stopped', 'stopped']
    state = json.loads((Path(result['run_dir']) / 'nightly-state.json').read_text())
    assert state['stop_requested'] and state['budget']['spent_usd'] == .66
    assert (Path(result['run_dir']) / 'morning-summary.json').exists()


def test_live_keeps_freeform_progress_without_changing_blind_rows(repo):
    from makerbench.run_log_io import trial_rows
    run = repo / 'runs' / '20261022T062220Z-fixture-udu'
    log = live.read(run / 'run_log.json')
    log['trials'][0]['result']['backend'] = 'blender'
    atomic_write_json(run / 'run_log.json', log)
    assert len(trial_rows(log)) == 1
    assert client(repo).get('/api/live').json()['lanes'][0]['state'] == 'scored'


def test_resume_stopped_job_only_dispatches_unfinished_entrants(tmp_path):
    from test_nightly_cad import _queue, _image, _registry, _synthetic_handler
    queue = _queue(tmp_path / 'queue.json', _image(tmp_path / 'ref.png'))
    registry = _registry(tmp_path / 'registry.json')
    root = tmp_path / 'runs'
    calls = []
    def handler(job, entrant, run_dir, registry):
        calls.append(entrant.entrant_id)
        if len(calls) == 1:
            live.LiveStore(root, queue, tmp_path).stop(run_dir.name)
        return _synthetic_handler(job, entrant, run_dir, registry)
    executor = nightly_cad.NightlyExecutor(queue_path=queue, registry_path=registry, output_root=root, handlers={k: handler for k in ('arena', 'cadam', 'live')})
    first = executor.run()
    assert first['status'] == 'stopped'
    payload = live.read(queue)
    payload['jobs'][0]['status'] = 'queued'
    atomic_write_json(queue, payload)
    second = executor.run()
    assert calls == ['cadam-fable-image', 'codex-openscad', 'codex-live']
    assert second['outcomes'][0]['status'] == 'already-complete'
    assert second['status'] != 'stopped'
    assert not live.read(Path(second['run_dir']) / 'nightly-state.json')['stop_requested']


def test_capture_uses_only_read_only_view_endpoint(monkeypatch):
    import base64
    import io
    requests = []
    png = b'\x89PNG\r\n\x1a\nfixture'
    monkeypatch.setenv('HWE_FUSION_HOST', '127.0.0.1')
    monkeypatch.setenv('HWE_FUSION_TOKEN', 'synthetic-token')
    def respond(request, timeout):
        requests.append(request)
        return io.BytesIO(json.dumps({'image_base64': base64.b64encode(png).decode()}).encode())
    monkeypatch.setattr(live.urllib.request, 'urlopen', respond)
    assert live.capture('fusion-live') == png
    assert requests[0].get_method() == 'GET'
    assert requests[0].full_url.endswith('/view?width=640&height=480')
    assert requests[0].get_header('Authorization') == 'Bearer synthetic-token'


def test_stop_rejects_symlink_lock_and_stale_job(repo, tmp_path):
    store = live.LiveStore(repo / 'runs', repo / 'runs' / 'nightly-cad-queue.json', repo)
    lock = store.queue.with_name(store.queue.name + '.lock')
    outside = tmp_path / 'outside-lock'
    outside.write_text('unchanged')
    lock.symlink_to(outside)
    with pytest.raises(ValueError, match='symlinks'):
        store.stop('20261022T062220Z-fixture-udu')
    assert outside.read_text() == 'unchanged'
    lock.unlink()
    state = repo / 'runs' / '20261022T062220Z-fixture-udu' / 'nightly-state.json'
    atomic_write_json(state, {'status': 'stopped'})
    with pytest.raises(ValueError, match='no longer running'):
        store.stop(state.parent.name)
