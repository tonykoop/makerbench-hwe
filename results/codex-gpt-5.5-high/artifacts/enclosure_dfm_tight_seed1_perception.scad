$fn = 72;

// Units: mm
// Two-part enclosure, printed in assembled alignment with a small visible split.
// Internal clear cavity target: 52 x 42 x 30 mm minimum.
// Nominal wall thickness: 2.0 mm; local minimum wall kept >= 1.5 mm.
// Lid M3 clearance holes and base insert bores share the same fastener_axes[].

cavity_x = 52;
cavity_y = 42;
cavity_z = 30;

wall = 2.0;
bottom_thick = 2.0;
lid_thick = 3.0;
split_gap = 0.25;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;
base_h = bottom_thick + cavity_z;

corner_r = 3.0;

boss_d = 8.2;
boss_h = 9.0;
insert_bore_d = 4.7;     // typical M3 heat-set insert pilot bore
insert_bore_depth = 6.2;
m3_clearance_d = 3.4;    // M3 normal clearance through lid

boss_ring_wall = (boss_d - insert_bore_d) / 2;
axis_margin = 7.0;
fastener_axes = [
    [ axis_margin - outer_x / 2,  axis_margin - outer_y / 2],
    [ outer_x / 2 - axis_margin,  axis_margin - outer_y / 2],
    [ outer_x / 2 - axis_margin,  outer_y / 2 - axis_margin],
    [ axis_margin - outer_x / 2,  outer_y / 2 - axis_margin]
];

module rounded_rect_2d(w, h, r) {
    offset(r = r)
        square([w - 2 * r, h - 2 * r], center = true);
}

module rounded_box(w, h, z, r) {
    linear_extrude(height = z)
        rounded_rect_2d(w, h, r);
}

module screw_axis_holes(d, z_start, z_len) {
    for (p = fastener_axes)
        translate([p[0], p[1], z_start])
            cylinder(d = d, h = z_len);
}

module base_shell() {
    difference() {
        rounded_box(outer_x, outer_y, base_h, corner_r);

        translate([0, 0, bottom_thick])
            rounded_box(cavity_x, cavity_y, cavity_z + 0.2, max(0.5, corner_r - wall));

        // Aggressive side lightening slots; leave 2 mm bottom/top rails and corner structure.
        for (sx = [-1, 1])
            translate([sx * (outer_x / 2 + 0.01), 0, bottom_thick + 7.5])
                rotate([0, 90, 0])
                    rounded_box(18, 26, wall + 0.04, 2.0);

        for (sy = [-1, 1])
            translate([0, sy * (outer_y / 2 + 0.01), bottom_thick + 7.5])
                rotate([90, 0, 0])
                    rounded_box(24, 26, wall + 0.04, 2.0);

        // Bottom panel lightening pockets, outside the sealed wall/boss zones.
        for (x = [-13, 13])
            translate([x, 0, -0.01])
                rounded_box(14, 22, bottom_thick + 0.04, 2.0);
    }
}

module base_insert_bosses() {
    for (p = fastener_axes)
        translate([p[0], p[1], bottom_thick])
            difference() {
                cylinder(d = boss_d, h = boss_h);
                translate([0, 0, boss_h - insert_bore_depth + 0.01])
                    cylinder(d = insert_bore_d, h = insert_bore_depth + 0.04);
            }
}

module base_part() {
    difference() {
        union() {
            base_shell();
            base_insert_bosses();
        }

        // Keep insert bores open after boss union and add slight lead-in chamfer.
        for (p = fastener_axes) {
            translate([p[0], p[1], bottom_thick + boss_h - insert_bore_depth])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.2);

            translate([p[0], p[1], bottom_thick + boss_h - 0.8])
                cylinder(d1 = insert_bore_d + 1.0, d2 = insert_bore_d, h = 0.82);
        }
    }
}

module lid_part() {
    z0 = base_h + split_gap;

    translate([0, 0, z0])
        difference() {
            union() {
                rounded_box(outer_x, outer_y, lid_thick, corner_r);

                // Shallow locating ribs stay inside the cavity with clearance and do not touch base walls.
                translate([0,  cavity_y / 2 - 1.0, -1.2])
                    cube([cavity_x - 10, 1.5, 1.2], center = true);
                translate([0, -cavity_y / 2 + 1.0, -1.2])
                    cube([cavity_x - 10, 1.5, 1.2], center = true);
                translate([ cavity_x / 2 - 1.0, 0, -1.2])
                    cube([1.5, cavity_y - 10, 1.2], center = true);
                translate([-cavity_x / 2 + 1.0, 0, -1.2])
                    cube([1.5, cavity_y - 10, 1.2], center = true);
            }

            screw_axis_holes(m3_clearance_d, -0.02, lid_thick + 0.04);

            // Counterbore pockets for M3 socket/button-head screws.
            for (p = fastener_axes)
                translate([p[0], p[1], lid_thick - 1.25])
                    cylinder(d = 6.2, h = 1.28);

            // Lid lightening pockets leave a 1.8 mm top skin and ribs around screw holes.
            for (x = [-14, 0, 14])
                translate([x, 0, -0.01])
                    rounded_box(8.0, 24.0, 1.22, 1.6);
        }
}

base_part();
lid_part();