#!/usr/bin/env python3
"""Fail when committed ``site/data`` outputs drift from ``site/build_data.py``.

The check builds the site-data payloads into a temporary directory and compares
only the files owned by ``build_data.py``. Curated inputs such as
``failure_gallery.json`` and separately generated reports are left alone.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_DATA_DIR = Path("site/data")

GENERATED_TOP_LEVEL = frozenset(
    {
        "explorer.json",
        "findings.json",
        "get_started.json",
        "inspect.json",
        "leaderboard.json",
    }
)
GENERATED_DIRS = ("archive", "badges", "models", "tasks")
BUILD_DATA_INPUTS = ("meshes.json", "opportunity-matrix.json")


@dataclass(frozen=True)
class DriftReport:
    changed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()

    @property
    def has_drift(self) -> bool:
        return bool(self.changed or self.missing or self.extra)

    def format(self) -> str:
        if not self.has_drift:
            return "site/data is current with site/build_data.py."
        lines = [
            "Committed site/data outputs drifted from site/build_data.py.",
            "Run `python site/build_data.py` and commit the regenerated files.",
        ]
        if self.changed:
            lines.append("Changed files:")
            lines.extend(f"  - {path}" for path in self.changed)
        if self.missing:
            lines.append("Missing committed files:")
            lines.extend(f"  - {path}" for path in self.missing)
        if self.extra:
            lines.append("Stale committed files:")
            lines.extend(f"  - {path}" for path in self.extra)
        return "\n".join(lines)


def _copy_build_inputs(source_data: Path, generated_data: Path) -> None:
    for name in BUILD_DATA_INPUTS:
        src = source_data / name
        if src.exists():
            dst = generated_data / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    archive_src = source_data / "archive"
    if archive_src.exists():
        shutil.copytree(archive_src, generated_data / "archive", dirs_exist_ok=True)


def _run_build_data(repo_root: Path, generated_site: Path) -> None:
    generated_data = generated_site / "data"
    source_site = repo_root / "site"
    source_data = source_site / "data"
    _copy_build_inputs(source_data, generated_data)
    cmd = [
        sys.executable,
        str(source_site / "build_data.py"),
        "--results-dir",
        str(repo_root / "results"),
        "--registry",
        str(repo_root / "tasks" / "registry.json"),
        "--out",
        str(generated_data / "leaderboard.json"),
        "--badge-dir",
        str(generated_data / "badges"),
        "--og-dir",
        str(generated_site / "assets" / "og"),
        "--archive-dir",
        str(generated_data / "archive"),
        "--model-pages-dir",
        str(generated_site / "models"),
        "--task-pages-dir",
        str(generated_site / "tasks"),
        "--data-models-dir",
        str(generated_data / "models"),
        "--data-tasks-dir",
        str(generated_data / "tasks"),
        "--blog-dir",
        str(source_site / "blog"),
        "--gallery",
        str(source_data / "failure_gallery.json"),
        "--findings-out",
        str(generated_data / "findings.json"),
    ]
    subprocess.run(cmd, cwd=repo_root, check=True)


def generated_site_data_files(data_dir: Path) -> set[str]:
    """Return ``site/data``-relative files owned by ``site/build_data.py``."""
    files: set[str] = set()
    for name in GENERATED_TOP_LEVEL:
        path = data_dir / name
        if path.exists():
            files.add(name)
    for dirname in GENERATED_DIRS:
        root = data_dir / dirname
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                files.add(path.relative_to(data_dir).as_posix())
    return files


def compare_site_data(committed_data: Path, generated_data: Path) -> DriftReport:
    committed = generated_site_data_files(committed_data)
    generated = generated_site_data_files(generated_data)
    common = committed & generated
    changed = tuple(
        sorted(
            path
            for path in common
            if not filecmp.cmp(
                committed_data / path,
                generated_data / path,
                shallow=False,
            )
        )
    )
    missing = tuple(sorted(generated - committed))
    extra = tuple(sorted(committed - generated))
    return DriftReport(changed=changed, missing=missing, extra=extra)


def check_build_data_drift(repo_root: Path = REPO_ROOT) -> DriftReport:
    repo_root = repo_root.resolve()
    with tempfile.TemporaryDirectory(prefix="makerbench-site-data-") as tmp:
        generated_site = Path(tmp) / "site"
        _run_build_data(repo_root, generated_site)
        return compare_site_data(
            repo_root / SITE_DATA_DIR,
            generated_site / "data",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to check (default: inferred from this script).",
    )
    args = parser.parse_args(argv)

    report = check_build_data_drift(args.repo_root)
    print(report.format())
    return 1 if report.has_drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
