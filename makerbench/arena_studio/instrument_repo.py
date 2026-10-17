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


def variants(repo: Optional[Path], *, flute: bool = False) -> dict[str, Any]:
    """``{"source": file name or None, "variants": [{id, label, detail}]}``.

    A flute family lists ids and labels only: its dimensions come from the
    manager-cleaned design-table CSVs, not from repo files (#1108 privacy rule).
    """
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
                "detail": "" if flute else " · ".join(f"{c.replace('_', ' ')} {row[c]}" for c in detail_cols if row.get(c))[:240],
            })
        if out:
            return {"source": name, "variants": out}
    return {"source": None, "variants": []}


def registry_repo_path(registry: Iterable[Mapping[str, Any]], task_id: Optional[str]) -> Optional[str]:
    for task in registry:
        if task.get("id") == task_id:
            return str(task.get("repo_path") or "") or None
    return None


# --- brief auto-fill (#1108) ---------------------------------------------------

import json  # noqa: E402 - kept with the section that uses it

SPEC_KEYS = re.compile(
    r"(length|width|height|depth|diameter|thickness|envelope|scale|tuning|key|root|range|"
    r"strings?_?count|string_count|holes?|tongues?|bore|bars?|keys_count|notes?)", re.I)
TABLE_NAMES = re.compile(r"(hole|tongue|tuning|scale|string|fret|pitch|bar|note)[^/]*\.csv$", re.I)
NEVER_FILES = re.compile(r"(bom|sourcing|price|cost|supplier|rfq|order|invoice|customer|sales|quote)", re.I)
FLUTE_WORDS = re.compile(r"flute|kena|quena|xiao|dizi|shakuhachi|fujara|bansuri|ocarina|whistle|recorder|kaval|siku|gemshorn|pistalka", re.I)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
MAX_SPEC = 14
MAX_TABLE_ROWS = 24
MAX_IMAGES = 6
TABLE_DIRS = ("", "data", "docs")


def _scalars(node: Any, path: str = "", depth: int = 0):
    if depth > 4:
        return
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _scalars(value, f"{path}.{key}" if path else str(key), depth + 1)
    elif isinstance(node, (str, int, float)) and not isinstance(node, bool) and path:
        yield path, node


def _manifest_fields(repo: Path) -> tuple[Optional[str], list[dict[str, str]]]:
    path = repo_file(repo, "capstone-manifest.json")
    if path is None or path.stat().st_size > MAX_FILE_BYTES:
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, []
    title = None
    for key in ("instrument", "title", "name"):
        value = data.get(key) if isinstance(data, Mapping) else None
        if isinstance(value, str):
            title = value
            break
        if isinstance(value, Mapping) and isinstance(value.get("name"), str):
            title = value["name"]
            break
    fields = []
    for key_path, value in _scalars(data):
        leaf = key_path.rsplit(".", 1)[-1]
        if not public_column(leaf) or not SPEC_KEYS.search(leaf) or len(str(value)) > 80:
            continue
        if isinstance(value, str) and value.startswith(("http", "/", ".")):
            continue
        fields.append({"label": leaf.replace("_", " "), "value": str(value), "source": f"capstone-manifest.json › {key_path}"})
        if len(fields) >= MAX_SPEC:
            break
    return title, fields


def _family_row(repo: Path, variant: Optional[str]) -> list[dict[str, str]]:
    for name in VARIANT_FILES:
        path = repo_file(repo, name)
        if path is None:
            continue
        try:
            columns, rows = read_csv(path)
        except RepoFileError:
            continue  # an unreadable table contributes nothing to the brief
        id_col = next((c for c in ID_COLUMNS if c in columns), None)
        if not rows or id_col is None:
            continue
        row = next((r for r in rows if variant and r.get(id_col) == variant), rows[0])
        return [
            {"label": c.replace("_", " "), "value": row[c], "source": f"{name} › {row[id_col]}"}
            for c in columns
            if c != id_col and row.get(c) and not re.search(r"status|release|notes|role|packet|path", c) and len(row[c]) <= 80
        ][:MAX_SPEC]
    return []


def _tables(repo: Path) -> list[dict[str, Any]]:
    out = []
    for folder_name in TABLE_DIRS:
        folder = _walk(repo, Path(folder_name).parts) if folder_name else repo
        if folder is None:
            continue
        for path in sorted(folder.glob("*.csv")):
            if path.is_symlink() or NEVER_FILES.search(path.name) or not TABLE_NAMES.search(path.name):
                continue
            try:
                columns, rows = read_csv(path)
            except RepoFileError:
                continue
            if columns and rows:
                relative = path.relative_to(repo.resolve()).as_posix()
                out.append({"source": relative, "columns": columns, "rows": rows[:MAX_TABLE_ROWS]})
    return out[:4]


def repo_images(repo: Path) -> list[str]:
    folder = _walk(repo, ("images",))
    if folder is None:
        return []
    names = sorted(p.name for p in folder.iterdir()
                   if p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGE_SUFFIXES and _SAFE.match(p.name))
    names.sort(key=lambda n: (n != "hero-render.png", "family" not in n, n))
    return names[:MAX_IMAGES]


def is_flute(name: str) -> bool:
    return bool(FLUTE_WORDS.search(name or ""))


def brief(repo: Optional[Path], *, name: str, slug: str, variant: Optional[str] = None,
          registry_brief: str = "") -> dict[str, Any]:
    """Suggested brief fields, each with the file it came from. Tony edits; nothing is final."""
    out: dict[str, Any] = {"fields": [], "tables": [], "images": [], "text": "", "sources": [], "notes": []}
    title = None
    if repo is not None:
        title, manifest_fields = _manifest_fields(repo)
        if is_flute(f"{slug} {name}"):
            # Flute dimensions come only from the manager-cleaned design-table CSVs (never the workbook).
            out["notes"].append("Flute dimensions and tables come only from the manager-cleaned design-table CSVs, so none are read from the repo here.")
        else:
            out["fields"] = (_family_row(repo, variant) + manifest_fields)[:MAX_SPEC]
            out["tables"] = _tables(repo)
        out["images"] = repo_images(repo)
    lines = [registry_brief.strip()] if registry_brief.strip() else [f"Model the {title or name}" + (f", variant {variant}" if variant else "") + "."]
    if out["fields"]:
        lines.append("Key dimensions and tuning: " + "; ".join(f"{f['label']} {f['value']}" for f in out["fields"][:8]) + ".")
    for table in out["tables"][:2]:
        lines.append(f"Follow the table in {table['source']} ({len(table['rows'])} rows).")
    out["text"] = "\n\n".join(lines)
    out["sources"] = sorted({f["source"].split(" › ")[0] for f in out["fields"]} | {t["source"] for t in out["tables"]}
                            | ({"arena registry"} if registry_brief.strip() else set()))
    return out
