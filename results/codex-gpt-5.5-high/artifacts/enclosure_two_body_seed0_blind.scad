$fn = 48;

wall = 2.5;
clearance = 0.30;

cavity_x = 70;
cavity_y = 70;
cavity_z = 20;

base_inner_x = cavity_x + 2;
base_inner_y = cavity_y + 2;
base_inner_h = cavity_z + 2;

base_outer_x = base_inner_x + 2 * wall;
base_outer_y = base_inner_y + 2 * wall;
base_h = wall + base_inner_h;

lid_plate_h = wall;
lid_skirt_h = 8;
lid_outer_x = base_outer_x;
lid_outer_y = base_outer_y;

skirt_outer_x = base_inner_x - 2 * clearance;
skirt_outer_y = base_inner_y - 2 * clearance;
skirt_inner_x = skirt_outer_x - 2 * wall;
skirt_inner_y = skirt_outer_y - 2 * wall;

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    linear_extrude(height = z)
        offset(r = r)
            square([x - 2 * r, y - 2 * r], center = true);
}

module base() {
    difference() {
        rounded_box([base_outer_x, base_outer_y, base_h], 4);
        translate([0, 0, wall])
            rounded_box([base_inner_x, base_inner_y, base_inner_h + 0.02], 2);
    }
}

module lid() {
    union() {
        translate([0, 0, base_h])
            rounded_box([lid_outer_x, lid_outer_y, lid_plate_h], 4);

        translate([0, 0, base_h - lid_skirt_h])
            difference() {
                rounded_box([skirt_outer_x, skirt_outer_y, lid_skirt_h], 2);
                translate([0, 0, -0.01])
                    rounded_box([skirt_inner_x, skirt_inner_y, lid_skirt_h + 0.02], 1);
            }
    }
}

base();
lid();