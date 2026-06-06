$fn = 96;

thickness_mm = 2.0;
bend_radius_mm = 2.0;
k_factor = 0.45;
bend_angle_deg = 90;
bend_angle_rad = PI / 2;

flange_a_outside_mm = 50.0;
flange_b_outside_mm = 40.0;
width_mm = 30.0;

outside_setback_mm = (bend_radius_mm + thickness_mm) * tan(bend_angle_deg / 2);
bend_allowance_mm = bend_angle_rad * (bend_radius_mm + k_factor * thickness_mm);
developed_flat_length_mm =
    flange_a_outside_mm +
    flange_b_outside_mm -
    (2 * outside_setback_mm - bend_allowance_mm);

echo(str(
    "MAKERBENCH-SHEETMETAL: {",
    "\"thickness_mm\":", thickness_mm, ",",
    "\"bend_radius_mm\":", bend_radius_mm, ",",
    "\"developed_flat_length_mm\":", developed_flat_length_mm,
    "}"
));

module formed_l_bracket(
    a = flange_a_outside_mm,
    b = flange_b_outside_mm,
    w = width_mm,
    t = thickness_mm,
    r = bend_radius_mm
) {
    outer_r = r + t;
    steps = 96;

    inner_arc = [
        for (i = [0:steps])
            let(theta = 180 - 90 * i / steps)
            [r * cos(theta), r * sin(theta)]
    ];

    outer_arc = [
        for (i = [steps:-1:0])
            let(theta = 180 - 90 * i / steps)
            [outer_r * cos(theta), outer_r * sin(theta)]
    ];

    section = concat(
        [[-a + outer_r, outer_r], [-a + outer_r, r]],
        inner_arc,
        [[outer_r, b - outer_r], [r, b - outer_r]],
        outer_arc
    );

    rotate([90, 0, 0])
        linear_extrude(height = w, center = true, convexity = 10)
            polygon(points = section);
}

formed_l_bracket();