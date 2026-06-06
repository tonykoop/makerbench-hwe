// Units: mm
$fn = 96;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90;
width_mm = 30.0;

outside_flange_A_mm = 70.0;
outside_flange_B_mm = 40.0;

bend_angle_rad = bend_angle_deg * PI / 180;
neutral_radius_mm = bend_radius_mm + k_factor * thickness_mm;
bend_allowance_mm = bend_angle_rad * neutral_radius_mm;
outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2);
bend_deduction_mm = 2 * outside_setback_mm - bend_allowance_mm;
developed_flat_length_mm = outside_flange_A_mm + outside_flange_B_mm - bend_deduction_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm, ",",
    "\"bend_radius_mm\":", bend_radius_mm, ",",
    "\"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

leg_A_tangent_mm = outside_flange_A_mm - outside_setback_mm;
leg_B_tangent_mm = outside_flange_B_mm - outside_setback_mm;
outer_radius_mm = bend_radius_mm + thickness_mm;

module sheet_l_bracket_profile() {
    polygon(points=[
        [-leg_A_tangent_mm, outer_radius_mm],
        [0, outer_radius_mm],
        [for (a = [90:-2:0]) [outer_radius_mm * cos(a), outer_radius_mm * sin(a)]],
        [outer_radius_mm, -leg_B_tangent_mm],
        [bend_radius_mm, -leg_B_tangent_mm],
        [for (a = [0:2:90]) [bend_radius_mm * cos(a), bend_radius_mm * sin(a)]],
        [-leg_A_tangent_mm, bend_radius_mm]
    ]);
}

linear_extrude(height = width_mm, center = true)
    sheet_l_bracket_profile();