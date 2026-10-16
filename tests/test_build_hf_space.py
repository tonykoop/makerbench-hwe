"""HF Space staging contract tests (#1026).

``scripts/build_hf_space.py`` stages ``spaces/makerbench_hwe/`` into a
directory for a manual upload. These tests stage the real Space source and
prove the staged tree is public-safe: no private storage, oracle, submission,
held-out-seed or result paths, no reference to the private locations, and the
public-artifact audit and public-term checks pass on it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "makerbench_build_hf_space",
    ROOT / "scripts" / "build_hf_space.py",
)
build_hf_space = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_hf_space)

SPACE_SRC = ROOT / "spaces" / "makerbench_hwe"


@pytest.fixture()
def staged(tmp_path: Path) -> Path:
    out = tmp_path / "hf_space"
    build_hf_space.stage(out)
    return out


def test_stage_copies_exactly_the_space_source(staged: Path) -> None:
    expected = sorted(
        p.relative_to(SPACE_SRC).as_posix()
        for p in SPACE_SRC.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
    )
    got = sorted(p.relative_to(staged).as_posix() for p in build_hf_space.staged_files(staged))
    assert got == expected
    for name in (
        "README.md",
        "app.py",
        "requirements.txt",
        "tabs/grade.py",
        "tabs/explore.py",
        "tabs/leaderboard.py",
    ):
        assert name in got


def test_stage_drops_caches(tmp_path: Path) -> None:
    src = tmp_path / "src"
    import shutil

    shutil.copytree(SPACE_SRC, src)
    (src / "__pycache__").mkdir(exist_ok=True)
    (src / "__pycache__" / "app.cpython-311.pyc").write_bytes(b"0")
    (src / "tabs" / "stray.pyc").write_bytes(b"0")
    out = tmp_path / "out"
    build_hf_space.stage(out, src=src)
    assert not (out / "__pycache__").exists()
    assert not (out / "tabs" / "stray.pyc").exists()


def test_staged_tree_contains_no_private_or_oracle_paths(staged: Path) -> None:
    """Integrity: nothing private or answer-bearing is reachable from the Space."""
    forbidden_parts = {
        "private",
        "oracles",
        "oracle",
        "submissions",
        "held_out",
        "heldout",
        "held-out",
        "results",
        "artifacts",
    }
    for path in build_hf_space.staged_files(staged):
        rel = path.relative_to(staged)
        assert not {p.lower() for p in rel.parts} & forbidden_parts, rel
        text = path.read_text(encoding="utf-8").lower()
        for marker in (
            "private/oracles",
            "private\\oracles",
            "private/submissions",
            "private\\submissions",
            "makerbench-oracles",
        ):
            assert marker not in text, f"{rel} refers to {marker}"
    assert build_hf_space.verify_staged(staged) == []


def test_space_source_reads_nothing_outside_its_directory() -> None:
    """The app must not reach into the repo (results/, private/, site/ data)."""
    for path in SPACE_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "parents[" not in text, path
        assert ".parent.parent" not in text, path
        assert "open(" not in text, path
        assert "read_text" not in text, path


@pytest.mark.parametrize(
    "rel",
    [
        "private/oracles/gold.json",
        "oracles/answers.txt",
        "results/model/r_x.json",
        "tabs/held_out/seeds.txt",
    ],
)
def test_verify_rejects_forbidden_paths(staged: Path, rel: str) -> None:
    target = staged / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    problems = build_hf_space.verify_staged(staged)
    assert any(p.startswith(rel) for p in problems), problems


def test_verify_rejects_reference_to_private_oracles(staged: Path) -> None:
    (staged / "tabs" / "leak.py").write_text(
        'PATH = "../../private/oracles/x.json"\n', encoding="utf-8"
    )
    problems = build_hf_space.verify_staged(staged)
    assert "tabs/leak.py: refers to a private location" in problems


def test_verify_rejects_pin_drift_and_missing_canary(staged: Path) -> None:
    (staged / "requirements.txt").write_text("gradio==4.0.0\n", encoding="utf-8")
    readme = staged / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(build_hf_space.CANARY_GUID, "x"),
        encoding="utf-8",
    )
    problems = build_hf_space.verify_staged(staged)
    assert "README.md: contamination canary missing" in problems
    assert "requirements.txt: gradio pin must equal README sdk_version" in problems


def test_stage_fails_closed_on_integrity_problem(tmp_path: Path) -> None:
    import shutil

    src = tmp_path / "src"
    shutil.copytree(SPACE_SRC, src)
    (src / "private").mkdir()
    (src / "private" / "x.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="integrity"):
        build_hf_space.stage(tmp_path / "out", src=src)


def test_public_term_and_artifact_scripts_pass_on_staged_tree(staged: Path) -> None:
    terms = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_public_terms.py"), "--root", str(staged)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert terms.returncode == 0, terms.stdout + terms.stderr

    audit = build_hf_space._load_script("audit_public_artifacts")
    entries = [
        audit.TrackedEntry(mode="100644", path=p.relative_to(staged).as_posix())
        for p in build_hf_space.staged_files(staged)
    ]
    assert audit.audit_tracked_entries(entries) == []


def test_space_card_is_public_and_accurate(staged: Path) -> None:
    readme = (staged / "README.md").read_text(encoding="utf-8")
    assert "sdk: gradio" in readme
    assert "app_file: app.py" in readme
    assert "private dashboard" not in readme.lower()
    assert "https://tonykoop.github.io/makerbench-hwe/" in readme
    assert "https://github.com/tonykoop/makerbench-hwe" in readme
    assert build_hf_space.CANARY_GUID in readme
    for tab in ("Grade a part", "Explore designs", "Leaderboard"):
        assert f"**{tab}**" in readme
    assert "@software{" in readme  # citation
    assert "makerbench grade" in readme  # CLI quickstart
    assert "pending" in readme.lower()  # dataset link not live yet


def test_main_prints_upload_command_for_the_one_space(tmp_path, monkeypatch, capsys):
    out = tmp_path / "hf_space"
    monkeypatch.setattr(sys, "argv", ["build_hf_space.py", "--out", str(out)])

    build_hf_space.main()

    captured = capsys.readouterr().out
    assert build_hf_space.DEFAULT_SPACE_ID == "tonykoop/makerbench-hwe"
    assert f"hf upload tonykoop/makerbench-hwe {out} . --repo-type space" in captured
    assert (out / "app.py").is_file()
