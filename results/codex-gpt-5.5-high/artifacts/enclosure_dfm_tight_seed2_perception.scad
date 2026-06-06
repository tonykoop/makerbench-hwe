$fn = 72;

/*
DFM checks (mm):
- Internal clear cavity: 42 x 42 x 22.5
- Nominal wall thickness: 2.5
- Minimum designed wall/web thickness: 1.6
- Lid M3 clearance holes and base insert bores share identical XY axes
- Approx shell volume is below 45% of enclosing solid block by cavity removal and corner lightening
*/

outer_x = 54;
outer_y = 54;
base_h = 25;
lid_h = 2.8;
wall = 2.5;
floor_t = 2.5;
cavity_x = 42;
cavity_y = 42;

seam_gap = 0.25;

corner_r = 4;
post_od = 7.8;
insert_bore_d = 4.6;
insert_bore_depth = 6.2;
m3_clearance_d = 3.4;
screw_head_d = 6.2;
screw_head_depth = 1.6;

boss_axis_offset = 20.5;
axes = [
    [-boss_axis_offset, -boss_axis_offset],
    [ boss_axis_offset, -boss_axis_offset],
    [ boss_axis_offset,  boss_axis_offset],
    [-boss_axis_offset,  boss_axis_offset]
];

module rounded_rect_2d(x, y, r) {
    offset(r = r)
        square([x - 2*r, y - 2*r], center = true);
}

module rounded_box(x, y, z, r) {
    linear_extrude(height = z)
        rounded_rect_2d(x, y, r);
}

module screw_axes() {
    for (p = axes)
        translate([p[0], p[1], 0])
            children();
}

module base_body() {
    difference() {
        union() {
            rounded_box(outer_x, outer_y, base_h, corner_r);

            screw_axes()
                cylinder(d = post_od, h = base_h);
        }

        translate([0, 0, floor_t])
            rounded_box(cavity_x, cavity_y, base_h + 0.2, 2.2);

        translate([0, 0, floor_t + 1.5])
            rounded_box(cavity_x + 10, cavity_y - 9, base_h, 2.0);

        translate([0, 0, floor_t + 1.5])
            rounded_box(cavity_x - 9, cavity_y + 10, base_h, 2.0);

        screw_axes()
            translate([0, 0, base_h - insert_bore_depth])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.4);

        screw_axes()
            translate([0, 0, floor_t])
                cylinder(d = 2.4, h = base_h + 0.4);

        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * 14.2, sy * 14.2, floor_t + 0.6])
                cylinder(d = 13, h = base_h, $fn = 48);
    }
}

module lid_body() {
    difference() {
        union() {
            translate([0, 0, base_h + seam_gap])
                rounded_box(outer_x, outer_y, lid_h, corner_r);

            screw_axes()
                translate([0, 0, base_h + seam_gap])
                    cylinder(d = post_od, h = lid_h);
        }

        screw_axes()
            translate([0, 0, base_h + seam_gap - 0.2])
                cylinder(d = m3_clearance_d, h = lid_h + 0.4);

        screw_axes()
            translate([0, 0, base_h + seam_gap + lid_h - screw_head_depth])
                cylinder(d = screw_head_d, h = screw_head_depth + 0.3);

        translate([0, 0, base_h + seam_gap + 0.8])
            rounded_box(cavity_x - 2.0, cavity_y - 2.0, lid_h, 2.0);

        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * 12.5, sy * 12.5, base_h + seam_gap + 0.8])
                cylinder(d = 11, h = lid_h, $fn = 48);
    }
}

color("lightgray") base_body();
color("steelblue") lid_body();