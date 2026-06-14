import importlib.util
import json
import sys
from pathlib import Path
from shutil import copytree


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generate_run_explorer = _load_script("generate_run_explorer")
generate_run_library = _load_script("generate_run_library")


def test_run_explorer_renders_fixture_metadata(tmp_path):
    fixture = ROOT / "tests" / "fixtures" / "run_nav" / "run_alpha"
    run_dir = tmp_path / "run_alpha"
    copytree(fixture, run_dir)

    entry = generate_run_explorer.write_explorer(run_dir)
    html = (run_dir / "explorer.html").read_text(encoding="utf-8")

    assert entry.run_id == "fixture-alpha-workflow"
    assert entry.harness_class == "agentic_cad_stack"
    assert entry.hii_label == "light human assist"
    assert "Artifact Viewer" in html
    assert "WorkflowManifest / HII Trace" in html
    assert "gdt-summary.txt" in html
    assert "fixture-alpha-certificate-hash" in html


def test_run_library_emits_manifest_and_filterable_html(tmp_path):
    fixture_root = ROOT / "tests" / "fixtures" / "run_nav"
    runs_root = tmp_path / "runs"
    copytree(fixture_root, runs_root)
    output_html = tmp_path / "library.html"
    output_manifest = tmp_path / "runs-manifest.json"

    entries = generate_run_library.write_library(runs_root, output_html, output_manifest)
    html = output_html.read_text(encoding="utf-8")
    manifest = json.loads(output_manifest.read_text(encoding="utf-8"))

    assert len(entries) == 2
    assert manifest["count"] == 2
    assert {run["run_id"] for run in manifest["runs"]} == {
        "fixture-alpha-workflow",
        "fixture-beta-workflow",
    }
    assert (runs_root / "run_alpha" / "explorer.html").exists()
    assert (runs_root / "run_beta" / "explorer.html").exists()
    assert 'data-key="harness_class"' in html
    assert 'data-key="domain"' in html
    assert 'data-key="hii_label"' in html
    assert 'data-key="verification_status"' in html
    assert 'data-key="score_bucket"' in html
