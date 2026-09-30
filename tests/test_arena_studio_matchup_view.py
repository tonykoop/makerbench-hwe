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


def test_production_relative_render_paths_reach_mounted_studio_routes(tmp_path, monkeypatch):
    from pathlib import Path
    import trimesh
    from makerbench import code_cad_arena_runner as runner, nightly_cad
    from makerbench.code_cad_objective import RenderArtifacts
    from makerbench.code_cad_providers import make_stub_generator

    monkeypatch.chdir(tmp_path)
    run=Path('runs/code_cad_arena/relative-matchup')
    run.mkdir(parents=True)
    registry={'instruments':[{'id':'boxolin', 'task_brief':'fixture box instrument',
                             'envelope_mm':[100,100,100], 'min_bodies':1}]}
    registry_path=tmp_path/'registry.json'
    registry_path.write_text(json.dumps(registry))
    image=tmp_path/'reference.png'
    image.write_bytes(PNG)
    job=nightly_cad.NightlyJob(job_id='relative-fixture', instrument_id='boxolin',
                             reference_image=str(image), entrants=[
        nightly_cad.NightlyEntrant(entrant_id=model+'::L1', model_id=model, kind='arena')
        for model in ['stub-a','stub-b']])
    job.validate()
    preview=build_matchup('model', ['stub-a','stub-b'], instruments=['boxolin'], models=['stub-a'])
    metadata={k:preview[k] for k in ['varied_axis','values','varied_axes','factorial','held']}
    nightly_cad._fresh_run_log(run, job, matchup=metadata)

    def compiler(_source, output):
        output.mkdir(parents=True, exist_ok=True)
        stl=output/'output.stl'
        trimesh.creation.box(extents=[30,20,10]).export(stl)
        png=output/'preview.png'
        png.write_bytes(PNG)
        return RenderArtifacts(stl_path=stl, png_path=png)

    models=tuple(e.entrant_id for e in job.entrants)
    execute=runner.make_execute_trial(registry=registry, run_dir=run,
        generators={m:make_stub_generator() for m in models}, compiler=compiler, backend='openscad')
    log=orch.run_orchestration(config=orch.OrchestrationConfig(instrument_ids=('boxolin',),
        model_ids=models, seeds=(0,)), run_log_path=run/'run_log.json', execute_trial=execute)
    paths=[Path(t['result']['artifacts']['png_path']) for t in log['trials']]
    assert len(paths)==2 and all(not p.is_absolute() and p.is_file() for p in paths)
    (tmp_path/'server-cwd').mkdir()
    monkeypatch.chdir(tmp_path/'server-cwd')
    client=TestClient(create_studio_app(registry_path=registry_path, repo_root=tmp_path),
                      base_url='http://127.0.0.1')
    summary=client.get('/api/runs/relative-matchup/summary')
    assert summary.status_code==200
    rows=summary.json()['matchup_trials']
    assert len(rows)==2 and all(r['render_url'] for r in rows)
    for row in rows:
        response=client.get(row['render_url'])
        assert response.status_code==200 and response.content==PNG


def test_short_run_relative_path_uses_the_selected_run(tmp_path):
    run=make_matchup_repo(tmp_path)
    (tmp_path/'preview-0.png').write_bytes(b'not the selected PNG')
    path=run/'run_log.json'
    payload=json.loads(path.read_text())
    payload['trials'][0]['result']['artifacts']['png_path']='preview-0.png'
    path.write_text(json.dumps(payload))
    client=TestClient(create_studio_app(registry_path=tmp_path/'registry.json', repo_root=tmp_path),
                      base_url='http://127.0.0.1')
    response=client.get('/api/runs/matchup-fixture/matchup-render/fixture-0')
    assert response.status_code==200 and response.content==PNG


def test_repository_qualified_symlink_never_falls_back_to_another_in_run_file(tmp_path):
    run=make_matchup_repo(tmp_path)
    outside=tmp_path/'outside.png'
    outside.write_bytes(PNG)
    link=run/'escape.png'
    link.symlink_to(outside)
    relative=link.relative_to(tmp_path)
    alternate=run/relative
    alternate.parent.mkdir(parents=True)
    alternate.write_bytes(PNG)
    path=run/'run_log.json'
    payload=json.loads(path.read_text())
    payload['trials'][0]['result']['artifacts']['png_path']=relative.as_posix()
    path.write_text(json.dumps(payload))
    client=TestClient(create_studio_app(registry_path=tmp_path/'registry.json', repo_root=tmp_path),
                      base_url='http://127.0.0.1')
    assert client.get('/api/runs/matchup-fixture/matchup-render/fixture-0').status_code==404
