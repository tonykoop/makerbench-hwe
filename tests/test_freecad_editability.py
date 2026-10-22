import json
from pathlib import Path
from types import SimpleNamespace

from makerbench import _freecad_worker as worker, freecad_backend as backend, cadquery_backend
from makerbench.arena_studio import control_room


def test_worker_counts_document_structures_without_counting_raw_shapes():
    sheet = SimpleNamespace(TypeId='Spreadsheet::Sheet', getNonEmptyCells=lambda: ['A1', 'A2', 'B1'],
                            getAlias=lambda cell: {'A1': 'width', 'A2': 'height'}.get(cell, ''))
    doc = SimpleNamespace(Objects=[sheet, SimpleNamespace(TypeId='PartDesign::Body'),
        SimpleNamespace(TypeId='Sketcher::SketchObject', ConstraintCount=4),
        SimpleNamespace(TypeId='Part::Feature'), SimpleNamespace(TypeId='Part::Compound')])
    assert worker.editability_counts(doc) == {'aliases': 2, 'bodies': 1, 'sketches': 1, 'constraints': 4}
    assert worker.editability_counts(SimpleNamespace(Objects=[])) == dict.fromkeys(['aliases', 'bodies', 'sketches', 'constraints'], 0)


def test_fake_runner_preserves_proof_and_warning(tmp_path, monkeypatch):
    counts = {'aliases': 0, 'bodies': 0, 'sketches': 0, 'constraints': 0}
    warning = 'FreeCAD editability: no PartDesign bodies'
    monkeypatch.setattr(cadquery_backend, '_bubblewrap_available', lambda _: True)
    monkeypatch.setattr(backend, 'pinned_appdir', lambda: tmp_path)
    monkeypatch.setattr(cadquery_backend, '_render_preview', lambda *args: ())
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            out = Path(command[command.index('/out') - 1])
            (out / 'output.step').write_text('fixture step')
            (out / 'output.stl').write_text('fixture mesh')
            (out / 'output.FCStd').write_bytes(b'fixture native')
            (out / 'provenance.json').write_text(json.dumps({'backend': 'freecad',
                'freecad_version': backend.FREECAD_VERSION, 'editability': counts, 'warnings': [warning]}))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def communicate(self, **kwargs):
            return '', ''
    monkeypatch.setattr(backend.subprocess, 'Popen', Process)
    source = tmp_path / 'source.py'
    source.write_text('# fixture')
    out = tmp_path / 'out'
    result = backend.compile_freecad_to_artifacts(source, out)
    assert (out / 'output.FCStd').read_bytes() == b'fixture native'
    assert result.warnings == (warning,)
    assert json.loads((out / 'provenance.json').read_text())['editability'] == counts


def test_card_reads_measured_provenance_and_rejects_escape(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    mesh = run / 'output.stl'
    mesh.write_text('fixture')
    row = {'trial_id': 't', 'model_id': 'freecad-sol', 'status': 'scored',
           'result': {'editable_parameters': 999, 'artifacts': {'stl_path': str(mesh)}}}
    counts = {'aliases': 12, 'bodies': 3, 'sketches': 4, 'constraints': 20}
    provenance = run / 'provenance.json'
    provenance.write_text(json.dumps({'backend': 'freecad', 'editability': counts}))
    card = control_room._design(run, tmp_path, row, {})
    assert card['editability'] == counts and card['editable_parameters'] == 12
    provenance.unlink()
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps({'backend': 'freecad', 'editability': counts}))
    provenance.symlink_to(outside)
    assert control_room._design(run, tmp_path, row, {})['editability'] is None
    provenance.unlink()
    provenance.write_text(json.dumps({'backend': 'freecad', 'editability': {**counts, 'bodies': True}}))
    assert control_room._design(run, tmp_path, row, {})['editability'] is None
