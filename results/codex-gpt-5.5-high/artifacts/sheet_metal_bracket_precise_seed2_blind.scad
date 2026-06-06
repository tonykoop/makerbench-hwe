// Units: mm
$fn = 96;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90;
bend_angle_rad = PI / 2;

flange_a_outside_mm = 40;
flange_b_outside_mm = 30;
width_mm = 30;

outside_radius_mm = bend_radius_mm + thickness_mm;
neutral_radius_mm = bend_radius_mm + k_factor * thickness_mm;
bend_allowance_mm = bend_angle_rad * neutral_radius_mm;
outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2);
developed_flat_length_mm =
    flange_a_outside_mm + flange_b_outside_mm
    - 2 * outside_setback_mm
    + bend_allowance_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm, ",",
    "\"bend_radius_mm\":", bend_radius_mm, ",",
    "\"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

module quarter_annular_sector(r_inner, r_outer, steps = 64) {
    polygon(points = concat(
        [for (i = [0:steps]) [
            r_outer * cos(i * 90 / steps),
            r_outer * sin(i * 90 / steps)
        ]],
        [for (i = [steps:-1:0]) [
            r_inner * cos(i * 90 / steps),
            r_inner * sin(i * 90 / steps)
        ]]
    ));
}

module formed_l_bracket() {
    linear_extrude(height = width_mm, center = true, convexity = 10)
    union() {
        translate([0, -thickness_mm])
            square([flange_a_outside_mm, thickness_mm]);

        translate([-thickness_mm, 0])
            square([thickness_mm, flange_b_outside_mm]);

        quarter_annular_sector(bend_radius_mm, outside_radius_mm, 96);
    }
}

formed_l_bracket();