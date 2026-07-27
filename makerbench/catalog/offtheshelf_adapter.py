"""Back-compat adapter: read the fasteners catalog from an offtheshelf checkout.

``makerbench/catalog/fasteners.json`` is the packaged, committed source of
truth that every existing task, grader, and test reads today via
``makerbench.parts.load_catalog()`` / ``PartsLibrary`` -- nothing about that
default path changes as part of this module.

``tonykoop/offtheshelf`` is now the upstream single source of truth for the
mechanical parts those fasteners describe (offtheshelf issue #4, "Migrate
makerbench-hwe catalog/fasteners.json into offtheshelf"). This module is the
read side of that relationship: given a local checkout of ``offtheshelf``, it
converts ``components/mechanical/MB-{SHCS,HSI}-*/metadata.yaml`` back into the
exact ``fasteners.json`` record shape -- the inverse of offtheshelf's
``generators/migrate_makerbench_fasteners.py``. A catalog built this way is
field-for-field identical to the committed ``fasteners.json`` for every part
it currently carries (see ``tests/test_offtheshelf_adapter.py``).

This is opt-in only: set the ``MAKERBENCH_OFFTHESHELF_ROOT`` environment
variable (or pass ``offtheshelf_root=...`` to ``load_catalog()`` /
``PartsLibrary()``) to a local offtheshelf checkout. Leave both unset -- the
default for every existing task, grader, and test -- and nothing here is even
imported; behavior is byte-identical to before this module existed.

``tolerances`` / ``units`` / ``notes`` are catalog-level metadata, not
per-part data, and offtheshelf's ``schema/component.schema.json`` has no slot
for them on an individual part. They therefore can't be *derived* from an
offtheshelf checkout -- they're pinned here to the values already published in
``fasteners.json``, and should be kept in lockstep by hand if that file's
catalog-level metadata ever changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Catalog-level metadata that has no per-part home in offtheshelf's schema.
# Keep in lockstep with makerbench/catalog/fasteners.json's top-level fields.
CATALOG_VERSION = "0.1.0"
UNITS = "mm"
NOTES = (
    "Synthetic MakerBench part numbers with realistic ISO 4762 / common "
    "heat-set insert dimensions. Local catalog: reproducible, offline, and "
    "free of commercial-data IP concerns. Clearance holes follow ISO 273 "
    "(close/normal/free fit)."
)
TOLERANCES = {"clearance_hole_dia_mm": 0.35, "boss_hole_dia_mm": 0.35}

# physical.* keys to copy back onto the flat fasteners.json record, per
# category. Order doesn't matter (dict), just completeness vs. the original
# migrate_makerbench_fasteners.py field lists.
_SHCS_PHYSICAL_FIELDS = (
    "thread",
    "pitch_mm",
    "length_mm",
    "head_dia_mm",
    "head_height_mm",
    "clearance_hole_close_mm",
    "clearance_hole_normal_mm",
    "clearance_hole_free_mm",
    "tap_drill_mm",
    "material",
    "drive",
)
_HSI_PHYSICAL_FIELDS = (
    "thread",
    "length_mm",
    "outer_dia_mm",
    "boss_hole_dia_mm",
    "min_boss_wall_mm",
    "material",
    "notes",
)


def metadata_to_fastener_record(metadata: dict[str, Any]) -> dict[str, Any]:
    """Convert one offtheshelf mechanical ``metadata.yaml`` dict back into a
    ``fasteners.json`` part record.

    Inverse of offtheshelf's ``generators/migrate_makerbench_fasteners.py``
    (``_socket_head_metadata`` / ``_heat_set_insert_metadata``).
    """

    tags = set(metadata.get("tags", []))
    physical = metadata.get("physical", {})

    if "socket-head-cap-screw" in tags:
        category = "socket_head_cap_screw"
        fields = _SHCS_PHYSICAL_FIELDS
    elif "heat-set-insert" in tags:
        category = "heat_set_insert"
        fields = _HSI_PHYSICAL_FIELDS
    else:
        raise ValueError(
            f"{metadata.get('mpn')!r}: unrecognized fastener category "
            f"(expected 'socket-head-cap-screw' or 'heat-set-insert' in tags, "
            f"got {sorted(tags)!r})"
        )

    record: dict[str, Any] = {"part_number": metadata["mpn"], "category": category}
    for field in fields:
        if field in physical:
            record[field] = physical[field]
    return record


def _resolve_files(component_dir: Path, root: Path, files: dict[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in (files or {}).items():
        if value is None:
            resolved[key] = None
            continue
        try:
            resolved[key] = (component_dir / str(value)).resolve().relative_to(
                root.resolve()
            ).as_posix()
        except ValueError:
            resolved[key] = (component_dir / str(value)).resolve().as_posix()
    return resolved


def _offtheshelf_catalog_record(
    metadata: dict[str, Any], component_dir: Path, root: Path
) -> dict[str, Any]:
    files = metadata.get("files") or {}
    return {
        "id": metadata.get("mpn"),
        # `part_number` is the key PartsLibrary.search()/.get() index on --
        # required for this record to be usable through the same tool surface
        # as bearings/tubing/fasteners.
        "part_number": metadata.get("mpn"),
        "mpn": metadata.get("mpn"),
        "manufacturer": metadata.get("manufacturer"),
        "description": metadata.get("description"),
        "category": metadata.get("category"),
        "package": metadata.get("package"),
        "tags": metadata.get("tags", []),
        "files": files,
        "resolved_files": _resolve_files(component_dir, root, files),
        "physical": metadata.get("physical", {}),
        "electrical": metadata.get("electrical", {}),
        "provenance": metadata.get("provenance", {}),
    }


def load_offtheshelf_catalog(
    offtheshelf_root: str | Path,
    *,
    category: str | None = None,
    package: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    query: str | None = None,
    redistributable_only: bool = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Search the FULL offtheshelf catalog (mechanical *and* electronic parts),
    not just the MB-* fasteners ``load_offtheshelf_fasteners`` covers.

    This mirrors the read contract offtheshelf documents for its consumers --
    same filters (category, package, tags, free-text query,
    redistributable_only) and largely the same record shape as
    ``offtheshelf.adapters.makerbench_hwe_parts_search.parts_search()`` in that
    repo (offtheshelf issue #6). It's reimplemented locally here rather than
    importing the sibling repo's package at runtime, so makerbench-hwe stays
    hermetic (no ``sys.path``/cross-repo import dependency, consistent with
    how ``load_offtheshelf_fasteners`` above reimplements the inverse of
    offtheshelf's migration script instead of importing it).

    This is additive and does not replace ``PartsLibrary.search()``/``.get()``
    with a different default catalog -- existing graders (e.g.
    ``enclosure_fastened``) keep depending on the legacy MB-* fastener record
    shape from ``load_offtheshelf_fasteners``. Use this to query the broader
    catalog explicitly, e.g. via ``PartsLibrary(offtheshelf_root=...).search_offtheshelf_catalog(...)``
    or the ``parts_search_offtheshelf`` tool from ``makerbench.parts.as_offtheshelf_catalog_tool``.
    """

    import yaml  # lazy: only needed on this opt-in path

    root = Path(offtheshelf_root)
    components_dir = root / "components"
    if not components_dir.is_dir():
        raise FileNotFoundError(
            f"{components_dir} does not exist -- is {root} an offtheshelf checkout?"
        )

    wanted_tags = {tag.lower() for tag in tags or ()}
    terms = {term.lower() for term in (query or "").split() if term.strip()}
    matches: list[dict[str, Any]] = []

    for metadata_path in sorted(components_dir.glob("*/*/metadata.yaml")):
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        if category is not None and metadata.get("category") != category:
            continue
        if package is not None and metadata.get("package") != package:
            continue
        if redistributable_only and not metadata.get("provenance", {}).get(
            "redistributable", False
        ):
            continue
        item_tags = {str(tag).lower() for tag in metadata.get("tags", [])}
        if wanted_tags and not wanted_tags.issubset(item_tags):
            continue
        if terms:
            searchable = " ".join(
                str(value)
                for value in (
                    metadata.get("mpn"),
                    metadata.get("manufacturer"),
                    metadata.get("description"),
                    metadata.get("package"),
                    metadata.get("category"),
                    " ".join(str(tag) for tag in metadata.get("tags", [])),
                )
                if value is not None
            ).lower()
            if not all(term in searchable for term in terms):
                continue

        matches.append(_offtheshelf_catalog_record(metadata, metadata_path.parent, root))
        if limit is not None and len(matches) >= limit:
            break

    return matches


