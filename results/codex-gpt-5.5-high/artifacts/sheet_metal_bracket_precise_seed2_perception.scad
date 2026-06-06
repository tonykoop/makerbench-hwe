// Units: mm
$fn = 96;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;

outside_flange_a_mm = 40.0;
outside_flange_b_mm = 30.0;
width_mm = 30.0;
bend_angle_deg = 90.0;

bend_angle_rad = bend_angle_deg * PI / 180.0;
outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2.0);
bend_allowance_mm = bend_angle_rad * (bend_radius_mm + k_factor * thickness_mm);
developed_flat_length_mm =
    outside_flange_a_mm + outside_flange_b_mm
    - (2.0 * outside_setback_mm - bend_allowance_mm);

straight_a_mm = outside_flange_a_mm - outside_setback_mm;
straight_b_mm = outside_flange_b_mm - outside_setback_mm;
outer_radius_mm = bend_radius_mm + thickness_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm, ",",
    "\"bend_radius_mm\":", bend_radius_mm, ",",
    "\"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

module quarter_annulus_2d(inner_r, outer_r, steps = 96) {
    polygon(
        points = concat(
            [for (i = [0:steps])
                [outer_r * cos(i * 90 / steps), outer_r * sin(i * 90 / steps)]
            ],
            [for (i = [steps:-1:0])
                [inner_r * cos(i * 90 / steps), inner_r * sin(i * 90 / steps)]
            ]
        )
    );
}

module formed_l_bracket_2d() {
    union() {
        translate([outer_radius_mm, 0])
            square([straight_a_mm, thickness_mm], center = false);

        translate([0, outer_radius_mm])
            square([thickness_mm, straight_b_mm], center = false);

        quarter_annulus_2d(bend_radius_mm, outer_radius_mm);
    }
}

linear_extrude(height = width_mm, center = true, convexity = 10)
    formed_l_bracket_2d();