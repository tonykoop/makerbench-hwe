"""Photo calibration and real CAD-axis registration controls (no FreeCAD)."""
import importlib.util
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

MODULE = Path(__file__).resolve().parents[1]/'scripts/render_wood_photo_overlay.py'
spec = importlib.util.spec_from_file_location('wood_photo_overlay', MODULE)
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


def test_perspective_registers_all_four_measured_plate_corners():
    corners = [111, 4, 351, 24, 333, 617, 90, 622]
    coefficients = overlay.perspective_coefficients(corners, 723, 1371)
    transform = np.append(coefficients, 1).reshape(3, 3)
    for point, expected in zip([(0, 0), (723, 0), (723, 1371), (0, 1371)],
                               np.asarray(corners).reshape(4, 2)):
        actual = transform @ [*point, 1]
        assert actual[:2]/actual[2] == pytest.approx(expected)


def test_overlay_preserves_physical_axis_scale_and_source_photo(tmp_path):
    photo = Image.new('RGB', (241, 457), 'white')
    # The same physical point has Y=80 mm horizontally and X=100 mm vertically.
    edges = [[[100, 80], [110, 80]]]
    overlay.render(photo, edges, [0, 0, 241, 0, 241, 457, 0, 457],
                   [457, 241], tmp_path)
    with Image.open(tmp_path/'outline.png') as outline:
        assert outline.size == (723, 1371)
        assert outline.getpixel((240, 300)) == (0, 0, 0)
        assert outline.getpixel((300, 240)) == (255, 255, 255)
    assert photo.getpixel((80, 100)) == (255, 255, 255)
    assert json.loads((tmp_path/'calibration.json').read_text())['pixels_per_mm'] == 3


def test_degenerate_photo_calibration_fails():
    with pytest.raises(np.linalg.LinAlgError):
        overlay.perspective_coefficients([0]*8, 723, 1371)


@pytest.mark.parametrize('solid_count,exit_code', [(1, 0), (14, 2)])
def test_command_exports_diagnostics_but_rejects_detached_solids(
        tmp_path, monkeypatch, solid_count, exit_code):
    photo = tmp_path/'photo.png'
    Image.new('RGB', (241, 457), 'white').save(photo)
    output = tmp_path/'out'
    calls = []
    monkeypatch.setattr(overlay.wood, '_copy_bundle', lambda *args: calls.append('validate'))

    def fake_worker(driver, spec, destination, inputs):
        calls.append('restore')
        (destination/'native-state.json').write_text(json.dumps(
            {'valid': True, 'solid_count': solid_count, 'errors': {}}))
        (destination/'outline.json').write_text('[]')

    monkeypatch.setattr(overlay.wood, '_run_worker', fake_worker)
    monkeypatch.setattr('sys.argv', ['overlay', '--bundle', str(tmp_path/'bundle'),
        '--photo', str(photo), '--out', str(output), '--corners',
        '0', '0', '241', '0', '241', '457', '0', '457'])
    if exit_code:
        with pytest.raises(SystemExit) as error:
            overlay.main()
        assert error.value.code == exit_code
    else:
        overlay.main()
    assert calls == ['validate', 'restore']
    assert (output/'overlay.png').is_file()
