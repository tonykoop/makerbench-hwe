$fn = 72;

// Units: mm
// Internal clear cavity target: 50 x 60 x 20
// Wall thickness: 3.0 nominal, all intentionally lightened ribs >= 1.5
// M3 lid clearance: 3.4 diameter
// M3 heat-set insert bore: 4.6 diameter, 6.0 deep
// Base and lid are rendered in assembled positions with no overlapping solids.

cavity_x = 50;
cavity_y = 60;
cavity_z = 20;

wall = 3.0;
floor_t = 3.0;
lid_t = 3.0;

boss_od = 9.0;
insert_bore_d = 4.6;
insert_bore_depth = 6.0;
screw_clear_d = 3.4;
screw_head_d = 6.2;
screw_head_depth = 1.8;

outer_x = cavity_x + 2 * wall + 18;
outer_y = cavity_y + 2 * wall + 18;
base_h = floor_t + cavity_z;
lid_z0 = base_h;

corner_r = 5;
boss_offset = 7.5;

hole_xy = [
    [-(outer_x / 2 - boss_offset), -(outer_y / 2 - boss_offset)],
    [ (outer_x / 2 - boss_offset), -(outer_y / 2 - boss_offset)],
    [ (outer_x / 2 - boss_offset),  (outer_y / 2 - boss_offset)],
    [-(outer_x / 2 - boss_offset),  (outer_y / 2 - boss_offset)]
];

module rounded_box_2d(x, y, r) {
    offset(r = r)
        square([x - 2 * r, y - 2 * r], center = true);
}

module rounded_prism(x, y, z, r) {
    linear_extrude(height = z)
        rounded_box_2d(x, y, r);
}

module base_shell() {
    difference() {
        union() {
            rounded_prism(outer_x, outer_y, base_h, corner_r);

            for (p = hole_xy)
                translate([p[0], p[1], floor_t])
                    cylinder(d = boss_od, h = cavity_z);

            translate([0, -(cavity_y / 2 + wall / 2), floor_t])
                cube([cavity_x + 2 * wall, wall, cavity_z], center = false);
            translate([-(cavity_x / 2 + wall / 2), 0, floor_t])
                cube([wall, cavity_y + 2 * wall, cavity_z], center = true);
            translate([(cavity_x / 2 + wall / 2), 0, floor_t])
                cube([wall, cavity_y + 2 * wall, cavity_z], center = true);
            translate([0, (cavity_y / 2 + wall / 2), floor_t])
                cube([cavity_x + 2 * wall, wall, cavity_z], center = true);

            for (sx = [-1, 1])
                translate([sx * (cavity_x / 2 + 5.5), 0, floor_t])
                    cube([1.8, cavity_y + 8, cavity_z], center = true);

            for (sy = [-1, 1])
                translate([0, sy * (cavity_y / 2 + 5.5), floor_t])
                    cube([cavity_x + 8, 1.8, cavity_z], center = true);
        }

        translate([0, 0, floor_t])
            cube([cavity_x, cavity_y, cavity_z + 0.2], center = false);

        translate([-cavity_x / 2, -cavity_y / 2, floor_t])
            cube([cavity_x, cavity_y, cavity_z + 0.2], center = false);

        for (p = hole_xy)
            translate([p[0], p[1], base_h - insert_bore_depth + 0.01])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.2);

        for (p = hole_xy)
            translate([p[0], p[1], floor_t - 0.1])
                cylinder(d = 2.6, h = cavity_z + 0.2);
    }
}

module lid() {
    difference() {
        union() {
            translate([0, 0, lid_z0])
                rounded_prism(outer_x, outer_y, lid_t, corner_r);

            translate([0, 0, lid_z0 - 1.4])
                difference() {
                    rounded_prism(cavity_x - 0.6, cavity_y - 0.6, 1.4, 2.2);
                    translate([0, 0, -0.1])
                        rounded_prism(cavity_x - 6.0, cavity_y - 6.0, 1.7, 1.0);
                }

            for (p = hole_xy)
                translate([p[0], p[1], lid_z0])
                    cylinder(d = boss_od + 1.0, h = lid_t);
        }

        for (p = hole_xy) {
            translate([p[0], p[1], lid_z0 - 0.1])
                cylinder(d = screw_clear_d, h = lid_t + 0.2);

            translate([p[0], p[1], lid_z0 + lid_t - screw_head_depth])
                cylinder(d = screw_head_d, h = screw_head_depth + 0.2);
        }

        translate([0, 0, lid_z0 - 0.1])
            rounded_prism(cavity_x - 9, cavity_y - 9, lid_t + 0.2, 1.0);
    }
}

base_shell();
lid();