$fn = 48;

wall = 3.0;
clearance = 0.30;
vertical_clearance = 0.20;

cavity_x = 50;
cavity_y = 60;
cavity_z = 20;

lid_thickness = 3.0;
skirt_thickness = 2.0;
skirt_depth = 5.0;

eps = 0.02;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;
base_h = wall + cavity_z;

module base() {
    difference() {
        translate([-outer_x / 2, -outer_y / 2, 0])
            cube([outer_x, outer_y, base_h]);

        translate([-cavity_x / 2, -cavity_y / 2, wall])
            cube([cavity_x, cavity_y, cavity_z + eps]);
    }
}

module lid() {
    skirt_outer_x = cavity_x - 2 * clearance;
    skirt_outer_y = cavity_y - 2 * clearance;
    skirt_inner_x = skirt_outer_x - 2 * skirt_thickness;
    skirt_inner_y = skirt_outer_y - 2 * skirt_thickness;

    union() {
        translate([-outer_x / 2, -outer_y / 2, base_h + vertical_clearance])
            cube([outer_x, outer_y, lid_thickness]);

        difference() {
            translate([-skirt_outer_x / 2, -skirt_outer_y / 2, base_h + vertical_clearance - skirt_depth])
                cube([skirt_outer_x, skirt_outer_y, skirt_depth]);

            translate([-skirt_inner_x / 2, -skirt_inner_y / 2, base_h + vertical_clearance - skirt_depth - eps])
                cube([skirt_inner_x, skirt_inner_y, skirt_depth + 2 * eps]);
        }
    }
}

color("lightgray") base();
color("steelblue") lid();