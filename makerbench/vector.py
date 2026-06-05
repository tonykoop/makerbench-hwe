"""Native 2D vector (SVG/DXF) parsing, measurement, and canonical hashing.

This is the public side of the first *native* laser-2d grading path (#27). The
existing laser task grades an OpenSCAD-extruded thin solid; this module instead
parses a real 2D cut file — SVG or DXF — into deterministic closed polygons that
a task grader measures with open math. It is pure, headless, and depends only on
the standard library plus ``shapely`` (already a core dependency); DXF is read by
a small hand-rolled reader so no new dependency is added.

Coordinate frames and units are deliberately explicit:

  * **SVG** — millimetre user units (``width``/``height`` MUST carry an explicit
    ``mm`` suffix and equal the ``viewBox`` extents, i.e. 1 user unit = 1 mm);
    frame +X right, +Y down (SVG native).
  * **DXF** — ASCII DXF with ``$INSUNITS == 4`` (millimetres); frame +X right,
    +Y up (DXF native).

Both decode into the *same* shapely-polygon representation (a `MultiPolygon` of
exterior profiles each carrying its interior cut-outs), so one format-agnostic
grader serves both. Anything outside the restricted profile — open/broken paths,
curves, transforms, ambiguous units — is rejected with a clear, levelled reason
rather than silently coerced, which is what lets Level-1 act as a real gate.

The vector artifact fingerprint `vector_sha256` is the anti-cheat anchor for a
submitted cut file, the 2D analogue of `geometry.canonical_sha256`: it is
invariant to subpath emission order and ring start-point, but not to the actual
geometry. Cross-format hash equality (the same part as SVG vs DXF) is **not**
guaranteed and not required — it is a per-artifact anchor, not a content address.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient

# Default rounding for canonicalization: 4 decimal places == 0.1 micron, matching
# geometry.canonical_sha256's vertex precision.
_NDIGITS = 4

_NUMBER_RE = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_PATH_TOKEN_RE = re.compile(r"[A-Za-z]|" + _NUMBER_RE)
_LENGTH_RE = re.compile(r"^\s*(" + _NUMBER_RE + r")\s*([a-z%]*)\s*$")


@dataclass
class VectorRejection:
    """A single reason a vector artifact is not gradable.

    `reason` is a stable machine code (used by graders/tests); `detail` is a
    human string. The presence of any rejection fails Level-1 (STRUCTURAL).
    """

    reason: str  # open_path | curve_unsupported | ambiguous_units |
    #             transform_unsupported | self_intersecting |
    #             rounded_rect_unsupported | relative_coords | empty | malformed
    detail: str = ""


@dataclass
class ParsedVector:
    """The deterministic result of parsing one restricted SVG/DXF cut file."""

    fmt: str  # "svg" | "dxf"
    width_mm: float
    height_mm: float
    rings: list[list[tuple[float, float]]] = field(default_factory=list)
    geometry: MultiPolygon = field(default_factory=lambda: MultiPolygon())
    rejections: list[VectorRejection] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejections and not self.geometry.is_empty


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def parse_vector(text: str) -> ParsedVector:
    """Sniff the artifact format and dispatch to the SVG or DXF parser.

    Total by contract: any malformed artifact yields a ParsedVector carrying a
    Level-1 rejection, never a raised exception, so a bad submission grades as a
    structural failure instead of aborting the run.
    """
    fmt = detect_format(text)
    try:
        head = text.lstrip()[:512]
        if "<svg" in head or head.startswith("<?xml"):
            return parse_svg(text)
        if "SECTION" in text or "ENTITIES" in text or head.startswith("0"):
            return parse_dxf(text)
        return ParsedVector("unknown", 0.0, 0.0,
                            rejections=[VectorRejection("malformed",
                                        "not recognizable as SVG or ASCII DXF")])
    except Exception as exc:  # noqa: BLE001 - parsing must never escape to the grader
        return ParsedVector(fmt, 0.0, 0.0,
                            rejections=[VectorRejection("malformed",
                                        f"unhandled parse error: {exc}")])


def detect_format(text: str) -> str:
    """Return 'svg' or 'dxf' for a submitted artifact (best-effort sniff)."""
    head = text.lstrip()[:512]
    if "<svg" in head or head.startswith("<?xml"):
        return "svg"
    return "dxf"


# --------------------------------------------------------------------------- #
# SVG
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse_length_mm(value: str | None) -> tuple[float | None, str]:
    """Parse an SVG length that MUST be in explicit mm. Returns (mm, error)."""
    if value is None:
        return None, "missing"
    m = _LENGTH_RE.match(value)
    if not m:
        return None, f"unparseable length {value!r}"
    num, unit = m.group(1), m.group(2)
    if unit != "mm":
        return None, (f"length {value!r} is not in explicit mm "
                      f"(unit={unit or 'none'})")
    return float(num), ""


def parse_svg(text: str) -> ParsedVector:
    """Parse a restricted-profile SVG into closed polygons.

    Restricted profile: root `<svg>` with `width`/`height` in explicit mm equal
    to the `viewBox` extents; allowed elements `<path>` (absolute `M L H V Z`,
    every subpath closed), `<rect>` (no `rx`/`ry`), `<polygon>`; no `transform`
    anywhere; no curves or relative path commands.
    """
    pv = ParsedVector("svg", 0.0, 0.0)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        pv.rejections.append(VectorRejection("malformed", f"XML parse error: {exc}"))
        return pv
    if _local(root.tag) != "svg":
        pv.rejections.append(VectorRejection("malformed", "root element is not <svg>"))
        return pv

    w_mm, w_err = _parse_length_mm(root.get("width"))
    h_mm, h_err = _parse_length_mm(root.get("height"))
    if w_mm is None or h_mm is None:
        pv.rejections.append(VectorRejection(
            "ambiguous_units",
            f"svg width/height must be explicit mm (width: {w_err or 'ok'}; "
            f"height: {h_err or 'ok'})"))
        return pv
    pv.width_mm, pv.height_mm = w_mm, h_mm

    viewbox = root.get("viewBox")
    if not viewbox:
        pv.rejections.append(VectorRejection(
            "ambiguous_units", "svg has no viewBox; mm mapping is undefined"))
        return pv
    try:
        vb = [float(x) for x in re.split(r"[\s,]+", viewbox.strip())]
    except ValueError:
        pv.rejections.append(VectorRejection("ambiguous_units", f"bad viewBox {viewbox!r}"))
        return pv
    if len(vb) != 4 or abs(vb[2] - w_mm) > 1e-6 or abs(vb[3] - h_mm) > 1e-6:
        pv.rejections.append(VectorRejection(
            "ambiguous_units",
            f"viewBox extents {vb[2:] if len(vb) == 4 else vb} must equal "
            f"width/height in mm ({w_mm} x {h_mm}) so 1 user unit == 1 mm"))
        return pv

    rings: list[list[tuple[float, float]]] = []
    for el in root.iter():
        if el is root:
            continue
        if el.get("transform") is not None:
            pv.rejections.append(VectorRejection(
                "transform_unsupported",
                f"<{_local(el.tag)}> carries a transform; coordinates must be "
                "absolute mm"))
            return pv
        tag = _local(el.tag)
        if tag == "g" or tag in ("defs", "title", "desc", "metadata", "style"):
            continue
        if tag == "path":
            sub, rej = _svg_path_rings(el.get("d", ""))
            if rej is not None:
                pv.rejections.append(rej)
                return pv
            rings.extend(sub)
        elif tag == "rect":
            ring, rej = _svg_rect_ring(el)
            if rej is not None:
                pv.rejections.append(rej)
                return pv
            rings.append(ring)
        elif tag == "polygon":
            ring, rej = _svg_polygon_ring(el.get("points", ""))
            if rej is not None:
                pv.rejections.append(rej)
                return pv
            rings.append(ring)
        else:
            pv.rejections.append(VectorRejection(
                "malformed", f"unsupported SVG element <{tag}>"))
            return pv

    return _finalize(pv, rings)


def _svg_path_rings(d: str):
    """Tokenize a restricted `<path>` d into closed rings."""
    tokens = _PATH_TOKEN_RE.findall(d or "")
    rings: list[list[tuple[float, float]]] = []
    cur_ring: list[tuple[float, float]] | None = None
    closed = False
    cx = cy = 0.0
    i = 0
    n = len(tokens)

    def _num(idx):
        return float(tokens[idx])

    while i < n:
        cmd = tokens[i]
        if not cmd.isalpha():
            return [], VectorRejection("malformed", f"path data starts with a number near {cmd!r}")
        i += 1
        if cmd in "mlhvqcsta" and cmd.islower() and cmd != "z":
            return [], VectorRejection("relative_coords",
                                       f"relative path command '{cmd}' is not allowed")
        up = cmd.upper()
        if up in ("C", "S", "Q", "T", "A"):
            return [], VectorRejection("curve_unsupported",
                                       f"curve command '{cmd}' is not allowed")
        if up == "Z":
            if cur_ring is not None:
                closed = True
                rings.append((cur_ring, True))  # type: ignore[arg-type]
                cur_ring = None
            continue
        if up == "M":
            if cur_ring is not None:
                rings.append((cur_ring, closed))  # type: ignore[arg-type]
            if i + 1 >= n:
                return [], VectorRejection("malformed", "M without coordinates")
            cx, cy = _num(i), _num(i + 1)
            i += 2
            cur_ring = [(cx, cy)]
            closed = False
            while i + 1 < n and not tokens[i].isalpha():  # implicit L pairs
                cx, cy = _num(i), _num(i + 1)
                i += 2
                cur_ring.append((cx, cy))
        elif up == "L":
            if cur_ring is None:
                return [], VectorRejection("malformed", "L before M")
            while i + 1 < n and not tokens[i].isalpha():
                cx, cy = _num(i), _num(i + 1)
                i += 2
                cur_ring.append((cx, cy))
        elif up == "H":
            if cur_ring is None:
                return [], VectorRejection("malformed", "H before M")
            while i < n and not tokens[i].isalpha():
                cx = _num(i)
                i += 1
                cur_ring.append((cx, cy))
        elif up == "V":
            if cur_ring is None:
                return [], VectorRejection("malformed", "V before M")
            while i < n and not tokens[i].isalpha():
                cy = _num(i)
                i += 1
                cur_ring.append((cx, cy))
        else:
            return [], VectorRejection("malformed", f"unsupported path command '{cmd}'")

    if cur_ring is not None:
        rings.append((cur_ring, closed))  # type: ignore[arg-type]

    out: list[list[tuple[float, float]]] = []
    for coords, is_closed in rings:  # type: ignore[misc]
        if not is_closed:
            return [], VectorRejection("open_path",
                                       "a subpath is not closed with Z")
        out.append(coords)
    return out, None


def _svg_rect_ring(el):
    try:
        x = float(el.get("x", "0"))
        y = float(el.get("y", "0"))
        w = float(el.get("width"))
        h = float(el.get("height"))
        # rx/ry parsed here too so a malformed value is a rejection, not a raise.
        rx = float(el.get("rx", "0") or 0)
        ry = float(el.get("ry", "0") or 0)
    except (TypeError, ValueError):
        return None, VectorRejection("malformed", "rect has missing/invalid numeric attribute")
    if rx > 0 or ry > 0:
        return None, VectorRejection("rounded_rect_unsupported",
                                     "rounded rects (rx/ry) are not allowed")
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], None


def _svg_polygon_ring(points: str):
    nums = re.findall(_NUMBER_RE, points or "")
    if len(nums) < 6 or len(nums) % 2 != 0:
        return None, VectorRejection("malformed", "polygon needs >=3 coordinate pairs")
    coords = [(float(nums[i]), float(nums[i + 1])) for i in range(0, len(nums), 2)]
    return coords, None


# --------------------------------------------------------------------------- #
# DXF (hand-rolled, restricted profile, no new dependency)
# --------------------------------------------------------------------------- #
_DXF_CURVE_ENTITIES = {"CIRCLE", "ARC", "ELLIPSE", "SPLINE"}


def parse_dxf(text: str) -> ParsedVector:
    """Parse a restricted-profile ASCII DXF into closed polygons.

    Restricted profile: ASCII DXF with header `$INSUNITS == 4` (mm); geometry as
    closed `LWPOLYLINE`/`POLYLINE` entities only. Curve entities (CIRCLE/ARC/
    ELLIPSE/SPLINE) and open polylines are rejected.
    """
    pv = ParsedVector("dxf", 0.0, 0.0)
    try:
        pairs = _dxf_pairs(text)
    except ValueError as exc:
        pv.rejections.append(VectorRejection("malformed", str(exc)))
        return pv

    insunits = _dxf_insunits(pairs)
    if insunits != 4:
        pv.rejections.append(VectorRejection(
            "ambiguous_units",
            f"$INSUNITS must be 4 (mm); got "
            f"{insunits if insunits is not None else 'absent'}"))
        return pv

    rings, rej = _dxf_entity_rings(pairs)
    if rej is not None:
        pv.rejections.append(rej)
        return pv
    return _finalize(pv, rings)


def _dxf_pairs(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    pairs: list[tuple[str, str]] = []
    i = 0
    while i + 1 < len(lines):
        code = lines[i].strip()
        val = lines[i + 1].strip()
        if not code:
            i += 1
            continue
        pairs.append((code, val))
        i += 2
    return pairs


def _dxf_insunits(pairs: list[tuple[str, str]]) -> int | None:
    for idx, (code, val) in enumerate(pairs):
        if code == "9" and val == "$INSUNITS" and idx + 1 < len(pairs):
            try:
                return int(float(pairs[idx + 1][1]))
            except ValueError:
                return None
    return None


def _dxf_entity_rings(pairs: list[tuple[str, str]]):
    """Walk the ENTITIES section, building closed rings from polylines."""
    section: str | None = None
    rings: list[list[tuple[float, float]]] = []

    # Polyline accumulation state.
    cur_entity: str | None = None
    lwp_x: list[float] = []
    lwp_y: list[float] = []
    lwp_flag = 0
    poly_open = False  # old-style POLYLINE/VERTEX gathering
    poly_flag = 0
    poly_pts: list[tuple[float, float]] = []
    vx: float | None = None
    vy: float | None = None

    def flush_lwpolyline():
        nonlocal lwp_x, lwp_y, lwp_flag
        coords = list(zip(lwp_x, lwp_y))
        result = (coords, lwp_flag)
        lwp_x, lwp_y, lwp_flag = [], [], 0
        return result

    i = 0
    n = len(pairs)
    while i < n:
        code, val = pairs[i]
        if code == "0":
            # Finish any pending LWPOLYLINE before switching entity.
            if cur_entity == "LWPOLYLINE":
                coords, flag = flush_lwpolyline()
                if not (flag & 1):
                    return [], VectorRejection("open_path",
                                               "an LWPOLYLINE is not closed (flag 70 bit 1 unset)")
                rings.append(coords)
            if val == "SECTION":
                section = pairs[i + 1][1] if i + 1 < n and pairs[i + 1][0] == "2" else None
                cur_entity = None
            elif val == "ENDSEC":
                section = None
                cur_entity = None
            elif section == "ENTITIES":
                if val in _DXF_CURVE_ENTITIES:
                    return [], VectorRejection("curve_unsupported",
                                               f"{val} entity is not allowed (polygonal cut paths only)")
                if val == "LWPOLYLINE":
                    cur_entity = "LWPOLYLINE"
                    lwp_x, lwp_y, lwp_flag = [], [], 0
                elif val == "POLYLINE":
                    cur_entity = "POLYLINE"
                    poly_open = True
                    poly_flag = 0
                    poly_pts = []
                    vx = vy = None
                elif val == "VERTEX":
                    cur_entity = "VERTEX"
                    vx = vy = None
                elif val == "SEQEND":
                    if poly_open:
                        if not (poly_flag & 1):
                            return [], VectorRejection("open_path",
                                                       "a POLYLINE is not closed (flag 70 bit 1 unset)")
                        rings.append(list(poly_pts))
                        poly_open = False
                    cur_entity = None
                else:
                    cur_entity = val
            i += 1
            continue

        # Attribute lines for the current entity. A non-numeric group value is a
        # malformed artifact (Level-1 rejection), never an escaping exception.
        try:
            if cur_entity == "LWPOLYLINE":
                if code == "10":
                    lwp_x.append(float(val))
                elif code == "20":
                    lwp_y.append(float(val))
                elif code == "70":
                    lwp_flag = int(float(val))
            elif cur_entity == "POLYLINE":
                if code == "70":
                    poly_flag = int(float(val))
            elif cur_entity == "VERTEX":
                if code == "10":
                    vx = float(val)
                elif code == "20":
                    vy = float(val)
                    if vx is not None:
                        poly_pts.append((vx, vy))
        except ValueError:
            return [], VectorRejection(
                "malformed", f"non-numeric DXF group value {val!r} (code {code})")
        i += 1

    return rings, None


# --------------------------------------------------------------------------- #
# Geometry resolution + measurement helpers (oracle-free)
# --------------------------------------------------------------------------- #
def _finalize(pv: ParsedVector, rings: list[list[tuple[float, float]]]) -> ParsedVector:
    if not rings:
        pv.rejections.append(VectorRejection("empty", "no closed geometry found"))
        return pv
    geom, rej = _build_geometry(rings)
    if rej is not None:
        pv.rejections.append(rej)
        return pv
    pv.rings = rings
    pv.geometry = geom
    if pv.fmt == "dxf":
        minx, miny, maxx, maxy = geom.bounds
        pv.width_mm, pv.height_mm = maxx - minx, maxy - miny
    return pv


def _build_geometry(rings: list[list[tuple[float, float]]]):
    """Resolve raw closed rings into a MultiPolygon of exteriors with holes.

    Classification is by even-odd containment depth, so interior cut-outs become
    holes of the profile that encloses them. Invalid (self-intersecting) or
    degenerate rings are rejected.
    """
    polys: list[Polygon] = []
    for r in rings:
        coords = r[:-1] if len(r) > 1 and r[0] == r[-1] else r
        if len(coords) < 3:
            return None, VectorRejection("malformed", "a ring has fewer than 3 distinct vertices")
        poly = Polygon(coords)
        if not poly.is_valid:
            return None, VectorRejection("self_intersecting",
                                         "a ring is self-intersecting or degenerate")
        if poly.area <= 0:
            return None, VectorRejection("malformed", "a ring encloses zero area")
        polys.append(poly)

    # Classify rings by even-odd containment *depth*, using whole-polygon
    # containment (`covers`, which tolerates shared boundaries) rather than a
    # single representative point — a representative point of a large outer
    # profile can coincidentally land inside one of its own cut-outs and corrupt
    # the parity, so point-in-polygon is not reliable for this.
    n = len(polys)
    contains = [[False] * n for _ in range(n)]
    for a in range(n):
        for b in range(n):
            if a != b and polys[b].covers(polys[a]):
                contains[b][a] = True
    depth = [sum(1 for b in range(n) if contains[b][a]) for a in range(n)]

    exteriors = [i for i in range(n) if depth[i] % 2 == 0]
    holes = [i for i in range(n) if depth[i] % 2 == 1]
    ext_holes: dict[int, list[list[tuple[float, float]]]] = {i: [] for i in exteriors}
    for h in holes:
        containers = [i for i in exteriors if contains[i][h]]
        if not containers:
            continue
        owner = min(containers, key=lambda i: polys[i].area)
        ext_holes[owner].append(list(polys[h].exterior.coords))

    result = [Polygon(list(polys[i].exterior.coords), ext_holes[i]) for i in exteriors]
    return MultiPolygon(result), None


def outer_bbox_mm(pv: ParsedVector) -> tuple[float, float]:
    """(width, height) of the overall outer profile bounding box, in mm."""
    minx, miny, maxx, maxy = pv.geometry.bounds
    return maxx - minx, maxy - miny


def outer_profile_count(pv: ParsedVector) -> int:
    return len(pv.geometry.geoms)


def cutout_polygons(pv: ParsedVector) -> list[Polygon]:
    """Every interior cut-out as its own Polygon, across all profiles."""
    out: list[Polygon] = []
    for poly in pv.geometry.geoms:
        for interior in poly.interiors:
            out.append(Polygon(interior))
    return out


def total_cut_area_mm2(pv: ParsedVector) -> float:
    return float(sum(c.area for c in cutout_polygons(pv)))


def developed_area_mm2(pv: ParsedVector) -> float:
    """Net solid area (outer profiles minus cut-outs), in mm^2."""
    return float(pv.geometry.area)


def slot_sizes_mm(pv: ParsedVector) -> list[tuple[float, float]]:
    """(long_side, short_side) of each cut-out's axis-aligned bbox, in mm."""
    sizes: list[tuple[float, float]] = []
    for c in cutout_polygons(pv):
        minx, miny, maxx, maxy = c.bounds
        a, b = maxx - minx, maxy - miny
        sizes.append((max(a, b), min(a, b)))
    return sizes


