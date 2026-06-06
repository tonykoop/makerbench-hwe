// Parametric 3D-printable two-part enclosure
// All units in mm

// Cavity dimensions (internal)
W_int = 50.0;
L_int = 60.0;
H_int_base = 15.0;
H_int_lid = 5.0;

// Wall thickness
t = 3.0;

// Joint/Lip dimensions
lip_h = 2.0;       // height of the mating lip step
lip_w = 1.5;       // width of the mating lip step
c = 0.2;           // nominal print clearance

// Calculated dimensions
W_ext = W_int + 2 * t;
L_ext = L_int + 2 * t;
H_base_ext = H_int_base + t;

module base() {
    difference() {
        union() {
            // Main lower box body
            translate([-W_ext/2, -L_ext/2, 0])
                cube([W_ext, L_ext, H_base_ext]);
            
            // Outer lip extension
            translate([-W_ext/2, -L_ext/2, H_base_ext])
                difference() {
                    cube([W_ext, L_ext, lip_h]);
                    translate([lip_w, lip_w, -0.1])
                        cube([W_ext - 2*lip_w, L_ext - 2*lip_w, lip_h + 0.2]);
                }
        }
        
        // Inner cavity of base
        translate([-W_int/2, -L_int/2, t])
            cube([W_int, L_int, H_base_ext + lip_h + 0.1]);
    }
}

module lid() {
    // Z-coordinates for the lid in its assembled position
    z_lid_lip_bottom = H_base_ext + c;
    z_lid_flange = H_base_ext + lip_h + c;
    z_lid_ceiling = z_lid_lip_bottom + H_int_lid;
    z_lid_top = z_lid_ceiling + t;
    
    difference() {
        union() {
            // Main upper box (outer part of lid)
            translate([-W_ext/2, -L_ext/2, z_lid_flange])
                cube([W_ext, L_ext, z_lid_top - z_lid_flange]);
            
            // Lid's inner lip extending downwards into base cavity
            translate([-(W_ext/2 - lip_w - c), -(L_ext/2 - lip_w - c), z_lid_lip_bottom])
                cube([W_ext - 2*(lip_w + c), L_ext - 2*(lip_w + c), z_lid_flange - z_lid_lip_bottom]);
        }
        
        // Inner cavity of lid
        translate([-W_int/2, -L_int/2, z_lid_lip_bottom - 0.1])
            cube([W_int, L_int, (z_lid_ceiling - z_lid_lip_bottom) + 0.1]);
    }
}

// Render both components in their non-interfering assembled positions
base();
lid();