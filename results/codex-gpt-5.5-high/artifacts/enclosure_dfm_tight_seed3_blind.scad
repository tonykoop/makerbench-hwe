$fn = 64;

// Units: mm
// Two separate assembled-position solids: base at z=0, lid above base with 0.25 mm visual assembly gap.
// Internal clear cavity: 54 x 54 x 32 mm minimum from z=3 to z=35.
// Nominal exterior: 70 x 70 x 41.25 mm.
// Screw axes shared by lid clearance holes and base insert bores.

inner_x = 54;
inner_y = 54;
cavity_h = 32;

wall = 3.0;
floor_t = 3.0;
base_h = floor_t + cavity_h + 3.0;

outer_x = 70;
outer_y = 70;

lid_t = 3.0;
lid_gap = 0.25;
lid_z = base_h + lid_gap;

corner_r = 3.0;

screw_xy = 28;
screw_pos = [
    [-screw_xy, -screw_xy],
    [ screw_xy, -screw_xy],
    [ screw_xy,  screw_xy],
    [-screw_xy,  screw_xy]
];

m3_clear_d = 3.4;
insert_bore_d = 4.7;
insert_bore_depth = 6.2;
boss_od = 8.8;

module rounded_box_2d(x, y, r) {
    offset(r = r)
        square([x - 2*r, y - 2*r], center = true);
}

module lightening_window_2d(x, y, r) {
    offset(r = r)
        square([x - 2*r, y - 2*r], center = true);
}

module base_shell() {
    difference() {
        linear_extrude(base_h)
            rounded_box_2d(outer_x, outer_y, corner_r);

        translate([0, 0, floor_t])
            linear_extrude(base_h + 0.2)
                rounded_box_2d(inner_x, inner_y, 1.5);

        for (x = [-outer_x/2 - 0.1, outer_x/2 + 0.1]) {
            translate([x, 0, floor_t + 8])
                rotate([0, 90, 0])
                    linear_extrude(8)
                        lightening_window_2d(28, 15, 2);
            translate([x, 0, floor_t + 24])
                rotate([0, 90, 0])
                    linear_extrude(8)
                        lightening_window_2d(28, 15, 2);
        }

        for (y = [-outer_y/2 - 0.1, outer_y/2 + 0.1]) {
            translate([0, y, floor_t + 8])
                rotate([90, 0, 0])
                    linear_extrude(8)
                        lightening_window_2d(28, 15, 2);
            translate([0, y, floor_t + 24])
                rotate([90, 0, 0])
                    linear_extrude(8)
                        lightening_window_2d(28, 15, 2);
        }
    }
}

module base_bosses() {
    for (p = screw_pos) {
        translate([p[0], p[1], floor_t])
            difference() {
                cylinder(d = boss_od, h = base_h - floor_t);
                translate([0, 0, base_h - floor_t - insert_bore_depth + 0.01])
                    cylinder(d = insert_bore_d, h = insert_bore_depth + 0.2);
                translate([0, 0, -0.1])
                    cylinder(d = 2.7, h = base_h - floor_t + 0.2);
            }
    }
}

module base_part() {
    union() {
        base_shell();
        base_bosses();
    }
}

module lid_part() {
    translate([0, 0, lid_z])
        difference() {
            linear_extrude(lid_t)
                rounded_box_2d(outer_x, outer_y, corner_r);

            linear_extrude(lid_t + 0.2)
                rounded_box_2d(42, 42, 2);

            for (p = screw_pos) {
                translate([p[0], p[1], -0.1])
                    cylinder(d = m3_clear_d, h = lid_t + 0.2);
                translate([p[0], p[1], lid_t - 1.0])
                    cylinder(d1 = 6.6, d2 = 3.6, h = 1.2);
            }

            for (x = [-18, 0, 18])
                translate([x, 0, -0.1])
                    cylinder(d = 10, h = lid_t + 0.2);
            for (y = [-18, 18])
                translate([0, y, -0.1])
                    cylinder(d = 10, h = lid_t + 0.2);
        }
}

base_part();
lid_part();