$fn = 48;

wall = 2.5;
clearance = 0.30;

cavity_x = 42;
cavity_y = 42;
cavity_z = 22;

base_bottom = wall;
base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_height = base_bottom + cavity_z;

lid_top = wall;
lid_skirt_h = 8;
lid_inner_x = base_outer_x + 2 * clearance;
lid_inner_y = base_outer_y + 2 * clearance;
lid_outer_x = lid_inner_x + 2 * wall;
lid_outer_y = lid_inner_y + 2 * wall;
lid_bottom_z = base_height + clearance - lid_skirt_h;
lid_top_z = base_height + clearance;

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
        rounded_box([base_outer_x, base_outer_y, base_height], 3);
        translate([0, 0, base_bottom])
            rounded_box([cavity_x, cavity_y, cavity_z + 0.02], 1.2);
    }
}

module lid() {
    difference() {
        union() {
            translate([0, 0, lid_top_z])
                rounded_box([lid_outer_x, lid_outer_y, lid_top], 3.3);

            translate([0, 0, lid_bottom_z])
                rounded_box([lid_outer_x, lid_outer_y, lid_skirt_h], 3.3);
        }

        translate([0, 0, lid_bottom_z - 0.01])
            rounded_box([lid_inner_x, lid_inner_y, lid_skirt_h + 0.02], 3.0);
    }
}

color("lightsteelblue") base();
color("gainsboro") lid();