def measured_slot_width_mm(pv: ParsedVector) -> float:
    """Smallest cut-out short-side (the binding slip-fit width), in mm."""
    sizes = slot_sizes_mm(pv)
    return min((s for _, s in sizes), default=0.0)


def min_web_mm(pv: ParsedVector) -> float:
    """Minimum web/bridge: closest spacing among cut-outs and to each edge."""
    webs: list[float] = []
    for poly in pv.geometry.geoms:
        holes = [Polygon(r) for r in poly.interiors]
        edge = poly.exterior
        for a in range(len(holes)):
            webs.append(float(holes[a].distance(edge)))
            for b in range(a + 1, len(holes)):
                webs.append(float(holes[a].distance(holes[b])))
    return min(webs) if webs else float("inf")


# --------------------------------------------------------------------------- #
# Canonicalization + hash
# --------------------------------------------------------------------------- #
def _fmt(value: float, ndigits: int) -> str:
    v = round(float(value), ndigits)
    if v == 0:  # collapse -0.0
        v = 0.0
    return f"{v:.{ndigits}f}"


def _rotate_to_min(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not coords:
        return coords
    k = min(range(len(coords)), key=lambda i: coords[i])
    return coords[k:] + coords[:k]


def canonicalize_rings(pv: ParsedVector, ndigits: int = _NDIGITS) -> list:
    """A deterministic, order-independent canonical form of the geometry.

    Each profile is oriented (exterior CCW, holes CW), coordinates rounded, and
    every ring rotated to start at its lexicographically smallest vertex. Holes
    and profiles are then sorted, so subpath emission order and ring start-point
    never change the result — only the actual geometry does.
    """
    profiles = []
    for poly in pv.geometry.geoms:
        poly = orient(poly, sign=1.0)  # exterior CCW, holes CW
        ext = [( _fmt(x, ndigits), _fmt(y, ndigits) )
               for x, y in list(poly.exterior.coords)[:-1]]
        ext = _rotate_to_min(ext)
        holes = []
        for interior in poly.interiors:
            h = [( _fmt(x, ndigits), _fmt(y, ndigits) )
                 for x, y in list(interior.coords)[:-1]]
            holes.append(_rotate_to_min(h))
        holes.sort()
        profiles.append((ext, holes))
    profiles.sort()
    return profiles


def vector_sha256(pv: ParsedVector, ndigits: int = _NDIGITS) -> str:
    """Stable geometry fingerprint of a vector artifact (anti-cheat anchor)."""
    payload = repr(canonicalize_rings(pv, ndigits))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
