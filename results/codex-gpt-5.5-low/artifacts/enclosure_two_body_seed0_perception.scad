$fn = 48;

wall = 2.5;
clearance = 0.30;

cavity_x = 70;
cavity_y = 70;
cavity_z = 20;

base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_outer_z = cavity_z + wall;

lid_top_t = wall;
lid_skirt_h = 8;
lid_inner_x = base_outer_x + 2 * clearance;
lid_inner_y = base_outer_y + 2 * clearance;
lid_outer_x = lid_inner_x + 2 * wall;
lid_outer_y = lid_inner_y + 2 * wall;

module rounded_box(size, r) {
    hull() {
        for (x = [r, size[0] - r])
        for (y = [r, size[1] - r])
        for (z = [r, size[2] - r])
            translate([x, y, z])
                sphere(r = r);
    }
}

module base() {
    difference() {
        rounded_box([base_outer_x, base_outer_y, base_outer_z], 2);
        translate([wall, wall, wall])
            cube([cavity_x, cavity_y, cavity_z + 1], center = false);
    }
}

module lid() {
    translate([
        -(lid_outer_x - base_outer_x) / 2,
        -(lid_outer_y - base_outer_y) / 2,
        base_outer_z + clearance
    ])
    union() {
        cube([lid_outer_x, lid_outer_y, lid_top_t], center = false);

        difference() {
            cube([lid_outer_x, lid_outer_y, lid_skirt_h], center = false);
            translate([wall, wall, -0.5])
                cube([lid_inner_x, lid_inner_y, lid_skirt_h + 1], center = false);
        }
    }
}

base();
lid();