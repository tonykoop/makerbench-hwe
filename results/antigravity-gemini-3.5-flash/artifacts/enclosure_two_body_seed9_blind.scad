// ==========================================
// 3D-Printable Two-Part Enclosure
// ==========================================
// Cavity Dimensions: 70 x 60 x 30 mm (Min)
// Wall Thickness: 2.0 mm
// Joint Type: Rebate / Lap Joint with clearance
// Nominal Clearance: 0.15 mm
// ==========================================

// --- Design Parameters ---
cavity_x = 70.0;     // Internal length
cavity_y = 60.0;     // Internal width
cavity_z = 30.0;     // Internal height
wall = 2.0;          // Wall thickness
lip_h = 2.0;         // Vertical height of the alignment lip
clearance = 0.15;    // Print clearance between mating surfaces
split_z = 15.0;      // Z height of the horizontal split plane

// --- Helper Modules ---
// Creates a cube centered in X and Y, starting at Z = 0
module centered_cube(w, d, h) {
    translate([-w/2, -d/2, 0]) cube([w, d, h]);
}

// --- Base Part ---
module enclosure_base() {
    color("LightBlue") {
        union() {
            // Main hollow base body
            difference() {
                // Outer shell
                translate([0, 0, -wall])
                    centered_cube(cavity_x + 2*wall, cavity_y + 2*wall, split_z + wall);
                
                // Inner cavity
                translate([0, 0, 0])
                    centered_cube(cavity_x, cavity_y, split_z + 1.0);
            }
            
            // Alignment Lip (Base male joint)
            // Starts at split plane, height is reduced by clearance
            translate([0, 0, split_z])
                difference() {
                    // Outer boundary of the lip (cleared from the outer pocket step)
                    centered_cube(cavity_x + wall - 2*clearance, cavity_y + wall - 2*clearance, lip_h - clearance);
                    
                    // Inner boundary of the lip (cleared to avoid spilling into the cavity area)
                    translate([0, 0, -0.5])
                        centered_cube(cavity_x + 2*clearance, cavity_y + 2*clearance, lip_h + 1.0);
                }
        }
    }
}

// --- Lid Part ---
module enclosure_lid() {
    color("Tomato") {
        difference() {
            // Main hollow lid body
            difference() {
                // Outer shell
                translate([0, 0, split_z])
                    centered_cube(cavity_x + 2*wall, cavity_y + 2*wall, (cavity_z - split_z) + wall);
                
                // Inner cavity
                translate([0, 0, split_z - 1.0])
                    centered_cube(cavity_x, cavity_y, (cavity_z - split_z) + 1.0);
            }
            
            // Pocket step to receive base lip (Lid female joint)
            translate([0, 0, split_z - 0.01])
                difference() {
                    // Pocket outer bounds (nominal dimensions, clearance is applied on the base lip)
                    centered_cube(cavity_x + wall, cavity_y + wall, lip_h + 0.02);
                    
                    // Keep the inner cavity area open
                    translate([0, 0, -0.5])
                        centered_cube(cavity_x, cavity_y, lip_h + 1.0);
                }
        }
    }
}

// --- Assembly Render ---
enclosure_base();
enclosure_lid();