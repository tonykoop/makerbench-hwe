$fn = 64;

// Units: mm
wall = 2.5;
clearance = 0.30;

cavity_x = 70;
cavity_y = 70;
cavity_z = 20;

base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_h = cavity_z + wall;

lid_top_th = wall;
lid_skirt_th = wall;
lid_skirt_h = 8;
lid_inner_x = base_outer_x + 2 * clearance;
lid_inner_y = base_outer_y + 2 * clearance;
lid_outer_x = lid_inner_x + 2 * lid_skirt_th;
lid_outer_y = lid_inner_y + 2 * lid_skirt_th;

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    hull() {
        for (sx = [-1, 1], sy = [-1, 1]) {
            translate([
                sx * (x / 2 - r),
                sy * (y / 2 - r),
                0
            ])
                cylinder(h = z, r = r);
        }
    }
}

module base() {
    difference() {
        rounded_box([base_outer_x, base_outer_y, base_h], 3);

        translate([0, 0, wall])
            rounded_box([cavity_x, cavity_y, cavity_z + 0.1], 1.5);
    }
}

module lid() {
    union() {
        translate([0, 0, base_h + clearance])
            difference() {
                rounded_box([lid_outer_x, lid_outer_y, lid_top_th], 3.3);

                translate([0, 0, -0.05])
                    rounded_box([lid_inner_x, lid_inner_y, lid_top_th + 0.1], 1.8);
            }

        translate([0, 0, base_h + clearance])
            rounded_box([lid_outer_x, lid_outer_y, lid_top_th], 3.3);

        translate([0, 0, base_h + clearance - lid_skirt_h])
            difference() {
                rounded_box([lid_outer_x, lid_outer_y, lid_skirt_h], 3.3);

                translate([0, 0, -0.05])
                    rounded_box([lid_inner_x, lid_inner_y, lid_skirt_h + 0.1], 1.8);
            }
    }
}

base();
lid();