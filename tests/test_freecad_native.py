"""Native FreeCAD deliverables through the sandbox boundary and nightly ingestion."""
import json
from pathlib import Path

import pytest
import trimesh
from PIL import Image

from makerbench import cadquery_backend, freecad_backend, nightly_cad, render
from makerbench import code_cad_arena_runner as arena
from makerbench.code_cad_objective import RenderArtifacts


@pytest.mark.parametrize('invalid', ['../outside.FCStd', '/tmp/out.FCStd', 'wrong.stl'])
def test_native_name_cannot_escape_output(tmp_path, invalid):
    with pytest.raises(ValueError, match='plain .FCStd'):
        freecad_backend.compile_freecad_to_artifacts(tmp_path / 'source.py', tmp_path / 'out',
                                                    native_filename=invalid)


@pytest.mark.parametrize('mode', ['ok', 'missing', 'symlink', 'oversized'])
def test_sandbox_copies_only_bounded_native_export(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(cadquery_backend, '_bubblewrap_available', lambda _: True)
    monkeypatch.setattr(freecad_backend, 'pinned_appdir', lambda: tmp_path / 'runtime')
    monkeypatch.setattr(cadquery_backend, '_render_preview', lambda *args: ())
    monkeypatch.setattr(freecad_backend, 'MAX_EXPORT_BYTES', 1024)
    source = tmp_path / 'source.py'
    source.write_text('result = fixture')
    name = 'box_base_r1_model.FCStd'

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            output = Path(command[command.index('/out') - 1])
            assert command[-1] == name
            (output / 'output.stl').write_bytes(b'fixture mesh')
            (output / 'output.step').write_bytes(b'fixture step')
            (output / 'provenance.json').write_text(json.dumps({'freecad_version': '1.1.4'}))
            if mode == 'ok':
                (output / name).write_bytes(b'fixture native')
            elif mode == 'symlink':
                (output / name).symlink_to(source)
            elif mode == 'oversized':
                (output / name).write_bytes(b'x' * 1025)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, **kwargs):
            return '', ''

    monkeypatch.setattr(freecad_backend.subprocess, 'Popen', Process)
    out = tmp_path / 'out'
    if mode == 'ok':
        artifacts = freecad_backend.compile_freecad_to_artifacts(source, out, native_filename=name)
        assert artifacts.stl_path.read_bytes() == b'fixture mesh'
        assert (out / name).read_bytes() == b'fixture native'
        assert json.loads((out / 'provenance.json').read_text())['confinement']['network'] is False
    else:
        with pytest.raises(render.CompileError, match='no usable'):
            freecad_backend.compile_freecad_to_artifacts(source, out, native_filename=name)
        assert not out.exists()


@pytest.mark.parametrize("turns", [1, 3])
def test_nightly_freecad_native_name_pointer_and_python_source(tmp_path, monkeypatch, turns):
    from makerbench.arena_studio.control_room import review_view

    registry = {'instruments': [{'id': 'test-harp', 'task_brief': 'A synthetic box.',
                                'envelope_mm': [100, 100, 100], 'min_bodies': 1}]}
    def generator(request):
        generator.usage_observations[(request.model_id, request.instrument_id, request.seed, request.context_tier)] = {'cost_usd': 0.1}
        return 'result = box'

    generator.usage_observations = {}
    monkeypatch.setattr(nightly_cad.providers, 'resolve_generator', lambda *args, **kwargs: generator)
    expected = 'test-harp_base_r1_model.FCStd'

    def compile_fixture(source, out, *, native_filename):
        assert source.suffix == '.py'
        assert native_filename == expected
        out.mkdir(parents=True, exist_ok=True)
        (out / native_filename).write_bytes(b'synthetic native document')
        trimesh.creation.box(extents=[20, 20, 20]).export(out / 'output.stl')
        Image.new('RGB', (16, 16)).save(out / 'preview.png')
        return RenderArtifacts(stl_path=out / 'output.stl', png_path=out / 'preview.png')

    monkeypatch.setattr(arena, 'compiler_for_backend', lambda _: compile_fixture)
    entrant = nightly_cad.NightlyEntrant('fc', 'arena', 'stub-a', backend='freecad')
    job = nightly_cad.NightlyJob('night', 'test-harp', '', [entrant], turns=turns, stop_early=False)
    run_dir = tmp_path / 'run'
    executor = nightly_cad.NightlyExecutor(queue_path=tmp_path / 'queue.json',
                                         registry_path=tmp_path / 'registry.json', output_root=tmp_path)
    cost = executor._run_arena(job, entrant, run_dir, registry)
    assert cost == pytest.approx(0.0 if turns == 1 else 0.1 * turns)
    row = json.loads((run_dir / 'run_log.json').read_text())['trials'][0]['result']
    assert Path(row['native_path']).name == expected
    if turns > 1:
        assert Path(row['native_path']).parent.parent.name == f'turn-{turns}'
        assert len(row['turns']) == turns
        assert all(Path(turn['source_path']).suffix == '.py' for turn in row['turns'])
        assert len(list((run_dir / 'gen').rglob('*.FCStd'))) == turns
    assert Path(row['native_path']).parent == Path(row['artifacts']['stl_path']).parent
    assert Path(row['gen']['scad_path']).suffix == '.py'
    assert not list((run_dir / 'gen').rglob('*.scad'))
    provenance = json.loads(Path(row['gen']['provenance_path']).read_text())
    assert Path(provenance['scad_path']).suffix == '.py'
    view = review_view(run_dir, tmp_path)
    assert view['designs'][0]['native'] == {'file': expected, 'app': 'FreeCAD'}
