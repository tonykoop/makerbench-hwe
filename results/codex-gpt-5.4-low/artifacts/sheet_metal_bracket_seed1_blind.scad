$fn = 96;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
outside_length_a_mm = 50.0;
outside_length_b_mm = 50.0;
width_mm = 30.0;
bend_angle_deg = 90.0;
k_factor = 0.45;

outer_radius_mm = bend_radius_mm + thickness_mm;
setback_mm = outer_radius_mm * tan(bend_angle_deg / 2.0);
bend_allowance_mm = (bend_angle_deg * PI / 180.0) * (bend_radius_mm + k_factor * thickness_mm);
flat_length_mm = outside_length_a_mm + outside_length_b_mm - 2.0 * setback_mm + bend_allowance_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {\"thickness_mm\": ",
    thickness_mm,
    ", \"bend_radius_mm\": ",
    bend_radius_mm,
    ", \"flat_length_mm\": ",
    flat_length_mm,
    "}"
));

function arc_points(cx, cy, r, a0, a1, n = 24) =
    [for (i = [0 : n]) let(a = a0 + (a1 - a0) * i / n) [cx + r * cos(a), cy + r * sin(a)]];

module l_bracket() {
    polygon(points = concat(
        [[outer_radius_mm, 0]],
        [[outside_length_a_mm, 0]],
        [[outside_length_a_mm, thickness_mm]],
        [[outer_radius_mm, thickness_mm]],
        arc_points(outer_radius_mm, outer_radius_mm, bend_radius_mm, -90, -180, 24),
        [[thickness_mm, outside_length_b_mm]],
        [[0, outside_length_b_mm]],
        [[0, outer_radius_mm]],
        arc_points(outer_radius_mm, outer_radius_mm, outer_radius_mm, 180, 270, 24)
    ));
}

linear_extrude(height = width_mm, center = false, convexity = 10)
    l_bracket();