"""Public grader primitives for the woodworking/CNC-router frontier ladder (issue #32).

These are the public, oracle-free building blocks for the deferred ``frontier_ladders``
woodworking rungs registered in ``tasks/registry.json`` (see ``docs/WOODWORKING_LADDER.md``).
Each primitive is pure and deterministic and grades entirely from public params — no gold
answer, held-out fixture, or private threshold is ever consulted — so they run in public CI
exactly like the sheet-metal and laser/vector ladder primitives.

The *families* stay deferred/design-only because their new geometry needs a private oracle
(makerbench-oracles#13); only these primitives ship now. A future live grader would AND each
primitive's booleans into the standard L2/L3/L4 levels.
"""

from __future__ import annotations


def dogbone_relief_check(params: dict) -> dict[str, float]:
    """Check adequacy of dogbone / T-bone relief geometry at CNC interior corners.

    A CNC router bit is cylindrical and cannot cut a perfect 90-degree interior corner;
    a circular dogbone relief (or T-bone variant) is pocketed at each internal corner so
    the mating part's corner can fully seat. The relief circle's radius must be at least
    the tool radius.

    ``params`` keys:
      - ``tool_radius_mm`` (float): radius of the router bit.
      - ``has_dogbone`` (bool): whether the design declares dogbone reliefs.
      - ``dogbone_radius_mm`` (float): declared radius of each relief circle.
      - ``corner_count`` (int): total number of interior corners in the design.
      - ``dogbone_corner_count`` (int): number of corners that have a relief.

    Returns ``{dogbone_present, dogbone_radius_adequate, all_corners_relieved, feasible}``
    (1.0 = yes, 0.0 = no).
    """
    tool_r = float(params["tool_radius_mm"])
    has_dogbone = bool(params.get("has_dogbone", False))
    dogbone_r = float(params.get("dogbone_radius_mm", 0.0))
    corner_count = int(params.get("corner_count", 0))
    dogbone_corner_count = int(params.get("dogbone_corner_count", 0))

    radius_adequate = has_dogbone and dogbone_r >= tool_r
    all_corners = has_dogbone and (corner_count == 0 or dogbone_corner_count >= corner_count)
    feasible = has_dogbone and radius_adequate and all_corners

    return {
        "dogbone_present": float(has_dogbone),
        "dogbone_radius_adequate": float(radius_adequate),
        "all_corners_relieved": float(all_corners),
        "feasible": float(feasible),
    }


def sheet_yield_feasible(params: dict) -> dict[str, float]:
    """Area heuristic: do declared part footprints fit within the stock sheet?

    Pure params-derived (no mesh, no oracle). Uses a conservative area test: the sum of
    all part footprints plus a per-part minimum-gap border must be ≤ the stock sheet area.
    The gap border accounts for tool path spacing between parts and around the sheet edge.

    ``params`` keys:
      - ``stock_width_mm`` (float): stock sheet width.
      - ``stock_height_mm`` (float): stock sheet height.
      - ``parts`` (list of ``{"width_mm", "height_mm"}``): declared part footprints.
      - ``min_gap_mm`` (float, default 6.0): minimum spacing between parts and to edges.

    Returns ``{total_part_area_mm2, stock_area_mm2, yield_fraction, parts_count,
    feasible}`` (``feasible`` = 1.0 iff the area heuristic passes).
    """
    stock_w = float(params["stock_width_mm"])
    stock_h = float(params["stock_height_mm"])
    parts = list(params.get("parts", []))
    min_gap = float(params.get("min_gap_mm", 6.0))

    stock_area = stock_w * stock_h
    n = len(parts)

    part_area = sum(
        float(p["width_mm"]) * float(p["height_mm"]) for p in parts
    )

    # Each part footprint is padded by min_gap on each side, so its effective footprint
    # is (w + min_gap)(h + min_gap).
    padded_area = sum(
        (float(p["width_mm"]) + min_gap) * (float(p["height_mm"]) + min_gap)
        for p in parts
    )

    yield_fraction = part_area / stock_area if stock_area > 0 else 0.0
    feasible = padded_area <= stock_area

    return {
        "total_part_area_mm2": round(part_area, 6),
        "stock_area_mm2": round(stock_area, 6),
        "yield_fraction": round(yield_fraction, 6),
        "parts_count": float(n),
        "feasible": float(feasible),
    }


def joinery_tool_radius_check(params: dict) -> dict[str, float]:
    """Check CNC tool-radius feasibility constraints for a declared joinery joint.

    Pure params-derived (no mesh, no oracle). A CNC-routed joint slot (finger, mortise, or
    half-lap) must be wide enough for the router bit to enter and cut cleanly: the slot
    width must be at least twice the tool radius (so the bit fits with space on both sides).
    The cut depth must also not exceed the material thickness.

    ``params`` keys:
      - ``joinery_type`` (str): ``"finger"``, ``"mortise_tenon"``, or ``"half_lap"``.
      - ``slot_width_mm`` (float): width of the narrowest joint slot or mortise.
      - ``tool_radius_mm`` (float): CNC router bit radius.
      - ``depth_mm`` (float): cut depth.
      - ``material_thickness_mm`` (float): stock thickness.

    Returns ``{tool_fits_slot, depth_feasible, slot_adequate_for_toolpath, feasible}``
    (1.0 = yes, 0.0 = no).
    """
    slot_w = float(params["slot_width_mm"])
    tool_r = float(params["tool_radius_mm"])
    depth = float(params["depth_mm"])
    thickness = float(params["material_thickness_mm"])

    tool_fits = slot_w >= 2.0 * tool_r
    depth_ok = depth <= thickness
    # Adequate slot: the tool can make a full-width pass (tool diameter ≤ slot width).
    slot_adequate = slot_w >= 2.0 * tool_r
    feasible = tool_fits and depth_ok

    return {
        "tool_fits_slot": float(tool_fits),
        "depth_feasible": float(depth_ok),
        "slot_adequate_for_toolpath": float(slot_adequate),
        "feasible": float(feasible),
    }
