// =========================================================================
// 3D-Printable Two-Part Enclosure with M3 Heat-Set Inserts
// Designed by Antigravity
// =========================================================================

// --- Parameters ---
$fn = 60; // Smoothness factor for cylinders and circles

// Cavity Dimensions (Internal)
cavity_w = 60.0; // Minimum internal width (x-axis)
cavity_d = 50.0; // Minimum internal depth (y-axis)
cavity_h = 35.0; // Minimum internal height (z-axis)
wall     = 3.0;  // Nominal wall thickness

// Fastener Dimensions (M3 Screws & Heat-Set Inserts)
insert_bore_dia   = 4.2; // Optimized bore diameter for M3 heat-set inserts
insert_bore_depth = 6.0; // Depth of the insert bore in the base
clearance_dia     = 3.4; // Clearance hole diameter for M3 screws in the lid
boss_rad          = 6.0; // Radius of the corner bosses to ensure adequate wall thickness

// Screw center offsets (places screws outside the cavity to preserve the 60x50 area)
screw_x = cavity_w / 2 + 5.0; // 35.0 mm
screw_y = cavity_d / 2 + 5.0; // 30.0 mm

// Outer Dimensions of the main rectangular section
outer_w = cavity_w + 2 * wall; // 66.0 mm
outer_d = cavity_d + 2 * wall; // 56.0 mm

// Visualization Settings
explode = false;      // Set to true to inspect the parts separately
explode_gap = 20.0;   // Separation distance when exploded

// --- 2D Profiles ---

// Outer profile including the corner bosses
module outer_profile() {
    union() {
        square([outer_w, outer_d], center=true);
        for (x = [-screw_x, screw_x]) {
            for (y = [-screw_y, screw_y]) {
                translate([x, y])
                    circle(r=boss_rad);
            }
        }
    }
}

// Inner cavity profile
module cavity_profile() {
    square([cavity_w, cavity_d], center=true);
}

// --- 3D Modules ---

// Enclosure Base
module base() {
    difference() {
        // Main outer extrusion
        linear_extrude(height = cavity_h + wall) {
            outer_profile();
        }
        
        // Internal cavity (leaves a floor of thickness 'wall' at the bottom)
        translate([0, 0, wall]) {
            linear_extrude(height = cavity_h + 1.0) {
                cavity_profile();
            }
        }
        
        // Heat-set insert bores at the 4 corners
        for (x = [-screw_x, screw_x]) {
            for (y = [-screw_y, screw_y]) {
                translate([x, y, cavity_h + wall - insert_bore_depth]) {
                    cylinder(r = insert_bore_dia / 2, h = insert_bore_depth + 1.0);
                }
            }
        }
    }
}

// Enclosure Lid
module lid() {
    difference() {
        // Main lid body sitting directly on top of the base
        translate([0, 0, cavity_h + wall]) {
            linear_extrude(height = wall) {
                outer_profile();
            }
        }
        
        // Clearance holes through the lid aligned with the base bores
        for (x = [-screw_x, screw_x]) {
            for (y = [-screw_y, screw_y]) {
                translate([x, y, cavity_h + wall - 1.0]) {
                    cylinder(r = clearance_dia / 2, h = wall + 2.0);
                }
            }
        }
    }
}

// --- Render Assembly ---

// Base remains at the origin
base();

// Lid is translated vertically if exploded
translate([0, 0, explode ? explode_gap : 0]) {
    lid();
}