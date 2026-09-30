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
    job = nightly_cad.NightlyJob(job_id='fixture', instrument_id='ocarina', seed=0, reference_image='unused', entrants=[])
    nightly_cad._fresh_run_log(tmp_path, job, matchup=metadata())
    original = (tmp_path / 'run_log.json').read_bytes()
    changed = {**metadata(), 'values': ['stub-a', 'stub-c']}
    with pytest.raises(ValueError, match='matchup'):
        nightly_cad._fresh_run_log(tmp_path, job, matchup=changed)
    assert (tmp_path / 'run_log.json').read_bytes() == original


def test_legacy_run_cannot_be_retroactively_claimed_as_matchup(tmp_path):
    job = nightly_cad.NightlyJob(job_id='fixture', instrument_id='ocarina', seed=0, reference_image='unused', entrants=[])
    nightly_cad._fresh_run_log(tmp_path, job)
    with pytest.raises(ValueError, match='matchup'):
        nightly_cad._fresh_run_log(tmp_path, job, matchup=metadata())
    assert 'matchup' not in json.loads((tmp_path / 'run_log.json').read_text())['config']


def test_held_values_must_describe_actual_job(tmp_path):
    job = nightly_cad.NightlyJob(job_id='fixture', instrument_id='ocarina', seed=1, reference_image='unused', entrants=[])
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
