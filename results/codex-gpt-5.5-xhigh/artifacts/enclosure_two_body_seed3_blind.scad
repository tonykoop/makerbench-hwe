$fn = 48;

wall = 3.0;
clearance = 0.35;

cavity_x = 50;
cavity_y = 50;
cavity_z = 30;

base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_outer_z = cavity_z + wall;

lid_top_thickness = wall;
lid_skirt_wall = wall;
lid_skirt_depth = 8;

lid_inner_x = base_outer_x + 2 * clearance;
lid_inner_y = base_outer_y + 2 * clearance;
lid_outer_x = lid_inner_x + 2 * lid_skirt_wall;
lid_outer_y = lid_inner_y + 2 * lid_skirt_wall;

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    hull() {
        translate([r, r, 0]) cylinder(h = z, r = r);
        translate([x - r, r, 0]) cylinder(h = z, r = r);
        translate([r, y - r, 0]) cylinder(h = z, r = r);
        translate([x - r, y - r, 0]) cylinder(h = z, r = r);
    }
}

module base() {
    difference() {
        rounded_box([base_outer_x, base_outer_y, base_outer_z], 2);
        translate([wall, wall, wall])
            cube([cavity_x, cavity_y, cavity_z + 0.2]);
    }
}

module lid() {
    translate([
        (base_outer_x - lid_outer_x) / 2,
        (base_outer_y - lid_outer_y) / 2,
        base_outer_z - lid_skirt_depth
    ])
    difference() {
        union() {
            translate([0, 0, lid_skirt_depth])
                rounded_box([lid_outer_x, lid_outer_y, lid_top_thickness], 2);
            difference() {
                rounded_box([lid_outer_x, lid_outer_y, lid_skirt_depth], 2);
                translate([lid_skirt_wall, lid_skirt_wall, -0.1])
                    cube([lid_inner_x, lid_inner_y, lid_skirt_depth + 0.2]);
            }
        }

        translate([lid_skirt_wall, lid_skirt_wall, -0.1])
            cube([lid_inner_x, lid_inner_y, lid_skirt_depth + 0.1]);
    }
}

base();
lid();