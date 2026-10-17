"""The instrument library as New Run's catalog (#1107).

The library (https://tonykoop.github.io/library.html) is the public catalog; the
Control Room is the local workshop. New Run lists instruments from a local snapshot
of it so the picker works offline, and shows when the snapshot was taken.

A snapshot is built from either source, whichever is newer for the person running it:

- a library manifest JSON (``instrument-showcase-library-manifest-v1``, written by the
  library generator's ``scripts/generate_library.py``);
- the published ``library.html`` page itself: every card carries the same fields as
  ``data-*`` attributes plus its slug, title, hero image and variant count.

Refreshing is explicit (a button in New Run); nothing here touches the network on its own.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from ..run_log_io import atomic_write_json

SNAPSHOT_SCHEMA = "makerbench-control-room-library-v1"
MANIFEST_SCHEMA = "instrument-showcase-library-manifest-v1"
LIBRARY_URL = "https://tonykoop.github.io/library.html"
SNAPSHOT_PATH = Path("control-room") / "library.json"
MAX_PAGE_BYTES = 8 * 1024 * 1024
#: Local manifest and cached snapshot files: the real library is well under 1 MB.
MAX_JSON_BYTES = 4 * 1024 * 1024
ENTRY_FIELDS = ("slug", "title", "family", "family_label", "subtype", "variants", "hero", "explorer",
                "cad", "completeness", "wolfram", "status")
FILTERS = ("family", "completeness", "cad", "wolfram")
#: The library page's own filter values, offered even when no card has them today.
KNOWN_FILTERS = {
    "family": ("idiophone", "drum", "string", "wind"),
    "completeness": ("complete", "near", "in-progress", "scaffold"),
    "cad": ("inline-glb", "external-glb", "gltf", "none"),
    "wolfram": ("live", "owner-only", "pending"),
}
_VARIANTS = re.compile(r"(\d+)\s+variants?")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class LibraryError(ValueError):
    """A library source that cannot become a snapshot."""


class _CardParser(HTMLParser):
    """Collects ``<article class="card">`` entries from the library page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self._card: Optional[dict[str, Any]] = None
        self._depth = 0
        self._field: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = {key: value or "" for key, value in attrs}
        classes = set(a.get("class", "").split())
        if tag == "article" and "card" in classes:
            self._card = {
                "family": a.get("data-family", ""), "status": a.get("data-status", ""), "cad": a.get("data-cad", ""),
                "wolfram": a.get("data-wolfram", ""), "completeness": a.get("data-completeness", ""),
                "has_explorer": a.get("data-has-explorer") == "yes", "title": "", "slug": "", "hero": "",
                "explorer": "", "family_label": "", "acoustic_class": "", "variants": None,
            }
            self._depth = 1
            return
        if self._card is None:
            return
        if tag == "article":
            self._depth += 1
        if tag == "a" and "card-title" in classes:
            self._card["explorer"] = a.get("href", "")
            self._field = "title"
        elif tag == "img" and not self._card["hero"]:
            self._card["hero"] = a.get("src", "")
        elif tag == "span" and "badge-family" in classes:
            self._field = "family_label"
        elif tag == "span" and "badge-acoustic" in classes:
            self._field = "acoustic_class"
        elif tag == "span" and "badge-fam-count" in classes:
            self._field = "variants"
        elif tag == "code" and self._field is None:
            self._field = "slug"

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return
        if tag in ("a", "span", "code"):
            self._field = None
        if tag == "article":
            self._depth -= 1
            if self._depth == 0:
                self.cards.append(self._card)
                self._card = None

    def handle_data(self, data: str) -> None:
        if self._card is None or self._field is None:
            return
        if self._field == "variants":
            match = _VARIANTS.search(data)
            if match:
                self._card["variants"] = int(match.group(1))
        else:
            self._card[self._field] = (self._card[self._field] + data).strip()


def _entry(**fields: Any) -> dict[str, Any]:
    slug = str(fields.get("slug") or "")
    return {
        "slug": slug,
        "title": str(fields.get("title") or slug),
        "family": str(fields.get("family") or ""),
        "family_label": str(fields.get("family_label") or fields.get("family") or ""),
        "subtype": str(fields.get("acoustic_class") or ""),
        "variants": fields.get("variants") if isinstance(fields.get("variants"), int) else 1,
        "hero": str(fields.get("hero") or ""),
        "explorer": str(fields.get("explorer") or ""),
        "cad": str(fields.get("cad") or "none"),
        "completeness": str(fields.get("completeness") or ""),
        "wolfram": str(fields.get("wolfram") or ""),
        "status": str(fields.get("status") or ""),
    }


def entries_from_html(text: str) -> list[dict[str, Any]]:
    parser = _CardParser()
    parser.feed(text)
    entries = [_entry(**card) for card in parser.cards if _SLUG.match(card["slug"] or "")]
    if not entries:
        raise LibraryError("No instrument cards found in the library page.")
    return entries


