// Units: mm
$fn = 96;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90;

outside_flange_a_mm = 50.0;
outside_flange_b_mm = 50.0;
width_mm = 30.0;

outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2);
straight_a_mm = outside_flange_a_mm - outside_setback_mm;
straight_b_mm = outside_flange_b_mm - outside_setback_mm;
bend_allowance_mm = (PI / 180) * bend_angle_deg * (bend_radius_mm + k_factor * thickness_mm);
developed_flat_length_mm = straight_a_mm + straight_b_mm + bend_allowance_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm, ",",
    "\"bend_radius_mm\":", bend_radius_mm, ",",
    "\"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

module annular_quarter(r_inner, r_outer, a0, a1) {
    steps = 96;
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

module bracket_cross_section() {
    union() {
        translate([bend_radius_mm + thickness_mm, 0])
            square([straight_a_mm, thickness_mm], center = false);

        translate([0, bend_radius_mm + thickness_mm])
            square([thickness_mm, straight_b_mm], center = false);

        translate([bend_radius_mm + thickness_mm, bend_radius_mm + thickness_mm])
            annular_quarter(
                bend_radius_mm,
                bend_radius_mm + thickness_mm,
                180,
                270
            );
    }
}

linear_extrude(height = width_mm, center = false, convexity = 10)
    bracket_cross_section();