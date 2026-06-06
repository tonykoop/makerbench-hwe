$fn = 64;

// Units: mm
wall = 2.5;
min_wall = 1.5;

cavity_x = 40;
cavity_y = 40;
cavity_z = 22.5;

outer_x = 62;
outer_y = 62;
base_z = wall + cavity_z;      // 25.0
lid_z = 3.2;
assembly_gap = 0.20;

screw_axis_x = 24;
screw_axis_y = 24;

m3_clearance_d = 3.4;
insert_bore_d = 4.7;
insert_bore_depth = 7.0;

boss_d = 9.0;
boss_r = boss_d / 2;

lightening_slot_w = 8;
lightening_slot_h = 11;
lightening_z0 = wall + 5.0;

module screw_axes() {
    for (x = [-screw_axis_x, screw_axis_x])
        for (y = [-screw_axis_y, screw_axis_y])
            translate([x, y, 0])
                children();
}

module base_shell() {
    difference() {
        union() {
            // Floor.
            translate([-outer_x/2, -outer_y/2, 0])
                cube([outer_x, outer_y, wall]);

            // Four side walls, leaving a true 40 x 40 mm internal cavity.
            translate([-outer_x/2, -outer_y/2, wall])
                cube([(outer_x - cavity_x)/2, outer_y, cavity_z]);
            translate([cavity_x/2, -outer_y/2, wall])
                cube([(outer_x - cavity_x)/2, outer_y, cavity_z]);
            translate([-cavity_x/2, -outer_y/2, wall])
                cube([cavity_x, (outer_y - cavity_y)/2, cavity_z]);
            translate([-cavity_x/2, cavity_y/2, wall])
                cube([cavity_x, (outer_y - cavity_y)/2, cavity_z]);

            // Insert bosses live outside the 40 x 40 cavity envelope.
            screw_axes()
                cylinder(d = boss_d, h = base_z);
        }

        // Heat-set insert bores, aligned to lid clearance holes.
        screw_axes()
            translate([0, 0, base_z - insert_bore_depth + 0.01])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.02);

        // Side lightening windows. Kept above floor and below rim.
        for (y = [-outer_y/2 - 0.01, outer_y/2 - wall + 0.01])
            translate([-lightening_slot_w/2, y, lightening_z0])
                cube([lightening_slot_w, wall + 0.02, lightening_slot_h]);

        for (x = [-outer_x/2 - 0.01, outer_x/2 - wall + 0.01])
            translate([x, -lightening_slot_w/2, lightening_z0])
                cube([wall + 0.02, lightening_slot_w, lightening_slot_h]);

        // Small bottom weight reliefs, outside the usable central cavity floor.
        for (x = [-14, 14])
            translate([x - 4, -3, -0.01])
                cube([8, 6, wall + 0.02]);
        for (y = [-14, 14])
            translate([-3, y - 4, -0.01])
                cube([6, 8, wall + 0.02]);
    }
}

module lid() {
    translate([0, 0, base_z + assembly_gap])
        difference() {
            union() {
                translate([-outer_x/2, -outer_y/2, 0])
                    cube([outer_x, outer_y, lid_z]);

                // Thin locating lip sits just inside the base opening, with clearance.
                translate([-18.9, -18.9, -1.4])
                    difference() {
                        cube([37.8, 37.8, 1.4]);
                        translate([1.5, 1.5, -0.01])
                            cube([34.8, 34.8, 1.42]);
                    }
            }

            // M3 clearance holes through lid, coaxial with base insert bores.
            screw_axes()
                translate([0, 0, -1.6])
                    cylinder(d = m3_clearance_d, h = lid_z + 3.2);

            // Top-side screw head reliefs.
            screw_axes()
                translate([0, 0, lid_z - 1.05])
                    cylinder(d = 6.2, h = 1.20);
        }
}

base_shell();
lid();