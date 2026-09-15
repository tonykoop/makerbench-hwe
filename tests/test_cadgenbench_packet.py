"""Tests for scripts/build_cadgenbench_packet.py (no network, synthetic fixtures)."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import build_cadgenbench_packet as packet  # noqa: E402

FAKE_STEP = "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def _make_steps_dir(tmp_path: Path, layout: str = "flat") -> Path:
    steps = tmp_path / "steps"
    steps.mkdir()
    for name in ("sample_a", "sample_b", "sample_c"):
        if layout == "flat":
            (steps / f"{name}.step").write_text(FAKE_STEP, encoding="utf-8")
        else:
            (steps / name).mkdir()
            (steps / name / "output.step").write_text(FAKE_STEP, encoding="utf-8")
    return steps


def _base_args(steps: Path, out: Path, *extra: str) -> list[str]:
    return [
        "--steps-dir",
        str(steps),
        "--run-name",
        "makerbench-test-run",
        "--submitter-name",
        "MakerBench",
        "--submission-name",
        "test-model via MakerBench brep-build123d",
        "--model",
        "test-model",
        "--makerbench-commit",
        "deadbeef" * 5,
        "--out",
        str(out),
        "--agree-to-publish",
        *extra,
    ]


def _write_expected_manifest(tmp_path: Path, *sample_ids: str) -> Path:
    path = tmp_path / "expected.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source_dataset": "example/cadgenbench-data",
                "source_revision": "abc123",
                "samples": [
                    {
                        "id": sample_id,
                        "task_type": "generation" if sample_id.startswith("sample") else "editing",
                    }
                    for sample_id in sample_ids
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_dry_run_validates_and_writes_nothing(tmp_path, capsys):
    steps = _make_steps_dir(tmp_path)
    out = tmp_path / "dist"
    rc = packet.main(_base_args(steps, out, "--dry-run"))
    assert rc == 0
    assert not out.exists()
    printed = capsys.readouterr().out
    assert "DRY RUN" in printed
    assert "sample_a" in printed and "sample_c" in printed
    assert "submission.zip" in printed


@pytest.mark.parametrize("layout", ["flat", "nested"])
def test_full_build_layout_and_meta(tmp_path, layout):
    steps = _make_steps_dir(tmp_path, layout=layout)
    out = tmp_path / "dist"
    rc = packet.main(_base_args(steps, out))
    assert rc == 0

    run_dir = out / "makerbench-test-run"
    staging = run_dir / "results" / "makerbench-test-run"
    # Local-grader staging layout: results/<run_name>/<sample>/output.step
    for name in ("sample_a", "sample_b", "sample_c"):
        assert (staging / name / "output.step").read_text(encoding="utf-8") == FAKE_STEP

    meta = json.loads((staging / "meta.json").read_text(encoding="utf-8"))
    assert set(meta) == {
        "submitter_name",
        "submission_name",
        "agent_url",
        "notes",
        "agree_to_publish",
    }
    assert meta["submitter_name"] == "MakerBench"
    assert meta["agree_to_publish"] is True
    assert meta["agent_url"] is None
    assert "makerbench_commit=deadbeefdead" in meta["notes"]
    assert len(meta["notes"]) <= packet.NOTES_MAX_CHARS

    # Zip root layout: meta.json + <sample>/output.step, no results/ prefix,
    # and no provenance sidecar inside the zip.
    with zipfile.ZipFile(run_dir / "submission.zip") as zf:
        names = sorted(zf.namelist())
    assert names == [
        "meta.json",
        "sample_a/",
        "sample_a/output.step",
        "sample_b/",
        "sample_b/output.step",
        "sample_c/",
        "sample_c/output.step",
    ]

    provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["makerbench_commit"] == "deadbeef" * 5
    assert provenance["profile"] == "brep-build123d"
    assert provenance["samples"] == ["sample_a", "sample_b", "sample_c"]


def test_requires_agree_to_publish(tmp_path, capsys):
    steps = _make_steps_dir(tmp_path)
    args = _base_args(steps, tmp_path / "dist")
    args.remove("--agree-to-publish")
    rc = packet.main(args)
    assert rc == 1
    assert "agree_to_publish" in capsys.readouterr().err


def test_empty_step_file_is_packaged_as_an_honest_zero(tmp_path):
    steps = _make_steps_dir(tmp_path)
    (steps / "sample_b.step").write_text("", encoding="utf-8")
    out = tmp_path / "dist"
    rc = packet.main(_base_args(steps, out))
    assert rc == 0

    run_dir = out / "makerbench-test-run"
    with zipfile.ZipFile(run_dir / "submission.zip") as zf:
        assert zf.read("sample_b/output.step") == b""
    provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["empty_outputs"] == ["sample_b"]


def test_rejects_missing_steps_dir(tmp_path, capsys):
    rc = packet.main(_base_args(tmp_path / "nope", tmp_path / "dist", "--dry-run"))
    assert rc == 1
    assert "steps-dir" in capsys.readouterr().err


def test_rejects_overlong_notes(tmp_path, capsys):
    steps = _make_steps_dir(tmp_path)
    rc = packet.main(_base_args(steps, tmp_path / "dist", "--dry-run", "--notes", "x" * 501))
    assert rc == 1
    assert "500" in capsys.readouterr().err


def test_expected_samples_emit_missing_folders_and_reject_extras(tmp_path, capsys):
    steps = _make_steps_dir(tmp_path)
    expected = _write_expected_manifest(tmp_path, "sample_a", "sample_b", "sample_c", "sample_d")
    out = tmp_path / "dist"
    rc = packet.main(_base_args(steps, out, "--expected-samples", str(expected)))
    assert rc == 0
    run_dir = out / "makerbench-test-run"
    assert (run_dir / "results" / "makerbench-test-run" / "sample_d").is_dir()
    with zipfile.ZipFile(run_dir / "submission.zip") as zf:
        assert "sample_d/" in zf.namelist()
        assert "sample_d/output.step" not in zf.namelist()
    provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["sample_count"] == 4
    assert provenance["output_count"] == 3
    assert provenance["missing_outputs"] == ["sample_d"]
    assert provenance["source_revision"] == "abc123"

    expected = _write_expected_manifest(tmp_path, "sample_a", "sample_b")
    rc = packet.main(
        _base_args(steps, tmp_path / "dist", "--dry-run", "--expected-samples", str(expected))
    )
    assert rc == 1
    assert "sample_c" in capsys.readouterr().err


def test_expected_manifest_can_build_folder_only_packet(tmp_path):
    steps = tmp_path / "steps"
    steps.mkdir()
    expected = _write_expected_manifest(tmp_path, "201", "202")
    out = tmp_path / "dist"

    rc = packet.main(_base_args(steps, out, "--expected-samples", str(expected)))

    assert rc == 0
    run_dir = out / "makerbench-test-run"
    with zipfile.ZipFile(run_dir / "submission.zip") as zf:
        assert sorted(zf.namelist()) == ["201/", "202/", "meta.json"]


def test_rebuild_does_not_reuse_a_now_missing_output(tmp_path):
    steps = _make_steps_dir(tmp_path)
    expected = _write_expected_manifest(tmp_path, "sample_a", "sample_b", "sample_c")
    out = tmp_path / "dist"
    args = _base_args(steps, out, "--expected-samples", str(expected))
    assert packet.main(args) == 0

    (steps / "sample_b.step").unlink()
    assert packet.main(args) == 0

    run_dir = out / "makerbench-test-run"
    staging = run_dir / "results" / "makerbench-test-run"
    assert not (staging / "sample_b" / "output.step").exists()
    with zipfile.ZipFile(run_dir / "submission.zip") as zf:
        assert "sample_b/" in zf.namelist()
        assert "sample_b/output.step" not in zf.namelist()
