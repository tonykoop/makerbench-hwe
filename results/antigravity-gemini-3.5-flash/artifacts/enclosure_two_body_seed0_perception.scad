// ============================================================================
// 3D-Printable Two-Part Enclosure
// Designed by Antigravity
//
// Key Features:
// - Parametric design with 70x70x20mm minimum internal cavity
// - Uniform 2.5mm wall thickness
// - Snug lip/groove mating joint with 0.2mm nominal printing clearance
// - Rounded corners (6.0mm outer, 3.5mm inner) for strength and aesthetics
// - Semi-transparent lid for visual inspection of mating surfaces
// ============================================================================

// --- Parameters ---
cavity_x = 70.0;      // Internal cavity length (mm)
cavity_y = 70.0;      // Internal cavity width (mm)
cavity_z = 20.0;      // Total internal cavity height (mm)
wall_thick = 2.5;     // Wall thickness (mm)
clearance = 0.2;      // Print clearance between mating parts (mm)

// --- Mating Joint Configuration ---
lip_height = 3.0;     // Vertical height of the alignment lip (mm)
// Lip thickness is half the wall thickness minus half the clearance,
// which balances strength between base and lid flanges.
lip_thick = (wall_thick - clearance) / 2; 

// --- Aesthetics ---
outer_radius = 6.0;   // Outer corner fillet radius (mm)
inner_radius = outer_radius - wall_thick; // Inner corner fillet radius (mm)

// --- Z-Split Partition ---
// We split the 20mm cavity height: 15mm in the base, 5mm in the lid.
base_cavity_z = 15.0; 
lid_cavity_z = cavity_z - base_cavity_z;

// --- Calculated Dimensions ---
outer_x = cavity_x + 2 * wall_thick;
outer_y = cavity_y + 2 * wall_thick;
base_height = base_cavity_z + wall_thick; // Base top mating plane (Z = 17.5)
lid_height = lid_cavity_z + wall_thick;   // Lid height from mating plane (7.5)

// --- Helper Module ---
module rounded_column(w, d, h, r) {
    radius = max(r, 0.01);
    hull() {
        translate([-w/2 + radius, -d/2 + radius, 0]) cylinder(h=h, r=radius, $fn=64);
        translate([ w/2 - radius, -d/2 + radius, 0]) cylinder(h=h, r=radius, $fn=64);
        translate([-w/2 + radius,  d/2 - radius, 0]) cylinder(h=h, r=radius, $fn=64);
        translate([ w/2 - radius,  d/2 - radius, 0]) cylinder(h=h, r=radius, $fn=64);
    }
}

// --- Base Module ---
module base() {
    difference() {
        union() {
            // Main outer shell of base
            rounded_column(outer_x, outer_y, base_height, outer_radius);
            
            // Alignment lip (inner part of the split wall)
            translate([0, 0, base_height])
                rounded_column(cavity_x + 2 * lip_thick, cavity_y + 2 * lip_thick, lip_height, inner_radius + lip_thick);
        }
        
        // Inner cavity subtraction
        translate([0, 0, wall_thick])
            rounded_column(cavity_x, cavity_y, base_height + lip_height + 1.0, inner_radius);
    }
}

// --- Lid Module ---
module lid() {
    // Sits in its assembled position starting at Z = base_height
    translate([0, 0, base_height]) {
        difference() {
            // Main outer shell of lid
            rounded_column(outer_x, outer_y, lid_height, outer_radius);
            
            // Joint pocket to receive base lip (with clearance)
            translate([0, 0, -0.1])
                rounded_column(cavity_x + 2 * lip_thick + 2 * clearance, cavity_y + 2 * lip_thick + 2 * clearance, lip_height + clearance + 0.1, inner_radius + lip_thick + clearance);
            
            // Inner cavity extension into lid
            translate([0, 0, -0.1])
                rounded_column(cavity_x, cavity_y, lid_height - wall_thick + 0.1, inner_radius);
        }
    }
}

// --- Render Assembly ---
// Render base as a solid blue slate
color([0.25, 0.45, 0.85, 1.0]) {
    base();
}

// Render lid as a semi-transparent crimson to inspect internal clearance
color([0.90, 0.35, 0.45, 0.75]) {
    lid();
}