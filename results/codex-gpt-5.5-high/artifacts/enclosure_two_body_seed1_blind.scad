// Two-part snap-free enclosure, assembled position, units: mm.
// Internal clear volume through lid skirt: 50.2 x 40.2 x 32.0 mm.

$fn = 48;

wall = 2.0;
clearance = 0.4;

cavity_x = 54.0;
cavity_y = 44.0;
cavity_z = 32.0;

base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_outer_z = cavity_z + wall;

lid_top_thickness = 2.0;
lid_skirt_thickness = 1.5;
lid_skirt_depth = 6.0;

lid_outer_x = base_outer_x;
lid_outer_y = base_outer_y;
lid_outer_z = lid_top_thickness;

skirt_outer_x = cavity_x - 2 * clearance;
skirt_outer_y = cavity_y - 2 * clearance;
skirt_inner_x = skirt_outer_x - 2 * lid_skirt_thickness;
skirt_inner_y = skirt_outer_y - 2 * lid_skirt_thickness;

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
        rounded_box([base_outer_x, base_outer_y, base_outer_z], 3.0);

        translate([0, 0, wall])
            rounded_box([cavity_x, cavity_y, cavity_z + 0.2], 1.2);
    }
}

module lid() {
    union() {
        translate([0, 0, base_outer_z])
            rounded_box([lid_outer_x, lid_outer_y, lid_outer_z], 3.0);

        translate([0, 0, base_outer_z - lid_skirt_depth])
            difference() {
                rounded_box([skirt_outer_x, skirt_outer_y, lid_skirt_depth], 1.0);

                translate([0, 0, -0.1])
                    rounded_box([skirt_inner_x, skirt_inner_y, lid_skirt_depth + 0.2], 0.6);
            }
    }
}

base();
lid();