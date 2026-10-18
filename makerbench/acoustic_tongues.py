"""Measured profiles for two-ended wood tongue-drum plates; separate acoustic output.

Run with ``python -m makerbench.acoustic_tongues --help``. Reads saved BREP only,
never executes/recomputes a native document or reads its nominal tongue table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from shapely.geometry import LineString, Polygon, box

from .acoustic_beam import WOODS, acoustic_report
from .canary import CANARY


MAX_BYTES = 16 * 1024**2


def _archive(path):
    from .wood_assembly import PARTS, _check_document, bundle_roles

    allowed_links = ()
    if path.name.endswith("_assembly.FCStd"):
        files = bundle_roles(path.parent)
        if path.name != files["assembly"]:
            raise ValueError("Native input is not the declared assembly")
        allowed_links = {files[part] for part in PARTS}
    _check_document(path, allowed_links)
    return zipfile.ZipFile(path)


def saved_panel_brep(path: Path, object_name="PanelBody", panel_link="Top"):
    """Follow only the selected supplied relative assembly link; return saved shape.

    Existing native archive policy bounds expansion and rejects executable types,
    XML entities and external paths. Stored geometry may differ from unsaved edits.
    Assembly placement is irrelevant: extraction uses the panel's local XY frame.
    """
    path = path.resolve()
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Native input exceeds size limit")
    provenance = {
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "shape_basis": "saved BREP, no document recompute",
    }
    if path.suffix.lower() in (".brep", ".brp"):
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("BREP exceeds size limit")
        return path.read_bytes(), provenance
    if path.suffix.lower() != ".fcstd":
        raise ValueError("Require FCStd or BREP input")
    with _archive(path) as archive:
        root = ET.fromstring(archive.read("Document.xml"))
        node = root.find(f"./ObjectData/Object[@name='{panel_link}']")
        link = (
            None if node is None else node.find("./Properties/Property[@name='LinkedObject']/XLink")
        )
        if link is not None:
            for name in ("Scale", "ScaleVector"):
                prop = node.find(f"./Properties/Property[@name='{name}']")
                if prop is not None:
                    values = list(prop)[0].attrib.values()
                    if any(abs(float(v) - 1) > 1e-9 for v in values):
                        raise ValueError("Scaled panel links are unsupported")
            relative = Path(link.get("file", ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe panel link")
            linked = (path.parent / relative).resolve()
            if not linked.is_relative_to(path.parent):
                raise ValueError("Panel link escapes input bundle")
            data, part_provenance = saved_panel_brep(
                linked, link.get("name"), panel_link="__none__"
            )
            provenance["panel_sha256"] = part_provenance["input_sha256"]
            provenance["panel_object"] = link.get("name")
            return data, provenance
        obj = root.find(f"./ObjectData/Object[@name='{object_name}']")
        shape = None if obj is None else obj.find("./Properties/Property[@name='Shape']/Part")
        if shape is None or not shape.get("file"):
            raise ValueError(f"Missing saved BREP for object {object_name}")
        provenance["panel_object"] = object_name
        return archive.read(shape.get("file")), provenance


def extract_profiles(footprint, root_band_mm, elements=160):
    """Clip off specified end bands, isolate single-root tongues, sample widths.

    Clamp planes are modelling inputs, not automatically inferred fixture edges.
    Interior, one-ended connected components are tongues; perimeter wood is not.
    Widths sum all transverse wood intervals in the same connected tongue.
    A split distal section is a 1-D reduction assumption, recorded in the output.
    """
    if not math.isfinite(root_band_mm) or root_band_mm <= 0:
        raise ValueError("root_band_mm must be finite and positive")
    if type(elements) is not int or not 4 <= elements <= 512:
        raise ValueError("elements must be an integer in 4..512")
    t = footprint["thickness_mm"]
    if not math.isfinite(t) or abs(t - 12) > 1e-5:
        raise ValueError("Require measured 12 mm plate thickness")
    plate = Polygon(footprint["outer"], footprint["holes"])
    if not plate.is_valid or plate.is_empty:
        raise ValueError("Invalid plate footprint")
    xmin, ymin, xmax, ymax = plate.bounds
    left, right = xmin + root_band_mm, xmax - root_band_mm
    if right <= left:
        raise ValueError("Root bands leave no free plate span")
    interior = plate.intersection(box(left, ymin, right, ymax))
    pieces = [interior] if interior.geom_type == "Polygon" else list(interior.geoms)
    tongues = []
    for p in pieces:
        if p.geom_type != "Polygon" or p.area < 1e-6:
            continue
        a, b, c, d = p.bounds
        if b <= ymin + 1e-6 or d >= ymax - 1e-6:
            continue
        at_left, at_right = abs(a - left) < 1e-6, abs(c - right) < 1e-6
        if at_left and at_right:  # background wood spanning both end bands
            continue
        if not (at_left or at_right):
            raise ValueError("Detached island has no clamped root")
        if p.interiors:
            raise ValueError("Perforated tongues are unsupported")
        length = c - a
        if length <= 0:
            raise ValueError("Zero free length")
        edges = np.linspace(0, length, elements + 1)
        centers = (edges[1:] + edges[:-1]) / 2
        widths, root_y, interval_counts = [], None, []
        for s in centers:
            x = left + s if at_left else right - s
            section = p.intersection(LineString([(x, ymin - 1), (x, ymax + 1)]))
            lines = (
                [section]
                if section.geom_type == "LineString"
                else [g for g in getattr(section, "geoms", []) if g.geom_type == "LineString"]
            )
            if not lines or sum(g.length for g in lines) <= 0:
                raise ValueError("Empty transverse tongue section")
            if root_y is None and len(lines) != 1:
                raise ValueError("Require one connected root section per tongue")
            interval_counts.append(len(lines))
            widths.append(sum(g.length for g in lines))
            if root_y is None:
                root_y = section.centroid.y
        # Stable identity station, independent of the requested FEM mesh density.
        identity_x = left + min(5.0, length / 10) if at_left else right - min(5.0, length / 10)
        identity = p.intersection(LineString([(identity_x, ymin - 1), (identity_x, ymax + 1)]))
        if identity.geom_type != "LineString":
            raise ValueError("Ambiguous tongue identity at root station")
        root_y = identity.centroid.y
        proximal = [w for w, s in zip(widths, centers) if s <= length * 0.4]
        distal = [w for w, s in zip(widths, centers) if s >= length * 0.45]
        tongues.append(
            dict(
                side="left" if at_left else "right",
                root_center_y_mm=root_y,
                free_length_mm=length,
                neck_width_mm=min(proximal),
                head_width_mm=max(distal),
                thickness_mm=t,
                edges_mm=edges.tolist(),
                widths_mm=widths,
                max_section_intervals=max(interval_counts),
                profile_method="sum of transverse wood intervals at each element midpoint",
            )
        )
    if not tongues:
        raise ValueError("No single-root tongues isolated at the supplied clamp planes")
    tongues.sort(key=lambda p: (p["root_center_y_mm"], p["side"]))
    for i, tongue in enumerate(tongues, 1):
        tongue["tongue_id"] = f"T{i:02d}"
    return dict(
        units="mm",
        thickness_mm=t,
        root_band_mm=root_band_mm,
        clamp_planes_x_mm=[left, right],
        elements_per_tongue=elements,
        footprint_deflection_mm=footprint.get("deflection_mm"),
        neck_definition="minimum width in proximal 40% of free span",
        head_definition="maximum width in distal 55% of free span",
        tongues=tongues,
    )


def extract_native(path, root_band_mm, elements=160, object_name="PanelBody", panel_link="Top"):
    from .wood_assembly import _run_worker

    data, provenance = saved_panel_brep(Path(path), object_name, panel_link)
    with tempfile.TemporaryDirectory(prefix="acoustic-brep-") as temporary:
        work = Path(temporary)
        inputs, output = work / "inputs", work / "output"
        inputs.mkdir()
        output.mkdir()
        (inputs / "panel.brep").write_bytes(data)
        _run_worker(Path(__file__).with_name("_acoustic_footprint_worker.py"), {}, output, inputs)
        footprint = json.loads((output / "footprint.json").read_text())
    profiles = extract_profiles(footprint, root_band_mm, elements)
    profiles["provenance"] = dict(
        provenance,
        brep_sha256=hashlib.sha256(data).hexdigest(),
        freecad_version=footprint["freecad_version"],
    )
    return profiles


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--root-band-mm",
        type=float,
        required=True,
        help="Explicit rigid-clamp offset from each plate end, in local X",
    )
    parser.add_argument("--elements", type=int, default=160)
    parser.add_argument("--species", nargs="+", choices=WOODS, default=["white_oak"])
    parser.add_argument("--object", default="PanelBody", help="Saved BREP object in a panel FCStd")
    parser.add_argument("--panel-link", default="Top", help="Top-panel link in assembly FCStd")
    parser.add_argument("--cache", type=Path, help="Optional stub cross-check disk cache")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        extraction = extract_native(
            args.input, args.root_band_mm, args.elements, args.object, args.panel_link
        )
        report = acoustic_report(extraction, args.species, args.cache)
    except (ValueError, OSError, RuntimeError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        report = dict(
            schema="makerbench-acoustic-estimate-v1",
            canary=CANARY,
            check="acoustic_prediction",
            status="skipped",
            reason=str(exc),
            included_in_base_checks=False,
            included_in_published_rates=False,
            tongues=[],
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"acoustic_prediction: {report['status']}; {len(report['tongues'])} tongues; {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
