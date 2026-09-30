"""Actual summary/render routes and observed timing for Studio matchups."""
import base64
import json

from fastapi.testclient import TestClient

from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio.doe import build_matchup
from makerbench import code_cad_orchestrator as orch

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def make_matchup_repo(root):
    directory = root / 'runs/code_cad_arena/matchup-fixture'
    directory.mkdir(parents=True)
    preview = build_matchup('model', ['stub-a', 'stub-b'], instruments=['ocarina'], models=['stub-a'])
    metadata = {k: preview[k] for k in ('varied_axis', 'values', 'varied_axes', 'factorial', 'held')}
    trials = []
    for n, model in enumerate(('stub-a', 'stub-b')):
        image = directory / f'preview-{n}.png'
        image.write_bytes(PNG)
        gates = {k: 1.0 for k in ('renders', 'watertight', 'nonzero_volume', 'body_count', 'fits_envelope', 'min_wall')}
        if n == 0:
            gates['min_wall'] = 0.0
        trials.append({'trial_id': f'fixture-{n}', 'model_id': model, 'instrument_id': 'ocarina', 'seed': 0, 'rep': 0,
                       'status': 'scored', **({'wall_time_s': 2.5} if n == 0 else {}),
                       'result': {'backend': 'openscad', 'artifacts': {'png_path': str(image)},
                                  'objective': {'objective_pass_rate': sum(gates.values())/6, 'sub_scores': gates}}})
    (directory / 'run_log.json').write_text(json.dumps({'config': {'model_ids': ['stub-a', 'stub-b'],
        'instrument_ids': ['ocarina'], 'matchup': metadata}, 'trials': trials}))
    (root / 'registry.json').write_text(json.dumps({'instruments': []}))
    return directory


def test_summary_and_contained_render_route(tmp_path):
    run = make_matchup_repo(tmp_path)
    client = TestClient(create_studio_app(registry_path=tmp_path/'registry.json', repo_root=tmp_path), base_url='http://127.0.0.1')
    data = client.get('/api/runs/matchup-fixture/summary').json()
    assert data['matchup']['varied_axis'] == 'models'
    assert len(data['matchup_trials']) == 2
    a, b = data['matchup_trials']
    assert a['gates']['min_wall'] == 0.0
    assert a['wall_time_s'] == 2.5 and b['wall_time_s'] is None
    image = client.get(a['render_url'])
    assert image.status_code == 200 and image.content == PNG
    assert image.headers['content-type'] == 'image/png'
    payload = json.loads((run/'run_log.json').read_text())
    outside = tmp_path/'outside.png'
    outside.write_bytes(PNG)
    payload['trials'][0]['result']['artifacts']['png_path'] = str(outside)
    (run/'run_log.json').write_text(json.dumps(payload))
    assert client.get('/api/runs/matchup-fixture/matchup-render/fixture-0').status_code == 404
    assert client.get('/api/runs/matchup-fixture/matchup-render/missing').status_code == 404


def test_render_symlink_cannot_escape_run(tmp_path):
    run = make_matchup_repo(tmp_path)
    outside = tmp_path/'outside.png'
    outside.write_bytes(PNG)
    client = TestClient(create_studio_app(registry_path=tmp_path/'registry.json', repo_root=tmp_path), base_url='http://127.0.0.1')
    assert client.get('/api/runs/matchup-fixture/matchup-render/fixture-0').status_code == 200
    (run/'preview-0.png').unlink()
    (run/'preview-0.png').symlink_to(outside)
    assert client.get('/api/runs/matchup-fixture/matchup-render/fixture-0').status_code == 404


def test_timing_is_observed_for_success_and_failure_and_retained_on_resume(tmp_path, monkeypatch):
    tick = iter([10.0, 12.5, 20.0, 25.0])
    monkeypatch.setattr(orch.time, 'monotonic', lambda: next(tick))
    config = orch.OrchestrationConfig(instrument_ids=('ocarina',), model_ids=('a', 'b'), seeds=(0,))
    def execute(trial):
        if trial.model_id == 'b':
            raise RuntimeError('fixture failure')
        return {'status': 'scored'}
    args = dict(config=config, run_log_path=tmp_path/'run.json', execute_trial=execute, clock_fn=lambda: 0)
    first = orch.run_orchestration(**args)
    assert [t['wall_time_s'] for t in first['trials']] == [2.5, 5.0]
    second = orch.run_orchestration(**args)
    assert second['trials'] == first['trials']


def test_invalid_metrics_and_legacy_runs_remain_unknown(tmp_path):
    run = make_matchup_repo(tmp_path)
    path = run/'run_log.json'
    payload = json.loads(path.read_text())
    payload['trials'][0]['wall_time_s'] = -1
    payload['trials'][0]['result']['objective']['objective_pass_rate'] = 2
    payload['trials'][0]['result']['objective']['sub_scores']['min_wall'] = -1
    path.write_text(json.dumps(payload))
    client = TestClient(create_studio_app(registry_path=tmp_path/'registry.json', repo_root=tmp_path), base_url='http://127.0.0.1')
    data = client.get('/api/runs/matchup-fixture/summary').json()
    assert data['instruments'] == ['ocarina']
    row = data['matchup_trials'][0]
    assert row['wall_time_s'] is row['objective_pass_rate'] is row['gates']['min_wall'] is None
    del payload['config']['matchup']
    path.write_text(json.dumps(payload))
    data = client.get('/api/runs/matchup-fixture/summary').json()
    assert 'matchup_trials' not in data and 'matchup' not in data
    assert client.get('/api/runs/matchup-fixture/matchup-render/fixture-0').status_code == 404


def test_declared_topology_checks_are_visible_without_inventing_undeclared_ones(tmp_path):
    run = make_matchup_repo(tmp_path)
    path = run/'run_log.json'
    payload = json.loads(path.read_text())
    objective = payload['trials'][0]['result']['objective']
    objective['sub_scores']['topology'] = 0.0
    objective['objective_pass_rate'] = 5/7
    path.write_text(json.dumps(payload))
    client = TestClient(create_studio_app(registry_path=tmp_path/'registry.json', repo_root=tmp_path), base_url='http://127.0.0.1')
    trials = client.get('/api/runs/matchup-fixture/summary').json()['matchup_trials']
    assert trials[0]['gates']['topology'] == 0.0
    assert 'interfaces' not in trials[0]['gates']
    assert 'topology' not in trials[1]['gates']
