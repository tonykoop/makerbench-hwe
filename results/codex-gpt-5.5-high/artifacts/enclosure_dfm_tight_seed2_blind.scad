$fn = 80;

// Units: mm
// Two separate assembled-position solids: base and lid.
// Internal free cavity: 42 x 42 x 21 mm.
// Nominal wall: 2.5 mm. Minimum designed web: 1.6 mm.
// M3 lid clearance holes and base heat-set insert bores share the same XY axes.

inner_x = 42;
inner_y = 42;
cavity_z = 21;

wall = 2.5;
floor_t = 2.5;
lid_t = 3.2;
assembly_gap = 0.25;

outer_x = inner_x + 2 * wall;
outer_y = inner_y + 2 * wall;
base_z = floor_t + cavity_z;

screw_clear_d = 3.4;
insert_bore_d = 4.6;
insert_bore_depth = 6.2;

boss_od = 8.0;
boss_wall_min = (boss_od - insert_bore_d) / 2;

corner_post_r = boss_od / 2;
screw_margin = 6.5;
screw_x = outer_x / 2 - screw_margin;
screw_y = outer_y / 2 - screw_margin;
screw_pts = [
    [-screw_x, -screw_y],
    [ screw_x, -screw_y],
    [ screw_x,  screw_y],
    [-screw_x,  screw_y]
];

lid_z0 = base_z + assembly_gap;

module rounded_box(size, r) {
    hull() {
        for (x = [-size[0]/2 + r, size[0]/2 - r])
        for (y = [-size[1]/2 + r, size[1]/2 - r])
            translate([x, y, 0])
                cylinder(h = size[2], r = r);
    }
}

module screw_axis_holes(d, h, z0 = -0.1) {
    for (p = screw_pts)
        translate([p[0], p[1], z0])
            cylinder(h = h, d = d);
}

module base() {
    color("lightgray")
    difference() {
        union() {
            difference() {
                rounded_box([outer_x, outer_y, base_z], 4.0);

                translate([0, 0, floor_t])
                    rounded_box([inner_x, inner_y, cavity_z + 0.2], 2.0);

                for (x = [-1, 1])
                    translate([x * outer_x * 0.18, -outer_y / 2 - 0.1, floor_t + 4])
                        cube([8, wall + 0.3, base_z - floor_t - 8], center = false);

                for (x = [-1, 1])
                    translate([x * outer_x * 0.18 - 8, outer_y / 2 - wall - 0.2, floor_t + 4])
                        cube([8, wall + 0.3, base_z - floor_t - 8], center = false);

                for (y = [-1, 1])
                    translate([-outer_x / 2 - 0.1, y * outer_y * 0.18, floor_t + 4])
                        cube([wall + 0.3, 8, base_z - floor_t - 8], center = false);

                for (y = [-1, 1])
                    translate([outer_x / 2 - wall - 0.2, y * outer_y * 0.18 - 8, floor_t + 4])
                        cube([wall + 0.3, 8, base_z - floor_t - 8], center = false);
            }

            for (p = screw_pts)
                translate([p[0], p[1], floor_t])
                    cylinder(h = cavity_z, d = boss_od);
        }

        for (p = screw_pts)
            translate([p[0], p[1], base_z - insert_bore_depth + 0.05])
                cylinder(h = insert_bore_depth + 0.2, d = insert_bore_d);

        for (p = screw_pts)
            translate([p[0], p[1], floor_t - 0.1])
                cylinder(h = cavity_z - insert_bore_depth - 0.4, d = 2.8);

        translate([0, 0, -0.1])
            rounded_box([inner_x - 5, inner_y - 5, floor_t + 0.2], 1.5);
    }
}

module lid() {
    color("silver")
    translate([0, 0, lid_z0])
    difference() {
        union() {
            rounded_box([outer_x, outer_y, lid_t], 4.0);

            translate([0, 0, -1.45])
                difference() {
                    rounded_box([inner_x - 0.6, inner_y - 0.6, 1.5], 1.6);
                    translate([0, 0, -0.1])
                        rounded_box([inner_x - 4.0, inner_y - 4.0, 1.7], 1.0);
                }
        }

        screw_axis_holes(screw_clear_d, lid_t + 0.4, -0.2);

        translate([0, 0, -0.2])
            rounded_box([inner_x - 8, inner_y - 8, lid_t + 0.4], 1.0);

        for (x = [-1, 1])
        for (y = [-1, 1])
            translate([x * 8, y * 8, -0.2])
                cylinder(h = lid_t + 0.4, d = 8);
    }
}

base();
lid();