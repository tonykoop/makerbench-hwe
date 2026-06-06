$fn = 64;

// Two-part enclosure, units in mm.
// Base and lid are shown in assembled position with nominal print clearance
// between the mating skirt and the base exterior.

wall = 2.5;
floor_thickness = 2.5;
lid_top_thickness = 2.5;

inner_x = 70;
inner_y = 70;
inner_z = 20;

fit_clearance = 0.30;      // radial clearance between mating walls
vertical_clearance = 0.20; // axial clearance at the lid stop
lid_skirt_depth = 8.0;

base_outer_x = inner_x + 2 * wall;
base_outer_y = inner_y + 2 * wall;
base_outer_z = inner_z + floor_thickness;

lid_outer_x = base_outer_x + 2 * fit_clearance + 2 * wall;
lid_outer_y = base_outer_y + 2 * fit_clearance + 2 * wall;
lid_outer_z = lid_top_thickness + lid_skirt_depth;

base_stop_z = base_outer_z - lid_skirt_depth;
lid_z = base_stop_z + vertical_clearance;

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    if (r <= 0) {
        cube(size);
    } else {
        hull() {
            for (ix = [r, x - r])
                for (iy = [r, y - r])
                    translate([ix, iy, 0])
                        cylinder(h = z, r = r);
        }
    }
}

module base_part() {
    difference() {
        rounded_box([base_outer_x, base_outer_y, base_outer_z], 3);

        translate([wall, wall, floor_thickness])
            rounded_box([inner_x, inner_y, inner_z + 0.02], 1.5);
    }
}

module lid_part() {
    difference() {
        rounded_box([lid_outer_x, lid_outer_y, lid_outer_z], 3);

        translate([wall, wall, lid_top_thickness])
            rounded_box(
                [
                    lid_outer_x - 2 * wall,
                    lid_outer_y - 2 * wall,
                    lid_skirt_depth + 0.02
                ],
                1.5
            );

        translate([wall + fit_clearance, wall + fit_clearance, lid_top_thickness])
            rounded_box(
                [
                    base_outer_x,
                    base_outer_y,
                    lid_skirt_depth - vertical_clearance + 0.02
                ],
                1.5
            );
    }
}

base_part();

translate([
    -(wall + fit_clearance),
    -(wall + fit_clearance),
    lid_z
])
    lid_part();