$fn = 64;

// Units: mm
wall = 2.5;
min_wall = 1.5;

cavity_x = 42;
cavity_y = 42;
cavity_z = 22;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;

base_floor = wall;
base_h = base_floor + cavity_z;
lid_plate = wall;
lid_h = lid_plate;

total_h = base_h + lid_h;

// M3 hardware
m3_clearance_d = 3.4;
insert_bore_d = 4.8;      // typical M3 heat-set insert pilot bore
insert_bore_depth = 5.8;
boss_od = 7.8;

// Fastener axes, common for lid holes and base insert bores
screw_margin = 7.0;
screw_axes = [
    [ screw_margin,  screw_margin],
    [ outer_x - screw_margin,  screw_margin],
    [ outer_x - screw_margin,  outer_y - screw_margin],
    [ screw_margin,  outer_y - screw_margin]
];

module rounded_box(size, r) {
    hull() {
        translate([r, r, 0]) cylinder(h = size.z, r = r);
        translate([size.x - r, r, 0]) cylinder(h = size.z, r = r);
        translate([size.x - r, size.y - r, 0]) cylinder(h = size.z, r = r);
        translate([r, size.y - r, 0]) cylinder(h = size.z, r = r);
    }
}

module screw_axis_positions(z0, h, d) {
    for (p = screw_axes)
        translate([p.x, p.y, z0])
            cylinder(h = h, d = d);
}

module base_insert_bosses() {
    for (p = screw_axes)
        translate([p.x, p.y, base_floor])
            cylinder(h = base_h - base_floor, d = boss_od);
}

module base_lightening_windows() {
    // Vertical side windows leave 2.0 mm ribs above/below and preserve 2.5 mm corner/screw zones.
    window_z = 8.0;
    window_h = 9.5;

    translate([14.0, -0.2, window_z])
        cube([19.0, wall + 0.4, window_h]);
    translate([14.0, outer_y - wall - 0.2, window_z])
        cube([19.0, wall + 0.4, window_h]);

    translate([-0.2, 14.0, window_z])
        cube([wall + 0.4, 19.0, window_h]);
    translate([outer_x - wall - 0.2, 14.0, window_z])
        cube([wall + 0.4, 19.0, window_h]);
}

module base() {
    difference() {
        union() {
            rounded_box([outer_x, outer_y, base_h], 3);
            base_insert_bosses();
        }

        translate([wall, wall, base_floor])
            rounded_box([cavity_x, cavity_y, cavity_z + 0.2], 1.2);

        screw_axis_positions(base_h - insert_bore_depth, insert_bore_depth + 0.3, insert_bore_d);

        base_lightening_windows();

        // Bottom lightening pockets, stopped short to keep a continuous 1.8 mm floor.
        translate([13.5, 8.5, -0.1])
            rounded_box([20.0, 8.0, 0.9], 1.0);
        translate([13.5, outer_y - 16.5, -0.1])
            rounded_box([20.0, 8.0, 0.9], 1.0);
        translate([8.5, 13.5, -0.1])
            rounded_box([8.0, 20.0, 0.9], 1.0);
        translate([outer_x - 16.5, 13.5, -0.1])
            rounded_box([8.0, 20.0, 0.9], 1.0);
    }
}

module lid_lightening_pockets() {
    // Blind top pockets leave 1.6 mm roof thickness and avoid screw pads.
    pocket_depth = lid_plate - 1.6;

    translate([13.0, 8.8, lid_h - pocket_depth])
        rounded_box([21.0, 8.4, pocket_depth + 0.1], 1.0);
    translate([13.0, outer_y - 17.2, lid_h - pocket_depth])
        rounded_box([21.0, 8.4, pocket_depth + 0.1], 1.0);
    translate([8.8, 13.0, lid_h - pocket_depth])
        rounded_box([8.4, 21.0, pocket_depth + 0.1], 1.0);
    translate([outer_x - 17.2, 13.0, lid_h - pocket_depth])
        rounded_box([8.4, 21.0, pocket_depth + 0.1], 1.0);
}

module lid() {
    translate([0, 0, base_h])
        difference() {
            rounded_box([outer_x, outer_y, lid_h], 3);

            screw_axis_positions(-0.1, lid_h + 0.2, m3_clearance_d);

            // Underside opening relief maintains a 2.5 mm perimeter land and 1.6 mm top skin.
            translate([wall, wall, -0.1])
                rounded_box([cavity_x, cavity_y, 0.9], 1.2);

            lid_lightening_pockets();
        }
}

base();
lid();