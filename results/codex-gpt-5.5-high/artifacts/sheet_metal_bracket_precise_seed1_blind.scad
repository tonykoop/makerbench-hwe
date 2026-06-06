// Units: mm
$fn = 96;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90.0;

outside_flange_a_mm = 50.0;
outside_flange_b_mm = 50.0;
width_mm = 30.0;

bend_allowance_mm =
    (bend_angle_deg * PI / 180.0) * (bend_radius_mm + k_factor * thickness_mm);

outside_setback_mm =
    (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2.0);

bend_deduction_mm =
    2.0 * outside_setback_mm - bend_allowance_mm;

developed_flat_length_mm =
    outside_flange_a_mm + outside_flange_b_mm - bend_deduction_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm, ",",
    "\"bend_radius_mm\":", bend_radius_mm, ",",
    "\"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

module annular_sector_2d(r_inner, r_outer, a0, a1, steps = 64) {
    outer_pts = [
        for (i = [0 : steps])
            let(a = a0 + (a1 - a0) * i / steps)
            [r_outer * cos(a), r_outer * sin(a)]
    ];

    inner_pts = [
        for (i = [steps : -1 : 0])
            let(a = a0 + (a1 - a0) * i / steps)
            [r_inner * cos(a), r_inner * sin(a)]
    ];

    polygon(concat(outer_pts, inner_pts));
}

module formed_l_bracket() {
    r_i = bend_radius_mm;
    r_o = bend_radius_mm + thickness_mm;

    straight_a_mm = outside_flange_a_mm - r_o;
    straight_b_mm = outside_flange_b_mm - r_o;

    rotate([90, 0, 0])
        linear_extrude(height = width_mm, center = true, convexity = 10)
            union() {
                translate([r_i, -r_o])
                    square([straight_a_mm, thickness_mm], center = false);

                translate([-r_o, r_i])
                    square([thickness_mm, straight_b_mm], center = false);

                annular_sector_2d(r_i, r_o, 180, 270);
            }
}

formed_l_bracket();