def entries_from_manifest(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema") != MANIFEST_SCHEMA:
        raise LibraryError(f"Library manifest schema must be {MANIFEST_SCHEMA}.")
    out = []
    for item in payload.get("entries") or []:
        if not isinstance(item, Mapping) or not _SLUG.match(str(item.get("slug") or "")):
            continue
        out.append(_entry(
            slug=item.get("slug"), title=item.get("title"), family=item.get("family"),
            acoustic_class=item.get("acoustic_class"), variants=item.get("family_count"),
            hero=item.get("hero_image_path"), explorer=item.get("explorer_path"), cad=item.get("cad"),
            completeness=item.get("completeness_state"), wolfram=item.get("wolfram_state"), status=item.get("status"),
        ))
    return out


def build_snapshot(entries: Iterable[Mapping[str, Any]], *, source: str, fetched_at: Optional[datetime] = None) -> dict:
    unique: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        unique.setdefault(entry["slug"], entry)
    return {
        "schema": SNAPSHOT_SCHEMA,
        "source": source,
        "fetched_utc": (fetched_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat(),
        "entries": list(unique.values()),
    }


def snapshot_path(runs_root: Path) -> Path:
    """``runs/control-room/library.json``, refusing a symlink anywhere on the way out of ``runs/``."""
    root = Path(runs_root)
    folder = root / SNAPSHOT_PATH.parent
    for path in (root, folder, folder / SNAPSHOT_PATH.name):
        if path.is_symlink():
            raise LibraryError(f"Refusing a symlinked library snapshot path ({path.name}).")
    if folder.exists() and not folder.resolve().is_relative_to(root.resolve()):
        raise LibraryError("The library snapshot folder resolves outside runs/.")
    return folder / SNAPSHOT_PATH.name


def read_json_bounded(path: Path, limit: int = MAX_JSON_BYTES) -> Any:
    """Parse a regular (non-symlink) JSON file of at most ``limit`` bytes, or raise LibraryError."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise LibraryError(f"{path.name} is not a regular file.")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise LibraryError(f"{path.name} is larger than {limit // (1024 * 1024)} MB.")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise LibraryError(f"{path.name} is not valid JSON: {error}") from None


def _valid_entry(entry: Any) -> Optional[dict[str, Any]]:
    if not isinstance(entry, Mapping) or not _SLUG.match(str(entry.get("slug") or "")):
        return None
    if any(not isinstance(entry.get(key, ""), (str, int)) or isinstance(entry.get(key), bool) for key in ENTRY_FIELDS):
        return None
    return _entry(**{key: entry.get(key) for key in ENTRY_FIELDS if key != "subtype"}, acoustic_class=entry.get("subtype"))


def load_snapshot(runs_root: Path) -> Optional[dict]:
    """The cached snapshot, or None when it is absent, unsafe, oversized or malformed (offline fallback)."""
    try:
        payload = read_json_bounded(snapshot_path(runs_root))
    except (LibraryError, OSError):  # unreadable (e.g. permissions) counts as no cache
        return None
    if not isinstance(payload, Mapping) or payload.get("schema") != SNAPSHOT_SCHEMA:
        return None
    raw = payload.get("entries")
    if not isinstance(raw, list):
        return None
    entries = [entry for entry in map(_valid_entry, raw) if entry is not None]
    if not entries:
        return None
    return {"schema": SNAPSHOT_SCHEMA, "source": str(payload.get("source") or ""),
            "fetched_utc": str(payload.get("fetched_utc") or ""), "entries": entries}


def fetch_page(url: str = LIBRARY_URL, timeout: float = 20.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "makerbench-control-room"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        data = response.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise LibraryError("The library page is larger than expected.")
    return data.decode("utf-8", errors="replace")


def refresh(runs_root: Path, *, manifest_path: Optional[Path] = None,
            fetch: Callable[[], str] = fetch_page, now: Optional[datetime] = None) -> dict:
    """Rebuild the snapshot from a manifest file when given, else from the published page.

    The previous snapshot is replaced only after the new one parsed and validated.
    """
    target = snapshot_path(runs_root)
    if manifest_path is not None:
        payload = read_json_bounded(Path(manifest_path))
        if not isinstance(payload, Mapping):
            raise LibraryError("The library manifest is not a JSON object.")
        snapshot = build_snapshot(entries_from_manifest(payload), source=f"manifest:{Path(manifest_path).name}", fetched_at=now)
    else:
        snapshot = build_snapshot(entries_from_html(fetch()), source=LIBRARY_URL, fetched_at=now)
    if not snapshot["entries"]:
        raise LibraryError("The library source has no usable instrument entries.")
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path(runs_root)  # re-check after mkdir: nothing may have become a symlink
    atomic_write_json(target, snapshot)
    return snapshot


def registry_links(registry: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Library slug -> arena registry task id, through the registry's ``repo_path``."""
    links: dict[str, str] = {}
    for task in registry:
        task_id = str(task.get("id") or "")
        tail = str(task.get("repo_path") or "").rstrip("/").rsplit("/", 1)[-1]
        for key in (tail, task_id):
            if key and task_id:
                links.setdefault(key, task_id)
    return links


def filter_options(entries: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    entries = list(entries)
    return {key: sorted({str(e[key]) for e in entries if e.get(key)}) for key in FILTERS}
