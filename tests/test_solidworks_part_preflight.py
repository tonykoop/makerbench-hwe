import io
import json
import urllib.error

import pytest

from makerbench.live_cad_runner import LiveCadConfig, connector_part_document
from makerbench import nightly_cad


@pytest.mark.parametrize('data,ok', [
    ({'adapter': 'solidworks', 'document': 'Part1', 'document_type': 'part'}, True),
    ({'adapter': 'solidworks', 'document': 'Assembly1', 'document_type': 'assembly'}, False),
    ({'adapter': 'solidworks', 'document': 'Drawing1', 'document_type': 'drawing'}, False),
    ({'adapter': 'solidworks', 'document': 'Part1'}, True),
    ({'adapter': 'solidworks', 'document': 'Part1', 'document_type': None}, False),
    ({'adapter': 'solidworks', 'document': None}, False),
    ({'adapter': 'solidworks', 'document': 'Part1', 'error': 'ActiveDoc COM error'}, False),
    ({'adapter': 'solidworks', 'document': '', 'document_type': 'part'}, False),
    ({'error': {'message': 'bound document is closed'}}, False),
    ({}, False), ([], False),
])
def test_context_contract(data, ok):
    def bridge(request, timeout):
        assert request.full_url == 'http://bridge.test:8767/context'
        assert request.get_header('Authorization') == 'Bearer test-token'
        return io.BytesIO(json.dumps(data).encode())
    config = LiveCadConfig(backend='solidworks-live', driver_model='test', env={'HWE_SW_HOST': 'bridge.test', 'HWE_SW_TOKEN': 'test-token'})
    passed, reason = connector_part_document(config, urlopen=bridge)
    assert passed is ok
    if isinstance(data, dict) and data.get('error'):
        error = data['error']
        assert (error['message'] if isinstance(error, dict) else error) in reason


def test_http_bridge_error_is_preserved_but_token_is_redacted():
    def bridge(*args, **kwargs):
        raise urllib.error.HTTPError('url', 500, 'failed', {},
            io.BytesIO(b'{"error":{"message":"SldWorks.Application.ActiveDoc secret-token"}}'))
    passed, reason = connector_part_document(LiveCadConfig(backend='solidworks-live', driver_model='test', env={'HWE_SW_TOKEN': 'secret-token'}), urlopen=bridge)
    assert not passed and 'SldWorks.Application.ActiveDoc' in reason and 'secret-token' not in reason


def test_nightly_fails_before_driver_or_mcp_probe(tmp_path, monkeypatch):
    from PIL import Image

    image = tmp_path / 'ref.png'
    Image.new('RGB', (10, 10)).save(image)
    job = nightly_cad.NightlyJob(job_id='j', instrument_id='test', reference_image=image,
                                entrants=[], budget_usd=0)
    entrant = nightly_cad.NightlyEntrant(entrant_id='sw', kind='live', backend='solidworks-live', model_id='codex-gpt-6.1-sol')
    reason = 'hwe-solidworks get_context: SldWorks.Application.ActiveDoc'
    monkeypatch.setattr(nightly_cad, 'connector_available', lambda _: True)
    monkeypatch.setattr(nightly_cad, 'connector_part_document', lambda _: (False, reason))
    def forbidden(*args, **kwargs):
        pytest.fail('must not start a driver or MCP process')
    monkeypatch.setattr(nightly_cad, 'connector_mcp_probe', forbidden)
    monkeypatch.setattr(nightly_cad, 'make_live_execute_trial', forbidden)
    executor = nightly_cad.NightlyExecutor(queue_path=tmp_path / 'queue', registry_path=tmp_path / 'registry', output_root=tmp_path)
    with pytest.raises(RuntimeError, match='SldWorks.Application.ActiveDoc') as error:
        executor._run_live(job, entrant, tmp_path / 'run', {})
    assert str(error.value) == reason


@pytest.mark.parametrize('ping_ok,error', [(True, None), (False, None),
                                         (True, 'no active SolidWorks document'),
                                         (True, 'SldWorks.Application.ActiveDoc COM failure')])
def test_current_bridge_context_without_part_probe_fixture(tmp_path, monkeypatch, ping_ok, error):
    from makerbench.nightly_preflight import audit_entrants

    # Shape from StudioPipeline/hwe solidworks/sw_adapter.py::handle_get_context
    # (pre-#576): configurations is an active/names mapping; no document_type exists.
    context = {
        'adapter': 'solidworks', 'document': 'Part1',
        'configurations': {'active': 'Default', 'names': ['Default']},
        'parameters': [], 'feature_tree': [], 'active_selections': [],
    }
    if error:
        context = {'error': {'message': error}}
    calls = []

    def bridge(request, timeout):
        calls.append(request.full_url)
        assert request.full_url.endswith('/context')
        return io.BytesIO(json.dumps(context).encode())

    # Exercise the real connector_part_document through audit_entrants. This module
    # has no autouse fake_part_document fixture and only replaces HTTP transport.
    monkeypatch.setattr('urllib.request.urlopen', bridge)
    shim = tmp_path / 'driver' / 'solidworks_mcp_server.py'
    shim.parent.mkdir()
    shim.write_text('# fixture')
    ref = tmp_path / 'ref.png'
    ref.write_bytes(b'fixture')
    queue = tmp_path / 'queue.json'
    queue.write_text(json.dumps({'jobs': [{
        'job_id': 'legacy', 'instrument_id': 'fixture', 'reference_image': str(ref),
        'entrants': [{'entrant_id': name, 'kind': 'live', 'backend': 'solidworks-live',
                      'model_id': 'codex-gpt-6.1-sol'} for name in ('sw-a', 'sw-b')],
    }]}))
    status = audit_entrants(queue, {'HWE_SW_TOKEN': 'fixture-token'}, cadam=None,
                            environ={}, which=lambda _: '/fixture/tool',
                            ping=lambda _: ping_ok, connector_root=tmp_path)
    assert status.job_ok
    for entrant in status.entrants:
        assert entrant.ok is (ping_ok and error is None)
        if error:
            assert any(error in line for line in entrant.notes)
        elif ping_ok:
            assert 'WARN: document type unverified (bridge predates hwe#576)' in entrant.notes
        else:
            assert any('bridge did not answer' in line for line in entrant.notes)
    assert len(calls) == (2 if ping_ok else 0)
