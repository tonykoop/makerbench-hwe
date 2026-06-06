// Units: mm

$fn = 72;

internal_x = 70;
internal_y = 70;
internal_z = 20;

wall = 2.5;
bottom = 2.5;
lid_t = 4.0;

outer_x = 90;
outer_y = 90;
base_h = bottom + internal_z;

corner_r = 3.0;

boss_d = 10.0;
boss_r = boss_d / 2;
boss_axis_offset = 8.0;

m3_clearance_d = 3.4;
m3_head_clearance_d = 6.2;
m3_head_recess_depth = 2.2;

insert_bore_d = 4.6;
insert_bore_depth = 8.0;

eps = 0.02;

module rounded_box(size, r) {
    linear_extrude(height = size[2])
        offset(r = r)
            offset(delta = -r)
                square([size[0], size[1]], center = true);
}

module screw_axes() {
    for (sx = [-1, 1], sy = [-1, 1])
        translate([
            sx * (outer_x / 2 - boss_axis_offset),
            sy * (outer_y / 2 - boss_axis_offset),
            0
        ])
            children();
}

module base() {
    difference() {
        union() {
            rounded_box([outer_x, outer_y, base_h], corner_r);

            screw_axes()
                cylinder(h = base_h, d = boss_d);
        }

        translate([0, 0, bottom])
            cube([internal_x, internal_y, internal_z + eps], center = false);

        translate([-internal_x / 2, -internal_y / 2, bottom])
            cube([internal_x, internal_y, internal_z + eps]);

        screw_axes()
            translate([0, 0, base_h - insert_bore_depth + eps])
                cylinder(h = insert_bore_depth + eps, d = insert_bore_d);
    }
}

module lid() {
    difference() {
        translate([0, 0, base_h])
            rounded_box([outer_x, outer_y, lid_t], corner_r);

        screw_axes() {
            translate([0, 0, base_h - eps])
                cylinder(h = lid_t + 2 * eps, d = m3_clearance_d);

            translate([0, 0, base_h + lid_t - m3_head_recess_depth])
                cylinder(h = m3_head_recess_depth + eps, d = m3_head_clearance_d);
        }
    }
}

base();
lid();