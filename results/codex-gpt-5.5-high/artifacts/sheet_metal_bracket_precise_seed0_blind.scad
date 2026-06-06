// Units: mm

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90;
bend_angle_rad = bend_angle_deg * PI / 180;

outside_flange_A_mm = 70.0;
outside_flange_B_mm = 40.0;
width_mm = 30.0;

outside_radius_mm = bend_radius_mm + thickness_mm;
outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2);
bend_allowance_mm = bend_angle_rad * (bend_radius_mm + k_factor * thickness_mm);
developed_flat_length_mm =
    outside_flange_A_mm + outside_flange_B_mm
    - (2 * outside_setback_mm - bend_allowance_mm);

$fn = 96;

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm, ",",
    "\"bend_radius_mm\":", bend_radius_mm, ",",
    "\"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

module annular_bend_segment(r_inner, r_outer, width, steps = 64) {
    points_outer = [
        for (i = [0:steps])
            let(a = -90 + i * 90 / steps)
            [r_outer * cos(a), r_outer * sin(a)]
    ];

    points_inner = [
        for (i = [steps:-1:0])
            let(a = -90 + i * 90 / steps)
            [r_inner * cos(a), r_inner * sin(a)]
    ];

    linear_extrude(height = width, center = true)
        polygon(concat(points_outer, points_inner));
}

module straight_flange_A() {
    translate([0, -outside_radius_mm, 0])
        cube([
            outside_flange_A_mm - outside_setback_mm,
            thickness_mm,
            width_mm
        ], center = false);
}

module straight_flange_B() {
    translate([-outside_radius_mm, 0, 0])
        cube([
            thickness_mm,
            outside_flange_B_mm - outside_setback_mm,
            width_mm
        ], center = false);
}

module formed_l_bracket() {
    translate([0, 0, width_mm / 2]) {
        straight_flange_A();
        straight_flange_B();
        annular_bend_segment(
            bend_radius_mm,
            outside_radius_mm,
            width_mm
        );
    }
}

formed_l_bracket();