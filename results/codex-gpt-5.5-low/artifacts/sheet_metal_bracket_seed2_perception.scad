// Units: mm
thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90;
horizontal_outside_length_mm = 40.0;
vertical_outside_length_mm = 30.0;
bracket_width_mm = 30.0;

bend_allowance_mm = (bend_angle_deg * PI / 180) * (bend_radius_mm + k_factor * thickness_mm);
outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2);
bend_deduction_mm = 2 * outside_setback_mm - bend_allowance_mm;
flat_length_mm = horizontal_outside_length_mm + vertical_outside_length_mm - bend_deduction_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {\"thickness_mm\": ",
    thickness_mm,
    ", \"bend_radius_mm\": ",
    bend_radius_mm,
    ", \"flat_length_mm\": ",
    flat_length_mm,
    "}"
));

$fn = 96;

module bend_sector_2d(r_inner, t, steps = 48) {
    r_outer = r_inner + t;

    inner_pts = [
        for (i = [0:steps])
            [r_inner * cos(i * 90 / steps), r_inner * sin(i * 90 / steps)]
    ];

    outer_pts = [
        for (i = [steps:-1:0])
            [r_outer * cos(i * 90 / steps), r_outer * sin(i * 90 / steps)]
    ];

    polygon(points = concat(inner_pts, outer_pts));
}

module l_bracket_2d() {
    union() {
        translate([bend_radius_mm, 0])
            square([
                horizontal_outside_length_mm - bend_radius_mm,
                thickness_mm
            ], center = false);

        translate([0, bend_radius_mm])
            square([
                thickness_mm,
                vertical_outside_length_mm - bend_radius_mm
            ], center = false);

        bend_sector_2d(bend_radius_mm, thickness_mm);
    }
}

linear_extrude(height = bracket_width_mm, convexity = 10)
    l_bracket_2d();