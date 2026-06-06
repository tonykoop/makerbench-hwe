// Two-part 3D-printable enclosure, units: mm

wall = 2.5;
clearance = 0.30;

cavity_x = 44;
cavity_y = 44;
cavity_z = 22;

bottom_thickness = wall;
lid_thickness = wall;
lid_plug_depth = 4;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;
base_h = bottom_thickness + cavity_z;

plug_x = cavity_x - 2 * clearance;
plug_y = cavity_y - 2 * clearance;

module base() {
    difference() {
        cube([outer_x, outer_y, base_h]);
        translate([wall, wall, bottom_thickness])
            cube([cavity_x, cavity_y, cavity_z + 0.2]);
    }
}

module lid() {
    union() {
        translate([0, 0, base_h])
            cube([outer_x, outer_y, lid_thickness]);

        translate([wall + clearance, wall + clearance, base_h - lid_plug_depth])
            cube([plug_x, plug_y, lid_plug_depth]);
    }
}

base();
lid();