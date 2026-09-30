"""Matchup provenance is typed, durable and cannot change on resume."""
import json
from pathlib import Path

import pytest

from makerbench.arena_studio import doe
from makerbench import nightly_cad
from makerbench.schema import RunResults


def metadata():
    preview = doe.build_matchup('model', ['stub-a', 'stub-b'], instruments=['ocarina'], models=['stub-a'])
    return {k: preview[k] for k in ('varied_axis', 'values', 'varied_axes', 'factorial', 'held')}


def test_result_envelope_preserves_matchup_and_legacy_loads():
    raw = {'benchmark_version': '0.1.0', 'model_identifier': 'stub-a', 'results': [], 'matchup': metadata()}
    result = RunResults.model_validate(raw)
    assert result.model_dump(mode='json')['matchup'] == metadata()
    del raw['matchup']
    assert RunResults.model_validate(raw).matchup is None
    legacy = Path('results/baseline-v0/r_vented_blind.json')
    assert RunResults.model_validate_json(legacy.read_text()).matchup is None


@pytest.mark.parametrize('change', [
    {'varied_axis': 'unsupported'},
    {'values': ['stub-a']},
    {'varied_axes': ['models', 'seeds']},
    {'held': {'models': 'stub-a'}},
    {'held': {'instruments': '/home/test/secret', 'levels': 'L1', 'context_tiers': 'blind', 'seeds': 0, 'backends': 'openscad'}},
])
def test_invalid_matchup_cannot_enter_published_envelope(change):
    raw = {'benchmark_version': '0.1.0', 'model_identifier': 'stub-a', 'matchup': {**metadata(), **change}}
    with pytest.raises(ValueError):
        RunResults.model_validate(raw)


def test_resume_cannot_relabel_prior_run(tmp_path):
    job = valid_job(tmp_path)
    nightly_cad._fresh_run_log(tmp_path, job, matchup=metadata())
    original = (tmp_path / 'run_log.json').read_bytes()
    changed = {**metadata(), 'values': ['stub-a', 'stub-b', 'stub-c']}
    with pytest.raises(ValueError, match='matchup'):
        nightly_cad._fresh_run_log(tmp_path, job, matchup=changed)
    assert (tmp_path / 'run_log.json').read_bytes() == original


def test_legacy_run_cannot_be_retroactively_claimed_as_matchup(tmp_path):
    job = valid_job(tmp_path)
    nightly_cad._fresh_run_log(tmp_path, job)
    with pytest.raises(ValueError, match='matchup'):
        nightly_cad._fresh_run_log(tmp_path, job, matchup=metadata())
    assert 'matchup' not in json.loads((tmp_path / 'run_log.json').read_text())['config']


def test_held_values_must_describe_actual_job(tmp_path):
    job = valid_job(tmp_path, seed=1)
    with pytest.raises(ValueError, match='matchup seeds'):
        nightly_cad._fresh_run_log(tmp_path, job, matchup=metadata())
    assert not (tmp_path / 'run_log.json').exists()


def test_exported_schemas_and_golden_metadata_are_current():
    import subprocess
    import sys
    from makerbench.schema import MatchupMetadata

    subprocess.run([sys.executable, 'scripts/export_result_schemas.py', '--check'], check=True)
    schema = json.loads(Path('schemas/results.schema.json').read_text())
    example = json.loads(Path('schemas/examples/matchup_results.example.json').read_text())
    assert schema == RunResults.model_json_schema()
    assert json.loads(Path('schemas/matchup.schema.json').read_text()) == MatchupMetadata.model_json_schema()
    assert RunResults.model_validate(example).matchup.model_dump() == metadata()
    objective = json.loads(Path('tests/fixtures/matchup_objective_scoreline.json').read_text())
    assert MatchupMetadata.model_validate(objective['matchup']).model_dump() == metadata()


def valid_job(tmp_path, ids=('stub-a::L1', 'stub-b::L1'), *, seed=0):
    image=tmp_path/'reference.png'
    image.write_bytes(b'fixture')
    job=nightly_cad.NightlyJob(job_id='fixture', instrument_id='ocarina', seed=seed,
                             reference_image=str(image), entrants=[
        nightly_cad.NightlyEntrant(entrant_id=identifier, model_id=model, kind='arena')
        for identifier, model in zip(ids, ['stub-a', 'stub-b'])
    ])
    job.validate()
    return job


@pytest.mark.parametrize('ids', [('stub-a', 'stub-b'), ('stub-a::L2', 'stub-b')])
@pytest.mark.parametrize('existing', [False, True])
def test_unverifiable_levels_rejected_without_modifying_evidence(tmp_path, ids, existing):
    job=valid_job(tmp_path, ids)
    claim=metadata()
    claim['held']['levels']='L2'
    path=tmp_path/'run_log.json'
    if existing:
        path.write_text(json.dumps({'config':{'matchup':claim}, 'trials':[{'trial_id':'preserve-sentinel'}]}))
    original=path.read_bytes() if existing else None
    with pytest.raises(ValueError, match='matchup level'):
        nightly_cad._fresh_run_log(tmp_path, job, matchup=claim)
    if existing:
        assert path.read_bytes() == original
    else:
        assert not path.exists()


@pytest.mark.parametrize('identifier', ['other::L2', 'stub-a::L2::cadquery',
                                       'stub-a::L2::openscad::extra', 'stub-a::'])
def test_malformed_level_encoding_cannot_claim_provenance(tmp_path, identifier):
    job=valid_job(tmp_path, (identifier, 'stub-b::L2'))
    claim=metadata()
    claim['held']['levels']='L2'
    with pytest.raises(ValueError, match='matchup level'):
        nightly_cad._fresh_run_log(tmp_path, job, matchup=claim)
    assert not (tmp_path/'run_log.json').exists()


@pytest.mark.parametrize('suffix', ['', '::openscad'])
def test_all_encoded_entrants_support_a_recorded_level(tmp_path, suffix):
    job=valid_job(tmp_path, ('stub-a::L2'+suffix, 'stub-b::L2'+suffix))
    claim=metadata()
    claim['held']['levels']='L2'
    nightly_cad._fresh_run_log(tmp_path, job, matchup=claim)
    assert json.loads((tmp_path/'run_log.json').read_text())['config']['matchup'] == claim


def test_legacy_plain_ids_remain_supported_without_a_matchup_claim(tmp_path):
    job=valid_job(tmp_path, ('stub-a', 'stub-b'))
    nightly_cad._fresh_run_log(tmp_path, job)
    original=(tmp_path/'run_log.json').read_bytes()
    nightly_cad._fresh_run_log(tmp_path, job)
    assert (tmp_path/'run_log.json').read_bytes() == original
    assert 'matchup' not in json.loads(original)['config']
