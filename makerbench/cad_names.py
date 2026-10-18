"""Descriptive names for generated CAD files (#1134).

``<instrument>_<variant>_<revision>_<part>.<ext>``: lowercase, hyphens inside a field,
underscores between fields, e.g. ``sambuca_base_r1_model.stl``. Output folders use the stem
(``sambuca_base_r1/``). The extension keeps its conventional case (``.FCStd``, ``.SLDPRT``).
"""

from __future__ import annotations

import re

#: The variant of a night that builds the instrument as registered (no variant picked).
BASE_VARIANT = "base"
#: The part name of a single-part deliverable (one file, possibly several bodies).
MODEL_PART = "model"
RULE = ("Name every CAD file <instrument>_<variant>_<revision>_<part>.<ext> (lowercase, hyphens inside a "
        "field, underscores between fields), never a bare name like assembly.FCStd or output.stl.")

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def field(value: str) -> str:
    """One name field: lowercase, runs of anything else become a single hyphen."""
    slug = _NOT_SLUG.sub("-", str(value).lower()).strip("-")
    if not slug:
        raise ValueError(f"{value!r} gives an empty CAD name field")
    return slug


def stem(instrument: str, variant: str = BASE_VARIANT, revision: str = "r1") -> str:
    return "_".join(field(part) for part in (instrument, variant, revision))


def name(stem_: str, part: str, ext: str) -> str:
    return f"{stem_}_{field(part)}.{ext.lstrip('.')}"


def follows(filename: str, stem_: str) -> bool:
    """Whether ``filename`` is ``<stem>_<part>.<ext>``."""
    return re.fullmatch(re.escape(stem_) + r"_[a-z0-9]+(?:-[a-z0-9]+)*\.[A-Za-z0-9]+", filename) is not None
