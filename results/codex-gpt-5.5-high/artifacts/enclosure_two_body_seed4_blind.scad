// 3D-printable two-part enclosure, units: mm
// Internal cavity: 52 x 62 x 22 mm
// Wall thickness: 3.0 mm
// Nominal mating clearance: 0.4 mm

$fn = 48;

wall = 3.0;
clearance = 0.4;

cavity_x = 52;
cavity_y = 62;
cavity_z = 22;

floor_thickness = wall;
base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_height = floor_thickness + cavity_z;

lid_top_thickness = wall;
lid_skirt_thickness = wall;
lid_skirt_depth = 8;

lid_inner_x = base_outer_x + 2 * clearance;
lid_inner_y = base_outer_y + 2 * clearance;
lid_outer_x = lid_inner_x + 2 * lid_skirt_thickness;
lid_outer_y = lid_inner_y + 2 * lid_skirt_thickness;
lid_height = lid_top_thickness + lid_skirt_depth;

module base() {
    difference() {
        cube([base_outer_x, base_outer_y, base_height], center = false);

        translate([wall, wall, floor_thickness])
            cube([cavity_x, cavity_y, cavity_z + 0.1], center = false);
    }
}

module lid() {
    translate([
        -(lid_outer_x - base_outer_x) / 2,
        -(lid_outer_y - base_outer_y) / 2,
        base_height - lid_skirt_depth
    ])
    difference() {
        cube([lid_outer_x, lid_outer_y, lid_height], center = false);

        translate([lid_skirt_thickness, lid_skirt_thickness, -0.1])
            cube([lid_inner_x, lid_inner_y, lid_skirt_depth + 0.1], center = false);
    }
}

base();
lid();