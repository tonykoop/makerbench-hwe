$fn = 64;

wall = 2.5;
clearance = 0.25;

cavity_x = 42;
cavity_y = 42;
cavity_z = 22;

floor_t = wall;
lid_t = 4.0;

boss_d = 9.0;
boss_r = boss_d / 2;
corner_pad = 7.0;

outer_x = cavity_x + 2 * (wall + corner_pad);
outer_y = cavity_y + 2 * (wall + corner_pad);
base_h = floor_t + cavity_z;

m3_clearance_d = 3.4;
m3_head_clearance_d = 6.2;
m3_head_recess_depth = 2.2;

insert_bore_d = 4.6;
insert_bore_depth = 6.0;

screw_margin = wall + corner_pad / 2;
screw_x = outer_x / 2 - screw_margin;
screw_y = outer_y / 2 - screw_margin;

module screw_positions() {
    for (x = [-screw_x, screw_x])
        for (y = [-screw_y, screw_y])
            translate([x, y, 0])
                children();
}

module rounded_box(size, r) {
    hull() {
        for (x = [-size[0] / 2 + r, size[0] / 2 - r])
            for (y = [-size[1] / 2 + r, size[1] / 2 - r])
                translate([x, y, 0])
                    cylinder(h = size[2], r = r);
    }
}

module base() {
    difference() {
        union() {
            rounded_box([outer_x, outer_y, base_h], 4);

            screw_positions()
                cylinder(h = base_h, d = boss_d);
        }

        translate([0, 0, floor_t])
            cube([cavity_x, cavity_y, cavity_z + clearance], center = false);

        translate([-cavity_x / 2, -cavity_y / 2, floor_t])
            cube([cavity_x, cavity_y, cavity_z + clearance]);

        screw_positions()
            translate([0, 0, base_h - insert_bore_depth])
                cylinder(h = insert_bore_depth + clearance, d = insert_bore_d);
    }
}

module lid() {
    difference() {
        union() {
            translate([0, 0, base_h])
                rounded_box([outer_x, outer_y, lid_t], 4);

            translate([0, 0, base_h - 1.5])
                rounded_box([cavity_x - 0.6, cavity_y - 0.6, 1.5], 2);
        }

        screw_positions() {
            translate([0, 0, base_h - clearance])
                cylinder(h = lid_t + 2 * clearance, d = m3_clearance_d);

            translate([0, 0, base_h + lid_t - m3_head_recess_depth])
                cylinder(h = m3_head_recess_depth + clearance, d = m3_head_clearance_d);
        }
    }
}

base();
lid();