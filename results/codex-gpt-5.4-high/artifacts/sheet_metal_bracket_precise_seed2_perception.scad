$fa = 1;
$fs = 0.1;

flange_a_outside_mm = 40;
flange_b_outside_mm = 30;
width_mm = 30;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90;

outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2);
bend_allowance_mm = (bend_angle_deg * PI / 180) * (bend_radius_mm + k_factor * thickness_mm);
flat_length_mm = (flange_a_outside_mm - outside_setback_mm) +
                 (flange_b_outside_mm - outside_setback_mm) +
                 bend_allowance_mm;

assert(flange_a_outside_mm > outside_setback_mm, "Flange A is too short for the specified bend.");
assert(flange_b_outside_mm > outside_setback_mm, "Flange B is too short for the specified bend.");

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\": ", thickness_mm,
    ", \"bend_radius_mm\": ", bend_radius_mm,
    ", \"flat_length_mm\": ", flat_length_mm,
    "}"
));

function arc_points(cx, cy, r, a0, a1, n) =
    [for (i = [0 : n]) let(a = a0 + (a1 - a0) * i / n) [cx + r * cos(a), cy + r * sin(a)]];

module bracket_profile_2d() {
    cx = bend_radius_mm + thickness_mm;
    cy = bend_radius_mm + thickness_mm;
    ro = bend_radius_mm + thickness_mm;
    ri = bend_radius_mm;
    n_arc = 64;

    polygon(points = concat(
        [[flange_a_outside_mm, 0]],
        arc_points(cx, cy, ro, 270, 180, n_arc),
        [[0, flange_b_outside_mm],
         [thickness_mm, flange_b_outside_mm],
         [thickness_mm, bend_radius_mm + thickness_mm]],
        arc_points(cx, cy, ri, 180, 270, n_arc),
        [[flange_a_outside_mm, thickness_mm]]
    ));
}

linear_extrude(height = width_mm, center = false, convexity = 10)
    bracket_profile_2d();