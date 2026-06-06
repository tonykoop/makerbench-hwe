// Units: mm
$fn = 128;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90.0;

flange_a_outside_mm = 50.0;
flange_b_outside_mm = 50.0;
width_mm = 30.0;

outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2);
bend_allowance_mm = (PI / 180) * bend_angle_deg * (bend_radius_mm + k_factor * thickness_mm);
developed_flat_length_mm =
    (flange_a_outside_mm - outside_setback_mm) +
    (flange_b_outside_mm - outside_setback_mm) +
    bend_allowance_mm;

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm, ",",
    "\"bend_radius_mm\":", bend_radius_mm, ",",
    "\"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

module formed_l_bracket() {
    ri = bend_radius_mm;
    ro = bend_radius_mm + thickness_mm;
    leg_a = flange_a_outside_mm - outside_setback_mm;
    leg_b = flange_b_outside_mm - outside_setback_mm;

    linear_extrude(height = width_mm, center = true, convexity = 10)
        union() {
            polygon(points = [
                [ri, 0],
                [ri + leg_a, 0],
                [ri + leg_a, thickness_mm],
                [ri, thickness_mm]
            ]);

            polygon(points = [
                [-thickness_mm, ri],
                [0, ri],
                [0, ri + leg_b],
                [-thickness_mm, ri + leg_b]
            ]);

            difference() {
                circle(r = ro);
                circle(r = ri);
                polygon(points = [
                    [-ro - 1, -ro - 1],
                    [ ro + 1, -ro - 1],
                    [ ro + 1, 0],
                    [0, 0],
                    [0, ro + 1],
                    [-ro - 1, ro + 1]
                ]);
            }
        }
}

formed_l_bracket();