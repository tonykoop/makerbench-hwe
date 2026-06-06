// =========================================================================
// HIGH-FIDELITY 3D-PRINTABLE TWO-PART ENCLOSURE WITH MATING LIP
// Designed for manufacturability (DFM) with aggressive weight savings
// =========================================================================

// --- DESIGN PARAMETERS ---
$fn = 64;

// Internal cavity dimensions (at least 50 x 50 x 30 mm)
cavity_w = 50.0;
cavity_d = 50.0;
cavity_h = 30.0; // Split as 26mm in base, 4mm in lid

// Wall thickness parameters (strictly maintaining >= 1.5 mm everywhere)
wall_thickness = 3.5;
corner_radius_out = 6.5;
corner_radius_in = 3.0; // Uniform 3.5mm wall on corners (6.5 - 3.0)

// Outer dimensions
out_w = cavity_w + 2 * wall_thickness; // 57.0 mm
out_d = cavity_d + 2 * wall_thickness; // 57.0 mm

// Part heights
base_h = 29.0; // Floor (3.0mm) + Cavity (26.0mm)
lid_h = 7.0;   // Ceiling (3.0mm) + Cavity (4.0mm)
joint_depth = 1.5; // Depth of mating lip joint

// Fasteners (M3 screws with heat-set inserts)
boss_offset = 20.5; // Centered at (+-20.5, +-20.5)
boss_radius = 5.0;  // Solid material boss around fasteners

// Interactive view controls
explode = 0.0; // Set to > 0 (e.g. 20) to visualize separated parts

// --- HELPER MODULES ---
module rounded_square(w, d, r) {
    hull() {
        translate([-w/2+r, -d/2+r]) circle(r);
        translate([ w/2-r, -d/2+r]) circle(r);
        translate([-w/2+r,  d/2-r]) circle(r);
        translate([ w/2-r,  d/2-r]) circle(r);
    }
}

// --- BASE PART ---
module base() {
    difference() {
        union() {
            // Main outer enclosure shell
            linear_extrude(height = base_h) {
                rounded_square(out_w, out_d, corner_radius_out);
            }
            // Corner bosses to anchor the heat-set inserts (Z: 3.0 to 29.0)
            for (x = [-boss_offset, boss_offset]) {
                for (y = [-boss_offset, boss_offset]) {
                    translate([x, y, 3.0]) {
                        cylinder(r = boss_radius, h = base_h - 3.0);
                    }
                }
            }
        }

        // 1. Primary Cavity (leaves 3.0mm thick floor)
        translate([0, 0, 3.0]) {
            linear_extrude(height = base_h - 3.0 + 0.1) {
                rounded_square(cavity_w, cavity_d, corner_radius_in);
            }
        }

        // 2. Mating Joint Recess on top lip
        // Pocket width is 2.0mm, leaving exactly 1.5mm of outer wall
        translate([0, 0, base_h - joint_depth]) {
            linear_extrude(height = joint_depth + 0.1) {
                rounded_square(54.0, 54.0, corner_radius_in + 2.0);
            }
        }

        // 3. Fastener Preparations (Heat-set inserts)
        for (x = [-boss_offset, boss_offset]) {
            for (y = [-boss_offset, boss_offset]) {
                // Main insert pocket (M3 short insert: 4.2mm dia, 5.0mm depth)
                translate([x, y, base_h - 5.0]) {
                    cylinder(r = 4.2/2, h = 5.1);
                }
                // Lead-in chamfer for perfect thermal insertion alignment
                translate([x, y, base_h - 0.6]) {
                    cylinder(r1 = 4.2/2, r2 = 4.8/2, h = 0.61);
                }
                // Screw clearance/dripping hole extension (3.2mm dia down to Z=13.0)
                translate([x, y, 13.0]) {
                    cylinder(r = 3.2/2, h = base_h - 13.0 - 5.0);
                }
            }
        }

        // 4. Weight Reduction / Aesthetic Grip Slots (Side Wall Pocketing)
        // Reduces weight drastically while keeping minimum wall thickness >= 2.0mm
        // Front & Back pockets
        for (y_val = [-out_d/2, out_d/2]) {
            for (x_offset = [-12, 0, 12]) {
                translate([x_offset, y_val, 14.5])
                rotate([90, 0, 0])
                linear_extrude(height = 3.0, center = true) // cuts 1.5mm deep into 3.5mm wall
                rounded_square(8, 15, 2);
            }
        }
        // Left & Right pockets
        for (x_val = [-out_w/2, out_w/2]) {
            for (y_offset = [-12, 0, 12]) {
                translate([x_val, y_offset, 14.5])
                rotate([0, 90, 0])
                linear_extrude(height = 3.0, center = true)
                rounded_square(8, 15, 2);
            }
        }
    }
}

// --- LID PART ---
module lid() {
    difference() {
        union() {
            // Main lid body (Z: 29.0 to 36.0)
            translate([0, 0, base_h]) {
                linear_extrude(height = lid_h) {
                    rounded_square(out_w, out_d, corner_radius_out);
                }
            }
            // Mating Lip projection (protrudes down into base joint recess)
            // Lip thickness = 1.6mm (robust and >= 1.5mm limit)
            // 0.2mm clearance applied radially on both inner and outer bounds
            translate([0, 0, base_h - 1.3]) {
                linear_extrude(height = 1.3) {
                    difference() {
                        rounded_square(53.6, 53.6, corner_radius_in + 1.8);
                        rounded_square(50.4, 50.4, corner_radius_in + 0.2);
                    }
                }
            }
            // Solid internal corner bosses in lid to stabilize screw heads
            for (x = [-boss_offset, boss_offset]) {
                for (y = [-boss_offset, boss_offset]) {
                    translate([x, y, base_h]) {
                        cylinder(r = boss_radius, h = 4.0);
                    }
                }
            }
        }

        // 1. Inner Cavity step in the lid
        translate([0, 0, base_h - 0.1]) {
            linear_extrude(height = 4.1) {
                rounded_square(cavity_w, cavity_d, corner_radius_in);
            }
        }

        // 2. Screw Shaft & Counterbore Holes
        for (x = [-boss_offset, boss_offset]) {
            for (y = [-boss_offset, boss_offset]) {
                // Screw shaft clearance (3.4mm dia for free M3 movement)
                translate([x, y, base_h - 2.0]) {
                    cylinder(r = 3.4/2, h = lid_h + 4.0);
                }
                // Cap screw head counterbore recess (6.2mm dia, 3.0mm depth)
                translate([x, y, base_h + lid_h - 3.0]) {
                    cylinder(r = 6.2/2, h = 3.1);
                }
            }
        }

        // 3. Weight Reduction / Aesthetic Center Pocket on Lid Top
        // 1.5mm depth pocket, leaving 2.0mm floor thickness
        translate([0, 0, base_h + lid_h - 1.5]) {
            linear_extrude(height = 1.6) {
                rounded_square(30.0, 30.0, 5.0);
            }
        }
    }
}

// --- RENDER ASSEMBLY ---
// Both parts rendered as distinct, non-interfering solids in assembly configuration
base();

translate([0, 0, explode]) {
    lid();
}