def load_offtheshelf_fasteners(offtheshelf_root: str | Path) -> dict[str, Any]:
    """Build a ``fasteners.json``-shaped catalog dict from an offtheshelf checkout.

    Only ``components/mechanical/MB-*/metadata.yaml`` parts are considered --
    those are exactly the ones migrated from (and meant to supersede)
    ``fasteners.json``. Other mechanical parts (e.g. generic ISO 4762 screws
    from a parametric generator, hex standoffs, ...) are out of scope for this
    adapter; they don't have MB-* part numbers that any existing task/grader
    references.
    """

    import yaml  # lazy: only needed on this opt-in path

    root = Path(offtheshelf_root)
    mechanical_root = root / "components" / "mechanical"
    if not mechanical_root.is_dir():
        raise FileNotFoundError(
            f"{mechanical_root} does not exist -- is {root} an offtheshelf checkout?"
        )

    parts = []
    for metadata_path in sorted(mechanical_root.glob("MB-*/metadata.yaml")):
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        parts.append(metadata_to_fastener_record(metadata))

    if not parts:
        raise FileNotFoundError(
            f"no components/mechanical/MB-*/metadata.yaml parts found under {root}"
        )

    return {
        "catalog_version": CATALOG_VERSION,
        "units": UNITS,
        "notes": NOTES,
        "tolerances": dict(TOLERANCES),
        "parts": parts,
    }
