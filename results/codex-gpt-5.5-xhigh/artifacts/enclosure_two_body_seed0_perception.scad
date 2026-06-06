$fn = 64;

// Units: mm

clearance = 0.35;
wall = 2.5;
eps = 0.02;

cavity_x = 72;
cavity_y = 72;
cavity_z = 22;

base_corner_r = 4;
cavity_corner_r = base_corner_r - wall;

base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_height = cavity_z + wall;

lid_skirt_height = 5;
lid_top_thickness = wall;

lid_inner_x = base_outer_x + 2 * clearance;
lid_inner_y = base_outer_y + 2 * clearance;
lid_inner_r = base_corner_r + clearance;

lid_outer_x = lid_inner_x + 2 * wall;
lid_outer_y = lid_inner_y + 2 * wall;
lid_outer_r = lid_inner_r + wall;

module rounded_box(size, r) {
    hull() {
        translate([r, r, 0])
            cylinder(h = size[2], r = r);
        translate([size[0] - r, r, 0])
            cylinder(h = size[2], r = r);
        translate([r, size[1] - r, 0])
            cylinder(h = size[2], r = r);
        translate([size[0] - r, size[1] - r, 0])
            cylinder(h = size[2], r = r);
    }
}

module base() {
    difference() {
        rounded_box([base_outer_x, base_outer_y, base_height], base_corner_r);

        translate([wall, wall, wall])
            rounded_box([cavity_x, cavity_y, cavity_z + eps], cavity_corner_r);
    }
}

module lid() {
    translate([
        -(wall + clearance),
        -(wall + clearance),
        base_height + clearance - lid_skirt_height
    ])
    union() {
        difference() {
            rounded_box([lid_outer_x, lid_outer_y, lid_skirt_height], lid_outer_r);

            translate([wall, wall, -eps])
                rounded_box(
                    [lid_inner_x, lid_inner_y, lid_skirt_height + 2 * eps],
                    lid_inner_r
                );
        }

        translate([0, 0, lid_skirt_height])
            rounded_box([lid_outer_x, lid_outer_y, lid_top_thickness], lid_outer_r);
    }
}

color("lightsteelblue") base();
color("gainsboro") lid();