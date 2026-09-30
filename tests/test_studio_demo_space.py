"""Space bundle is standalone, curated and fail-closed for public-data mistakes."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("studio_space_builder", ROOT / "scripts/build_studio_demo_space.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_stage_has_only_demo_runtime_and_declared_public_assets(tmp_path):
    out = tmp_path / "space"
    manifest = builder.stage(out)
    assert manifest["profile"] == "read-only-showcase"
    assert (out / "Dockerfile").is_file()
    assert "sdk: docker" in (out / "README.md").read_text()
    assert "app_port: 7860" in (out / "README.md").read_text()
    assert "USER studio" in (out / "Dockerfile").read_text()
    assert "--port" in (out / "Dockerfile").read_text()
    assert not (out / "makerbench/cli.py").exists()
    assert not (out / "makerbench/provenance.py").exists()
    assert not (out / "makerbench/arena_studio/service.py").exists()
    assert len(list((out / "makerbench/arena_studio/demo_assets").glob("*.png"))) == 28
    assert json.loads((out / "build-manifest.json").read_text()) == manifest
    assert all(not p.startswith(("runs/", "private/", ".git/")) for p in manifest["files"])


def test_existing_output_cannot_smuggle_stale_content(tmp_path):
    (tmp_path / "private.json").write_text("private sentinel")
    with pytest.raises(ValueError, match="fresh output"):
        builder.stage(tmp_path)
    assert (tmp_path / "private.json").read_text() == "private sentinel"


def test_unknown_answer_field_is_rejected():
    content = json.loads((ROOT / "makerbench/arena_studio/data/showcase.json").read_text())
    content["cases"][0]["rows"][0]["source_code"] = "PRIVATE_SENTINEL"
    with pytest.raises(ValueError, match="Unexpected showcase fields"):
        builder.audit_snapshot(content)


@pytest.mark.parametrize("wrong", ["seed", "trial_count"])
def test_misstated_aggregate_scope_is_rejected_before_staging(tmp_path, monkeypatch, wrong):
    source = ROOT / "makerbench/arena_studio/data/showcase.json"
    content = json.loads(source.read_text())
    if wrong == "seed":
        content["cases"][0]["held"]["seeds"] = 0
    else:
        content["cases"][1]["rows"][0]["n_objective_trials"] = 1
    original = Path.read_text
    def changed_snapshot(path, *args, **kwargs):
        return json.dumps(content) if path == source else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", changed_snapshot)
    out = tmp_path / "space"
    with pytest.raises(ValueError, match="trial counts"):
        builder.stage(out)
    assert not out.exists()


@pytest.mark.parametrize("field", ["elo", "elos", "vote_count", "elo2", "voterId", "ratings"])
def test_nested_preference_fields_are_rejected(field):
    with pytest.raises(ValueError, match="Preference"):
        builder.audit_data({"cases": [{"rows": [{field: 123}]}]})


def test_declared_scientific_gate_is_not_a_preference_field():
    builder.audit_data({"gates": {"fits_envelope": 1}, "source": "docs/showcase/kora/CASE_STUDY.md"})


@pytest.mark.parametrize("path", ["/home/user/private.png", "C:\\private\\image.png", "/mnt/c/private.png"])
def test_host_paths_are_rejected(path):
    with pytest.raises(ValueError, match="Host paths"):
        builder.audit_data({"image": path})
