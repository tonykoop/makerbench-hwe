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
    ({'adapter': 'solidworks', 'document': 'Part1'}, False),
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
        assert data['error']['message'] in reason


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
