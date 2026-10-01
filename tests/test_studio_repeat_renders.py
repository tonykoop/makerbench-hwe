"""Recorded-image import controls: correspondence, complete seeds and no answer fields."""
import importlib.util
import json
from pathlib import Path

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("repeat_importer", ROOT / "scripts/import_studio_repeat_renders.py")
importer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(importer)


@pytest.fixture
def recorded(tmp_path):
    root = tmp_path / "checkout"
    model = tmp_path / "model"
    backends = {backend: tmp_path / backend for backend in importer.BACKENDS}
    for backend, run, entrants in [("openscad", model, importer.MODELS),
                                   *((backend, run, (importer.MODELS[1],)) for backend, run in backends.items())]:
        trials = []
        for entrant in entrants:
            for seed in range(3):
                trial_id = f"ocarina__seed{seed}__rep0__{entrant}"
                png = run / "render" / trial_id / "preview.png"
                png.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (2, 2), (seed * 40, 100, 30)).save(png)
                trials.append({"model_id": entrant, "seed": seed, "rep": 0, "instrument_id": "ocarina",
                               "trial_id": trial_id, "status": "scored", "result": {
                                   "model_id": entrant, "seed": seed, "instrument_id": "ocarina",
                                   "backend": backend, "context_tier": "blind", "render_ok": True, "status": "scored",
                                   "gen": {"source_code": "PRIVATE_SENTINEL"},
                                   "objective": {"sub_scores": dict.fromkeys(importer.GATES, 1), "objective_pass_rate": 1}}})
        (run / "run_log.json").write_text(json.dumps({"trials": trials}))
        relative = ("docs/showcase/post3/matchup-model/objective_scoreline.json" if run == model
                    else f"docs/showcase/post3/matchup-backend/after/objective_scoreline-{backend}.json")
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"rows": [{"entrant": entrant, "backend": backend,
                                                     "objective_pass_rate": 1, "n_objective_trials": 3}
                                                    for entrant in entrants]}))
    return root, model, backends


def test_imports_all_recorded_seeds_without_reading_or_copying_source_geometry(recorded):
    root, model, backends = recorded
    content, copies = importer.prepare(root, model, backends)
    assert len(copies) == 15
    assert len(content["records"]) == 4
    text = json.dumps(content)
    assert "PRIVATE_SENTINEL" not in text and "source_code" not in text
    assert str(model) not in text and str(root) not in text
    for source, destination in copies:
        assert source.suffix == destination.suffix == ".png"
        assert destination.is_relative_to(root / "docs/showcase/post3")
    assert sorted(trial["seed"] for trial in content["records"][0]["trials"]) == [0, 0, 1, 1, 2, 2]


@pytest.mark.parametrize("defect", ["duplicate_seed", "wrong_context", "extra_check", "wrong_trial", "wrong_average"])
def test_rejects_misstated_source_scope_before_any_public_output(recorded, defect):
    root, model, backends = recorded
    log = model / "run_log.json"
    content = json.loads(log.read_text())
    trial = content["trials"][0]
    if defect == "duplicate_seed":
        content["trials"][1]["seed"] = 0
    elif defect == "wrong_context":
        trial["result"]["context_tier"] = "image"
    elif defect == "extra_check":
        trial["result"]["objective"]["sub_scores"]["interfaces"] = 0
    elif defect == "wrong_trial":
        trial["trial_id"] = "different-trial"
    else:
        trial["result"]["objective"]["objective_pass_rate"] = 0
    log.write_text(json.dumps(content))
    with pytest.raises(ValueError):
        importer.prepare(root, model, backends)
    assert not (root / "docs/showcase/post3/studio-repeat-runs.json").exists()
    assert not list(root.glob("**/scored/*.png"))
