"""Read-only access to a local instrument repo for New Run (#1109 variants, #1108 brief).

Instrument repos live under ``--instruments-root`` as ``<family dir>/<slug>``; a
registry task names its own ``repo_path``. Every lookup is contained the same way
as the design workbench: no ``..``, nothing under a ``private`` directory, and no
symlink anywhere on the path.

Variants follow the family model in #1097: ``variants.csv`` (design intent, one row
per product) when the repo has one, else the older ``family-spec.csv`` (one row per
family member, which is what the library's "N variants" counts). Only columns named
from a public design-intent vocabulary are read (an allowlist), and spreadsheets are
never opened.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

VARIANT_FILES = ("variants.csv", "family-spec.csv")
ID_COLUMNS = ("variant_id", "member_id", "id", "variant")
LABEL_COLUMNS = ("label", "name", "role", "tuning", "key", "root_note")
#: The public design-intent vocabulary (#1097). A column is read only when EVERY word of its
#: name is in this list or is a number: an allowlist, so customer, contact, price, retail or
#: any other unexpected column can never reach a response, however it is spelled.
PUBLIC_COLUMN_WORDS = frozenset("""
    id variant variants member family packet instrument label name role status
    tuning tuned temperament key root note notes_count scale mode system pitch hz frequency midi octave
    string strings course courses tongue tongues hole holes bar bars key_count fret frets
    count number total overall min max nominal target predicted range
    length width height depth diameter radius thickness gauge size envelope volume
    bore chamber cavity port gu mouth window wall
    bowl neck body head shell top bottom side sides back front soundboard bridge nut
    node position spacing tension kerf
    material materials stock wood species grain direction steel brass
    in inch mm cm m kg g lbf n deg cm3 l
""".split())


def public_column(name: str) -> bool:
    words = [w for w in re.split(r"[^a-z0-9]+", str(name).lower()) if w]
    return bool(words) and all(w in PUBLIC_COLUMN_WORDS or w.isdigit() for w in words)


class RepoFileError(ValueError):
    """A repo table that cannot be read safely (too large, malformed, or not text)."""
MAX_ROWS = 64
MAX_FILE_BYTES = 512 * 1024
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _walk(root: Path, parts: Iterable[str]) -> Optional[Path]:
    current = root
    for part in parts:
        if not _SAFE.match(part) or part.lower() == "private":
            return None
        current = current / part
        if current.is_symlink() or not current.is_dir():
            return None
    return current.resolve() if current.resolve().is_relative_to(root.resolve()) else None


def find_repo(instruments_root: Optional[Path], slug: str, repo_path: Optional[str] = None) -> Optional[Path]:
    """The local repo for a library slug or registry task, or None."""
    if instruments_root is None or not Path(instruments_root).is_dir():
        return None
    root = Path(instruments_root)
    if repo_path:
        found = _walk(root, Path(repo_path).parts)
        if found is not None:
            return found
    if not _SAFE.match(slug or ""):
        return None
    for family in sorted(root.iterdir()):
        if family.name.startswith((".", "_")) or family.name.lower() == "private" or family.is_symlink():
            continue
        found = _walk(root, (family.name, slug))
        if found is not None:
            return found
    return None


def repo_file(repo: Path, relative: str) -> Optional[Path]:
    """A regular file inside the repo, reached without symlinks or private dirs."""
    parts = Path(relative).parts
    if not parts:
        return None
    folder = _walk(repo, parts[:-1]) if len(parts) > 1 else repo
    if folder is None or not _SAFE.match(parts[-1]):
        return None
    path = folder / parts[-1]
    return path if path.is_file() and not path.is_symlink() else None


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Allowlisted columns and up to MAX_ROWS rows; RepoFileError for an unreadable table."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise RepoFileError(f"{path.name} is larger than {MAX_FILE_BYTES // 1024} KB.")
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            columns = [c for c in (reader.fieldnames or []) if c and public_column(c)]
            rows = []
            for index, row in enumerate(reader):
                if index >= MAX_ROWS:
                    break
                rows.append({c: (row.get(c) or "").strip() for c in columns})
    except (csv.Error, UnicodeDecodeError) as error:
        raise RepoFileError(f"{path.name} could not be read as a table: {str(error)[:120]}") from None
    return columns, rows


def variants(repo: Optional[Path]) -> dict[str, Any]:
    """``{"source": file name or None, "variants": [{id, label, detail}]}``."""
    if repo is None:
        return {"source": None, "variants": []}
    for name in VARIANT_FILES:
        path = repo_file(repo, name)
        if path is None:
            continue
        try:
            columns, rows = read_csv(path)
        except RepoFileError as error:
            return {"source": name, "variants": [], "error": str(error)}
        id_col = next((c for c in ID_COLUMNS if c in columns), None)
        if id_col is None:
            continue
        label_col = next((c for c in LABEL_COLUMNS if c in columns and c != id_col), None)
        detail_cols = [c for c in columns if c not in (id_col, label_col, "status", "release_notes", "notes")][:4]
        out = []
        for row in rows:
            if not row.get(id_col):
                continue
            out.append({
                "id": row[id_col][:80],
                "label": (row.get(label_col) or "")[:120] if label_col else "",
                "status": row.get("status", "")[:60],
                "detail": " · ".join(f"{c.replace('_', ' ')} {row[c]}" for c in detail_cols if row.get(c))[:240],
            })
        if out:
            return {"source": name, "variants": out}
    return {"source": None, "variants": []}


def registry_repo_path(registry: Iterable[Mapping[str, Any]], task_id: Optional[str]) -> Optional[str]:
    for task in registry:
        if task.get("id") == task_id:
            return str(task.get("repo_path") or "") or None
    return None
