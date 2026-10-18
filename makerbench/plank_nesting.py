"""One-plank nesting for flute half-blanks (#1098).

2-D, grain-aligned (blank length along the plank length) shelf nesting of
rectangular half-blanks on a single plank, with tool/kerf spacing, edge
margins, optional tab zones, a bed-fit crosscut plan for a 48" ShopBot,
flip-registration dowel holes, a utilisation report, an SVG layout drawing and
a serial map CSV.

Blank sizes are *inputs* (inches). Nothing here reads instrument design data;
the flute family generator (#1097) supplies real half-blank sizes later.
Units: inches throughout. Coordinates: x along the grain (plank length),
y across the plank width, origin at a segment's lower-left corner.

Output names follow #1134:
``naf-flute-plank_<order-id>_r1_layout.svg`` / ``_serial-map.csv``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from xml.sax.saxutils import escape
from dataclasses import dataclass, field
from pathlib import Path

INSTRUMENT = "naf-flute-plank"
REVISION = "r1"
EPS = 1e-9
MAX_DIM = 1000.0  # inches; sanity bound for any caller-supplied length
MAX_COUNT = 500  # flutes per order line
MAX_TABS = 20
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


@dataclass(frozen=True)
class PlankSpec:
    length: float = 96.0
    width: float = 10.0
    thickness: float = 0.75
    bed_x: float = 48.0  # ShopBot nominal bed, UNVERIFIED (#1098)
    bed_y: float = 48.0
    bed_clearance: float = 2.0  # keep the stock this far inside the bed in X
    tool_diameter: float = 0.25  # profile cutter
    kerf_clearance: float = 0.0625  # extra gap beyond the tool diameter
    crosscut_kerf: float = 0.125  # saw kerf between segments
    edge_margin_x: float = 0.75  # segment ends (also holds the dowels)
    edge_margin_y: float = 0.5
    dowel_diameter: float = 0.25
    tabs_per_blank: int = 0  # 0 = no tabs (tape-only hold-down)
    tab_width: float = 0.5
    tab_height: float = 0.125  # material left uncut under the tab

    def __post_init__(self):
        for name in ("length", "width", "thickness", "bed_x", "bed_y", "tool_diameter",
                     "dowel_diameter", "edge_margin_x", "edge_margin_y"):
            _pos(name, getattr(self, name))
        for name in ("kerf_clearance", "crosscut_kerf", "bed_clearance", "tab_width", "tab_height"):
            _nonneg(name, getattr(self, name))
        if not isinstance(self.tabs_per_blank, int) or isinstance(self.tabs_per_blank, bool) \
                or not 0 <= self.tabs_per_blank <= MAX_TABS:
            raise NestingError(f"tabs_per_blank must be an integer 0..{MAX_TABS}")
        if self.length > MAX_DIM or self.width > MAX_DIM:
            raise NestingError(f"plank dimensions must be <= {MAX_DIM} in")
        if self.width > self.bed_y:
            raise NestingError("plank width exceeds bed Y; tiling in Y is not supported")
        if self.max_segment_length - 2 * self.edge_margin_x <= self.spacing:
            raise NestingError("bed (minus clearance and end margins) cannot hold any blank")
        if self.width - 2 * self.edge_margin_y <= 0:
            raise NestingError("edge margins leave no usable plank width")
        if self.dowel_diameter > 0.8 * self.edge_margin_x:
            raise NestingError("dowel diameter too large for the end margin")

    @property
    def spacing(self) -> float:
        return self.tool_diameter + self.kerf_clearance

    @property
    def max_segment_length(self) -> float:
        return self.bed_x - self.bed_clearance


@dataclass(frozen=True)
class BlankType:
    key: str
    length: float
    width: float


@dataclass
class Placement:
    serial: str
    key: str
    half: str  # "half-a" | "half-b"
    segment: int
    x: float
    y: float
    length: float
    width: float
    tabs_x: list = field(default_factory=list)


@dataclass
class Segment:
    index: int
    x0: float  # position on the full plank
    length: float
    dowels: list = field(default_factory=list)  # (x, y) segment coords


class NestingError(ValueError):
    pass


def _pos(name, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise NestingError(f"{name} must be a finite number > 0, got {v!r}")


def _nonneg(name, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        raise NestingError(f"{name} must be a finite number >= 0, got {v!r}")


def validate_order(plank: "PlankSpec", blanks: dict, order: list) -> None:
    if not blanks or not order:
        raise NestingError("blanks and order must be non-empty")
    for k, b in blanks.items():
        _pos(f"blank {k!r} length", b.length)
        _pos(f"blank {k!r} width", b.width)
        if b.length > MAX_DIM or b.width > MAX_DIM:
            raise NestingError(f"blank {k!r} exceeds {MAX_DIM} in")
    for line in order:
        c = line.get("count")
        if isinstance(c, bool) or not isinstance(c, int) or not 0 <= c <= MAX_COUNT:
            raise NestingError(f"count for {line.get('key')!r} must be an integer 0..{MAX_COUNT}, got {c!r}")
        if line.get("key") not in blanks:
            raise NestingError(f"no half-blank size for key {line.get('key')!r}")
    if sum(line["count"] for line in order) == 0:
        raise NestingError("order requests zero flutes")


def order_id(plank: PlankSpec, blanks: dict, order: list) -> str:
    canon = json.dumps(
        {
            "plank": plank.__dict__,
            "blanks": {k: [b.length, b.width] for k, b in sorted(blanks.items())},
            "order": sorted([o["key"], int(o["count"])] for o in order),
        },
        sort_keys=True,
    )
    return "o" + hashlib.sha256(canon.encode()).hexdigest()[:6]


def crosscut_plan(plank: PlankSpec) -> list[Segment]:
    """Split the plank into the fewest equal segments that fit the bed in X."""
    k, m = plank.crosscut_kerf, plank.max_segment_length
    n = max(1, math.ceil((plank.length + k) / (m + k) - EPS))  # closed form, no loop
    if n > 100:
        raise NestingError("crosscut plan needs more than 100 segments; check bed and plank sizes")
    seg_len = (plank.length - plank.crosscut_kerf * (n - 1)) / n
    if seg_len <= 2 * plank.edge_margin_x + plank.spacing:
        raise NestingError(
            f"infeasible crosscut: {n} segment(s) of {seg_len:.3f} in after "
            f"{plank.crosscut_kerf:g} in kerfs leave no usable length"
        )
    segs, x0 = [], 0.0
    for i in range(n):
        s = Segment(i, x0, seg_len)
        cy = plank.width / 2  # on the flip axis: y -> width - y keeps these holes fixed
        m = plank.edge_margin_x / 2
        s.dowels = [(m, cy), (seg_len - m, cy)]
        segs.append(s)
        x0 += seg_len + plank.crosscut_kerf
    return segs


def _usable_length(plank: PlankSpec, seg: Segment) -> float:
    return seg.length - 2 * plank.edge_margin_x


def expand_order(order: list, blanks: dict, oid: str) -> list[tuple]:
    """-> [(serial, BlankType, half)] two halves per ordered flute."""
    items, n = [], 0
    for line in order:
        if line["key"] not in blanks:
            raise NestingError(f"no half-blank size for key {line['key']!r}")
        for _ in range(int(line["count"])):
            n += 1
            serial = f"{oid}-{n:02d}"
            for half in ("half-a", "half-b"):
                items.append((serial, blanks[line["key"]], half))
    return items


def nest(plank: PlankSpec, blanks: dict, order: list, oid: str | None = None):
    """Shelf-nest all half-blanks. Returns (segments, placements, unplaced, oid).

    First-fit-decreasing by length into rows (lanes along the grain). Rows are
    stacked across the plank width; a row's height is its first blank's width.
    Blanks that cannot fit are returned in ``unplaced`` rather than dropped.
    """
    validate_order(plank, blanks, order)
    oid = order_id(plank, blanks, order) if oid is None else oid
    if not _SLUG.fullmatch(oid):
        raise NestingError(f"order_id must match {_SLUG.pattern!r}, got {oid!r}")
    segs = crosscut_plan(plank)
    items = sorted(expand_order(order, blanks, oid), key=lambda t: (-t[1].length, -t[1].width, t[0], t[2]))
    usable_w = plank.width - 2 * plank.edge_margin_y

    # rows: dict(seg, y, h, used)
    rows: list[dict] = []
    seg_y_used = {s.index: 0.0 for s in segs}
    placements: list[Placement] = []
    unplaced = []

    for serial, bt, half in items:
        placed = False
        for row in rows:
            seg = segs[row["seg"]]
            need = bt.length + (plank.spacing if row["used"] > 0 else 0.0)
            if (
                bt.width <= row["h"] + EPS
                and row["used"] + need <= _usable_length(plank, seg) + EPS
            ):
                x = plank.edge_margin_x + row["used"] + (plank.spacing if row["used"] > 0 else 0.0)
                placements.append(_place(plank, serial, bt, half, seg.index, x, row["y"]))
                row["used"] += need
                placed = True
                break
        if placed:
            continue
        for seg in segs:  # open a new row on the first segment with room
            gap = plank.spacing if seg_y_used[seg.index] > 0 else 0.0
            y = plank.edge_margin_y + seg_y_used[seg.index] + gap
            if (
                bt.length <= _usable_length(plank, seg) + EPS
                and seg_y_used[seg.index] + gap + bt.width <= usable_w + EPS
            ):
                rows.append({"seg": seg.index, "y": y, "h": bt.width, "used": bt.length})
                seg_y_used[seg.index] += gap + bt.width
                placements.append(_place(plank, serial, bt, half, seg.index, plank.edge_margin_x, y))
                placed = True
                break
        if not placed:
            unplaced.append((serial, bt.key, half, bt.length, bt.width))
    return segs, placements, unplaced, oid, rows


def _place(plank, serial, bt, half, seg, x, y):
    tabs = []
    n = plank.tabs_per_blank
    for i in range(n):
        tx = x + bt.length * (i + 0.5) / n
        tabs.append(round(tx, 4))
    return Placement(serial, bt.key, half, seg, round(x, 4), round(y, 4), bt.length, bt.width, tabs)


def report(plank, segs, placements, unplaced, rows, blanks=None):
    plank_area = plank.length * plank.width
    blank_area = sum(p.length * p.width for p in placements)
    seg_reports = []
    for s in segs:
        ps = [p for p in placements if p.segment == s.index]
        seg_rows = [r for r in rows if r["seg"] == s.index]
        free_rows = [
            {
                "row_y": r["y"],
                "free_length": round(_usable_length(plank, s) - r["used"], 4),
                "row_height": r["h"],
            }
            for r in seg_rows
        ]
        used_h = sum(r["h"] for r in seg_rows) + plank.spacing * max(len(seg_rows) - 1, 0)
        free_w = plank.width - 2 * plank.edge_margin_y - used_h
        seg_reports.append(
            {
                "segment": s.index,
                "x0": round(s.x0, 4),
                "length": round(s.length, 4),
                "blank_count": len(ps),
                "blank_area": round(sum(p.length * p.width for p in ps), 3),
                "utilisation": round(sum(p.length * p.width for p in ps) / (s.length * plank.width), 4),
                "free_row_gaps": free_rows,
                "free_strip_width": round(max(free_w - plank.spacing, 0.0), 4),
            }
        )
    suggestions = []
    if blanks:
        for sr in seg_reports:
            for g in sr["free_row_gaps"]:
                fits = sorted(
                    b.key
                    for b in blanks.values()
                    if b.length + plank.spacing <= g["free_length"] + EPS and b.width <= g["row_height"] + EPS
                )
                if fits:
                    suggestions.append(
                        {"segment": sr["segment"], "row_y": g["row_y"], "free_length": g["free_length"], "keys_that_fit": fits}
                    )
    return {
        "plank": {"length": plank.length, "width": plank.width, "thickness": plank.thickness},
        "segments": seg_reports,
        "segment_count": len(segs),
        "crosscut_kerf_loss_area": round(plank.crosscut_kerf * (len(segs) - 1) * plank.width, 3),
        "placed_blanks": len(placements),
        "unplaced_blanks": [dict(zip(("serial", "key", "half", "length", "width"), u)) for u in unplaced],
        "plank_area": round(plank_area, 3),
        "blank_area": round(blank_area, 3),
        "utilisation": round(blank_area / plank_area, 4),
        "leftover_area": round(plank_area - blank_area, 3),
        "fill_suggestions": suggestions,
        "assumptions": ["bed 48x48 in is UNVERIFIED (#1098)", "blank sizes are caller-supplied parameters"],
    }


def write_serial_map(path, plank, segs, placements):
    cols = [
        "serial", "key", "half", "segment", "x_in", "y_in_sideA", "y_in_sideB_flipped",
        "length_in", "width_in", "plank_x_in", "tab_x_in",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for p in sorted(placements, key=lambda p: (p.serial, p.half)):
            seg = segs[p.segment]
            flipped_y = plank.width - p.y - p.width  # flip about the long axis
            w.writerow([
                p.serial, p.key, p.half, p.segment, f"{p.x:.4f}", f"{p.y:.4f}", f"{flipped_y:.4f}",
                f"{p.length:.4f}", f"{p.width:.4f}", f"{seg.x0 + p.x:.4f}",
                ";".join(f"{t:.4f}" for t in p.tabs_x),
            ])


_PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#e15759", "#76b7b2", "#edc948", "#9c755f"]


def render_svg(plank, segs, placements, unplaced, rep, scale=12.0):
    pad, gap_between = 30.0, 28.0
    keys = sorted({p.key for p in placements})
    color = {k: _PALETTE[i % len(_PALETTE)] for i, k in enumerate(keys)}
    w = max(s.length for s in segs) * scale + 2 * pad
    h = len(segs) * (plank.width * scale + gap_between) + pad + 20
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}" font-family="sans-serif" font-size="10">',
        f'<text x="{pad}" y="16" font-size="12">One-plank layout, side A (grain along X, inches). '
        f'{plank.length:g}x{plank.width:g}x{plank.thickness:g} plank, {len(segs)} segment(s), '
        f'utilisation {rep["utilisation"] * 100:.1f}%. Bed size unverified.</text>',
    ]
    for s in segs:
        oy = pad + 10 + s.index * (plank.width * scale + gap_between)
        out.append(f'<g id="segment-{s.index}"><rect x="{pad}" y="{oy}" width="{s.length * scale:.1f}" height="{plank.width * scale:.1f}" fill="#f4ead5" stroke="#333"/>')
        out.append(
            f'<rect x="{pad + plank.edge_margin_x * scale:.1f}" y="{oy + plank.edge_margin_y * scale:.1f}" '
            f'width="{(s.length - 2 * plank.edge_margin_x) * scale:.1f}" height="{(plank.width - 2 * plank.edge_margin_y) * scale:.1f}" fill="none" stroke="#bbb" stroke-dasharray="3,3"/>'
        )
        for dx, dy in s.dowels:
            out.append(f'<circle cx="{pad + dx * scale:.1f}" cy="{oy + dy * scale:.1f}" r="{plank.dowel_diameter * scale / 2:.1f}" fill="none" stroke="#c00"/>')
        out.append(f'<text x="{pad}" y="{oy - 3}">segment {s.index} (plank x {s.x0:.2f}..{s.x0 + s.length:.2f}); red = flip dowels; arrow = grain</text>')
        out.append(f'<path d="M{pad + 4} {oy + plank.width * scale + 12} h40 m-6 -3 l6 3 l-6 3" stroke="#333" fill="none"/>')
        for p in (q for q in placements if q.segment == s.index):
            x, y = pad + (p.x) * scale, oy + p.y * scale
            out.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{p.length * scale:.1f}" height="{p.width * scale:.1f}" fill="{color[p.key]}" fill-opacity="0.7" stroke="#222"/>'
            )
            out.append(f'<text x="{x + 3:.1f}" y="{y + p.width * scale / 2 + 3:.1f}">{escape(p.serial)} {escape(p.key)} {escape(p.half)}</text>')
            for tx in p.tabs_x:
                out.append(f'<rect x="{pad + tx * scale - plank.tab_width * scale / 2:.1f}" y="{y - 2:.1f}" width="{plank.tab_width * scale:.1f}" height="4" fill="#000"/>')
                out.append(f'<rect x="{pad + tx * scale - plank.tab_width * scale / 2:.1f}" y="{y + p.width * scale - 2:.1f}" width="{plank.tab_width * scale:.1f}" height="4" fill="#000"/>')
        out.append("</g>")
    if unplaced:
        out.append(f'<text x="{pad}" y="{h - 6}" fill="#c00">UNPLACED: {len(unplaced)} blank(s) did not fit (see utilisation json)</text>')
    out.append("</svg>")
    return "\n".join(out)


def load_order(path):
    try:
        d = json.loads(Path(path).read_text())
        plank = PlankSpec(**d.get("plank", {}))
        blanks = {k: BlankType(k, v["length"], v["width"]) for k, v in d["blanks"].items()}
        return plank, blanks, d["order"], d.get("order_id")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        if isinstance(e, NestingError):
            raise
        raise NestingError(f"invalid order file: {e!r}") from e


def run(order_path, out_dir):
    plank, blanks, order, oid = load_order(order_path)
    segs, placements, unplaced, oid, rows = nest(plank, blanks, order, oid)
    rep = report(plank, segs, placements, unplaced, rows, blanks)
    stem = f"{INSTRUMENT}_{oid}_{REVISION}"
    root = Path(out_dir).resolve()
    folder = (root / stem).resolve()
    if folder.parent != root:
        raise NestingError("output folder escapes --out")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}_layout.svg").write_text(render_svg(plank, segs, placements, unplaced, rep))
    write_serial_map(folder / f"{stem}_serial-map.csv", plank, segs, placements)
    (folder / f"{stem}_utilisation.json").write_text(json.dumps(rep, indent=2) + "\n")
    return folder, rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("order", help="order JSON (plank, blanks, order)")
    ap.add_argument("--out", default="generated")
    a = ap.parse_args(argv)
    try:
        folder, rep = run(a.order, a.out)
    except NestingError as e:
        print(f"error: {e}")
        return 2
    print(f"{folder}: utilisation {rep['utilisation']:.1%}, unplaced {len(rep['unplaced_blanks'])}")
    return 1 if rep["unplaced_blanks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
