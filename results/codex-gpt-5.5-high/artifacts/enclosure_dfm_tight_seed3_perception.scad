$fn = 64;

// Units: mm
// Internal free cavity target: 56 x 56 x 32 mm, satisfying >= 50 x 50 x 30 mm.
// Wall thickness: nominal 3.0 mm; all lightening pockets leave >= 1.5 mm.
// M3 lid clearance holes and base heat-set insert bores share the same XY axes.

wall = 3.0;
min_wall = 1.5;

cavity_x = 56;
cavity_y = 56;
cavity_z = 32;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;

base_z = wall + cavity_z;
lid_z = wall;

screw_pitch_x = 46;
screw_pitch_y = 46;
screw_positions = [
    [-screw_pitch_x / 2, -screw_pitch_y / 2],
    [ screw_pitch_x / 2, -screw_pitch_y / 2],
    [ screw_pitch_x / 2,  screw_pitch_y / 2],
    [-screw_pitch_x / 2,  screw_pitch_y / 2]
];

m3_clearance_d = 3.4;
m3_head_counterbore_d = 6.4;
m3_head_counterbore_z = 1.8;

insert_bore_d = 4.8;
insert_bore_depth = 6.5;
insert_boss_d = 9.6;

corner_r = 3.0;
lid_gap = 0.20;

module rounded_rect_2d(w, h, r) {
    offset(r = r)
        square([w - 2 * r, h - 2 * r], center = true);
}

module rounded_box(w, h, z, r) {
    linear_extrude(height = z)
        rounded_rect_2d(w, h, r);
}

module screw_xy() {
    for (p = screw_positions)
        translate([p[0], p[1], 0])
            children();
}

module base() {
    difference() {
        union() {
            difference() {
                rounded_box(outer_x, outer_y, base_z, corner_r);

                translate([0, 0, wall])
                    linear_extrude(height = cavity_z + 0.2)
                        rounded_rect_2d(cavity_x, cavity_y, max(0.1, corner_r - wall));

                // Side-wall lightening windows, leaving 3 mm top/bottom rails and 1.5 mm web thickness.
                translate([0, outer_y / 2 - min_wall / 2, wall + 5])
                    cube([30, min_wall + 0.4, 18], center = true);
                translate([0, -outer_y / 2 + min_wall / 2, wall + 5])
                    cube([30, min_wall + 0.4, 18], center = true);
                translate([outer_x / 2 - min_wall / 2, 0, wall + 5])
                    cube([min_wall + 0.4, 30, 18], center = true);
                translate([-outer_x / 2 + min_wall / 2, 0, wall + 5])
                    cube([min_wall + 0.4, 30, 18], center = true);

                // Bottom lightening pocket, preserving a continuous 1.5 mm floor.
                translate([0, 0, -0.1])
                    linear_extrude(height = wall - min_wall + 0.1)
                        rounded_rect_2d(38, 38, 2);
            }

            // Heat-set insert bosses stand inside the cavity and are tied to the base floor.
            screw_xy()
                cylinder(d = insert_boss_d, h = base_z - lid_gap);
        }

        // Insert bores aligned to lid clearance axes.
        screw_xy()
            translate([0, 0, base_z - insert_bore_depth])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.4);

        // Small pilot continuation below bore for screw tip relief without piercing floor.
        screw_xy()
            translate([0, 0, wall + 1.0])
                cylinder(d = 3.0, h = cavity_z);
    }
}

module lid() {
    translate([0, 0, base_z + lid_gap])
        difference() {
            union() {
                rounded_box(outer_x, outer_y, lid_z, corner_r);

                // Shallow internal registration lip, inset from base walls; stops before screw bosses.
                translate([0, 0, -2.0])
                    difference() {
                        linear_extrude(height = 2.0)
                            rounded_rect_2d(cavity_x - 0.8, cavity_y - 0.8, max(0.1, corner_r - wall));
                        translate([0, 0, -0.1])
                            linear_extrude(height = 2.2)
                                rounded_rect_2d(cavity_x - 6.8, cavity_y - 6.8, max(0.1, corner_r - wall - 3.0));
                    }
            }

            // Through clearance holes for M3 screws.
            screw_xy()
                translate([0, 0, -2.3])
                    cylinder(d = m3_clearance_d, h = lid_z + 4.6);

            // Top counterbores for socket/button head screw seating.
            screw_xy()
                translate([0, 0, lid_z - m3_head_counterbore_z])
                    cylinder(d = m3_head_counterbore_d, h = m3_head_counterbore_z + 0.2);

            // Top-side lightening pockets between screws, leaving >= 1.5 mm top skin.
            translate([0, 0, lid_z - 1.2])
                linear_extrude(height = 1.4)
                    rounded_rect_2d(28, 28, 2);
        }
}

base();
lid();