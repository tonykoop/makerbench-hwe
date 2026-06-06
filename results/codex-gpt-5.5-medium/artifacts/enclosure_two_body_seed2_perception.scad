// Units: mm
$fn = 48;

wall = 2.5;
clearance = 0.30;

cavity_x = 42;
cavity_y = 42;
cavity_z = 28;

skirt_depth = 6;
lid_top_thickness = wall;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;
base_h = cavity_z + wall;

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
        rounded_box([outer_x, outer_y, base_h], 1.2);

        translate([wall, wall, wall])
            cube([cavity_x, cavity_y, cavity_z + 0.2]);

        translate([wall + 1.0, wall + 1.0, wall])
            cube([cavity_x - 2.0, cavity_y - 2.0, cavity_z + 1.0]);
    }
}

module lid() {
    union() {
        translate([0, 0, base_h])
            rounded_box([outer_x, outer_y, lid_top_thickness], 1.2);

        translate([
            wall + clearance,
            wall + clearance,
            base_h - skirt_depth
        ])
            difference() {
                cube([
                    cavity_x - 2 * clearance,
                    cavity_y - 2 * clearance,
                    skirt_depth
                ]);

                translate([wall, wall, -0.1])
                    cube([
                        cavity_x - 2 * clearance - 2 * wall,
                        cavity_y - 2 * clearance - 2 * wall,
                        skirt_depth + 0.2
                    ]);
            }
    }
}

color("lightgray") base();
color("steelblue") lid();