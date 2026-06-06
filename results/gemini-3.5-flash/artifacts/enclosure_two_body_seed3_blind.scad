// =========================================================================
// 3D-PRINTABLE TWO-PART ENCLOSURE WITH STEP JOINT
// Designed with Design-for-Manufacturing (DFM) Principles
// Features: Rounded corners to prevent warping/stress, 
//           built-in self-aligning step joint, and 
//           proper 3D-printing tolerances.
// =========================================================================

/* [Enclosure Dimensions] */
// Minimum internal cavity dimensions
cavity_width = 50.0;
cavity_length = 50.0;
cavity_height = 30.0;
wall_thickness = 3.0;

/* [Printer & Joint Tolerances] */
// Radial and vertical clearance between mating surfaces
clearance = 0.25; 

/* [Aesthetics] */
// Outer corner radius (must be greater than wall_thickness)
r_ext = 5.0;

/* [Visualization] */
// Explode distance to inspect the joint (set to 0 for assembled view)
explode = 0.0; 

/* [Derived Parameters] */
r_int = max(0.1, r_ext - wall_thickness);
ext_width = cavity_width + 2 * wall_thickness;
ext_length = cavity_length + 2 * wall_thickness;
ext_height = cavity_height + 2 * wall_thickness;

// Split height (Base outer height - approximately 70% of total height)
base_height_ext = round((cavity_height + 2 * wall_thickness) * 0.7);
lid_height_ext = ext_height - base_height_ext;

// Joint Dimensions
lip_height = 3.0;
lip_thickness = wall_thickness / 2;

$fn = 64;

// Helper module for a robust rounded column
module rounded_column(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];
    hull() {
        translate([-x/2 + r, -y/2 + r, 0]) cylinder(r=r, h=z);
        translate([ x/2 - r, -y/2 + r, 0]) cylinder(r=r, h=z);
        translate([-x/2 + r,  y/2 - r, 0]) cylinder(r=r, h=z);
        translate([ x/2 - r,  y/2 - r, 0]) cylinder(r=r, h=z);
    }
}

// Base Component
module base() {
    color("LightBlue") {
        difference() {
            union() {
                // Main outer body of base
                rounded_column([ext_width, ext_length, base_height_ext], r_ext);
                
                // Mating lip extending upwards
                translate([0, 0, base_height_ext])
                    rounded_column([
                        cavity_width + 2 * lip_thickness, 
                        cavity_length + 2 * lip_thickness, 
                        lip_height
                    ], r_int + lip_thickness);
            }
            
            // Inner cavity carve-out
            translate([0, 0, wall_thickness])
                rounded_column([cavity_width, cavity_length, ext_height], r_int);
        }
    }
}

// Lid Component
module lid() {
    color("LightGreen") {
        translate([0, 0, explode]) {
            difference() {
                // Main outer body of lid
                translate([0, 0, base_height_ext])
                    rounded_column([ext_width, ext_length, lid_height_ext], r_ext);
                
                // Inner cavity carve-out for lid portion
                translate([0, 0, base_height_ext])
                    rounded_column([cavity_width, cavity_length, lid_height_ext - wall_thickness], r_int);
                
                // Recess for base lip with tolerance clearance added
                translate([0, 0, base_height_ext - 0.1])
                    rounded_column([
                        cavity_width + 2 * lip_thickness + 2 * clearance, 
                        cavity_length + 2 * lip_thickness + 2 * clearance, 
                        lip_height + clearance + 0.1
                    ], r_int + lip_thickness + clearance);
            }
        }
    }
}

// Render both parts in their specified positions
base();
lid();