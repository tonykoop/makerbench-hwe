$fn = 192;

thickness_mm = 2.0;
width_mm = 30.0;
flange_a_out_mm = 70.0;
flange_b_out_mm = 40.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90.0;

bend_angle_rad = bend_angle_deg * PI / 180.0;
neutral_radius_mm = bend_radius_mm + k_factor * thickness_mm;
outer_to_neutral_offset_mm = (1.0 - k_factor) * thickness_mm;
flange_a_neutral_mm = flange_a_out_mm - outer_to_neutral_offset_mm;
flange_b_neutral_mm = flange_b_out_mm - outer_to_neutral_offset_mm;
bend_allowance_mm = bend_angle_rad * neutral_radius_mm;
developed_flat_length_mm = flange_a_neutral_mm + flange_b_neutral_mm + bend_allowance_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm,
    ", \"bend_radius_mm\":", bend_radius_mm,
    ", \"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

module bracket_profile_2d() {
    difference() {
        union() {
            square([flange_a_out_mm, thickness_mm]);
            square([thickness_mm, flange_b_out_mm]);
        }
        intersection() {
            square([bend_radius_mm, bend_radius_mm]);
            circle(r = bend_radius_mm, $fn = 192);
        }
    }
}

module bend_wall_2d() {
    intersection() {
        difference() {
            circle(r = bend_radius_mm + thickness_mm, $fn = 192);
            circle(r = bend_radius_mm, $fn = 192);
        }
        square([bend_radius_mm + thickness_mm, bend_radius_mm + thickness_mm]);
    }
}

linear_extrude(height = width_mm) {
    union() {
        bracket_profile_2d();
        bend_wall_2d();
    }
}