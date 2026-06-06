$fn = 72;

// Units: mm
// Two printable parts shown in near-assembled position with a small Z clearance
// so the base and lid remain separate, non-interfering solids.

inner_x = 54;
inner_y = 64;
cavity_h = 22;

wall = 3.0;
bottom_t = 3.0;
base_h = bottom_t + cavity_h;

body_outer_x = inner_x + 2 * wall;
body_outer_y = inner_y + 2 * wall;

lid_outer_x = 76;
lid_outer_y = 86;
lid_t = 3.0;
assembly_gap = 0.20;
lid_z = base_h + assembly_gap;

body_corner_r = 3.0;
cavity_corner_r = 2.0;
lid_corner_r = 5.0;

screw_x = 32;
screw_y = 37;

m3_clearance_d = 3.4;
insert_bore_d = 4.7;
insert_bore_depth = 6.0;
insert_leadin_d = 5.2;
insert_leadin_depth = 0.8;

boss_d = 12.0;

module rounded_box_xy(size, r) {
    linear_extrude(height = size[2])
        offset(r = r)
            square([size[0] - 2 * r, size[1] - 2 * r], center = true);
}

module screw_positions() {
    for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * screw_x, sy * screw_y, 0])
            children();
}

module base() {
    color([0.12, 0.36, 0.78])
    difference() {
        union() {
            rounded_box_xy([body_outer_x, body_outer_y, base_h], body_corner_r);

            screw_positions()
                cylinder(d = boss_d, h = base_h);
        }

        translate([0, 0, bottom_t])
            rounded_box_xy([inner_x, inner_y, cavity_h + 0.4], cavity_corner_r);

        screw_positions()
            translate([0, 0, base_h - insert_bore_depth])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.35);

        screw_positions()
            translate([0, 0, base_h - insert_leadin_depth])
                cylinder(d = insert_leadin_d, h = insert_leadin_depth + 0.35);
    }
}

module lid() {
    color([0.88, 0.42, 0.14])
    translate([0, 0, lid_z])
    difference() {
        rounded_box_xy([lid_outer_x, lid_outer_y, lid_t], lid_corner_r);

        screw_positions()
            translate([0, 0, -0.2])
                cylinder(d = m3_clearance_d, h = lid_t + 0.4);
    }
}

base();
lid();