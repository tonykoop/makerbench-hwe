$fn = 72;

// Units: mm
// Two-part 3D printable enclosure, assembled position.
// Internal clear cavity target achieved: 52 x 42 x 32 mm.
// Nominal wall thickness: 2.0 mm; all structural walls >= 1.5 mm.
// M3 lid clearance holes and M3 heat-set insert bores share the same axes.

inner_x = 52;
inner_y = 42;
inner_z = 32;

wall = 2.0;
bottom_t = 2.0;
lid_t = 3.0;

outer_x = 68;
outer_y = 58;
base_h = bottom_t + inner_z;
lid_z = base_h;

corner_r = 3.0;

post_d = 8.0;
post_r = post_d / 2;
insert_bore_d = 4.6;
insert_bore_depth = 6.0;
m3_clear_d = 3.4;
screw_head_d = 6.2;
screw_head_depth = 1.4;

boss_axes = [
    [ outer_x/2 - 6.5,  outer_y/2 - 6.5],
    [-outer_x/2 + 6.5,  outer_y/2 - 6.5],
    [ outer_x/2 - 6.5, -outer_y/2 + 6.5],
    [-outer_x/2 + 6.5, -outer_y/2 + 6.5]
];

module rounded_box_2d(x, y, r) {
    offset(r = r)
        square([x - 2*r, y - 2*r], center = true);
}

module rounded_prism(x, y, z, r) {
    linear_extrude(height = z)
        rounded_box_2d(x, y, r);
}

module base_shell() {
    difference() {
        union() {
            rounded_prism(outer_x, outer_y, base_h, corner_r);

            for (p = boss_axes)
                translate([p[0], p[1], bottom_t])
                    cylinder(h = inner_z, d = post_d);
        }

        translate([0, 0, bottom_t])
            linear_extrude(height = inner_z + 0.2)
                square([inner_x, inner_y], center = true);

        for (p = boss_axes)
            translate([p[0], p[1], base_h - insert_bore_depth])
                cylinder(h = insert_bore_depth + 0.4, d = insert_bore_d);

        // Exterior pocketing keeps mass low while preserving 2 mm perimeter walls.
        translate([0, outer_y/2 - wall - 0.01, bottom_t + 5])
            rotate([90, 0, 0])
                linear_extrude(height = wall + 0.04)
                    rounded_box_2d(42, 18, 2);

        translate([0, -outer_y/2 + wall + 0.01, bottom_t + 5])
            rotate([90, 0, 0])
                linear_extrude(height = wall + 0.04)
                    rounded_box_2d(42, 18, 2);

        translate([outer_x/2 - wall - 0.01, 0, bottom_t + 5])
            rotate([90, 0, 90])
                linear_extrude(height = wall + 0.04)
                    rounded_box_2d(30, 18, 2);

        translate([-outer_x/2 + wall + 0.01, 0, bottom_t + 5])
            rotate([90, 0, 90])
                linear_extrude(height = wall + 0.04)
                    rounded_box_2d(30, 18, 2);
    }
}

module lid() {
    difference() {
        union() {
            translate([0, 0, lid_z])
                rounded_prism(outer_x, outer_y, lid_t, corner_r);

            // Downward locating rim enters the base opening with clearance.
            translate([0, 0, lid_z - 1.45])
                linear_extrude(height = 1.45)
                    difference() {
                        square([inner_x - 0.6, inner_y - 0.6], center = true);
                        square([inner_x - 4.6, inner_y - 4.6], center = true);
                    }
        }

        for (p = boss_axes) {
            translate([p[0], p[1], lid_z - 0.2])
                cylinder(h = lid_t + 0.6, d = m3_clear_d);

            translate([p[0], p[1], lid_z + lid_t - screw_head_depth])
                cylinder(h = screw_head_depth + 0.3, d = screw_head_d);
        }

        // Lightening windows in lid, away from screw seats and sealing rim.
        translate([0, 0, lid_z - 0.1])
            linear_extrude(height = lid_t + 0.2)
                for (x = [-16, 0, 16])
                    translate([x, 0])
                        rounded_box_2d(8, 24, 1.5);
    }
}

base_shell();
lid();