// ============================================================================
// 3D-Printable Two-Part Enclosure with M3 Heat-Set Insert Fasteners
// ============================================================================
// Design Features:
// - Internal Cavity: 50 x 50 x 30 mm
// - Wall Thickness: 3.0 mm
// - Fasteners: 4x M3 Socket-Head Cap Screws into M3 heat-set inserts
// - Alignment: Co-axial clearance holes in lid and insert bores in base
// ============================================================================

// --- Parameters ---
wall = 3.0;             // Enclosure wall thickness
cavity_w = 50.0;        // Internal cavity width (X)
cavity_l = 50.0;        // Internal cavity length (Y)
cavity_h = 30.0;        // Internal cavity height (Z)

// M3 Fastener & Insert Tolerances (Optimized for 3D Printing)
clearance_hole_d = 3.4;  // M3 screw clearance hole diameter (allows for printer tolerances)
insert_hole_d = 4.0;     // Bore diameter for standard M3 heat-set inserts (e.g., Ruthex/short)
insert_hole_depth = 6.0; // Depth of the insert bore to prevent bottoming out

// Corner Bosses & Rounding
outer_r = 5.0;           // Outer enclosure corner radius
boss_r = 5.0;            // Boss radius for screw holes
boss_offset = 2.5;       // Offset from the inner cavity walls to center the screw holes

// Explode view offset (set to > 0 to separate lid and base visually)
explode_z = 0;

// Calculated Hole Positions (Centered around X=0, Y=0)
hole_x = cavity_w / 2 - boss_offset;
hole_y = cavity_l / 2 - boss_offset;
hole_positions = [
    [ hole_x,  hole_y],
    [-hole_x,  hole_y],
    [ hole_x, -hole_y],
    [-hole_x, -hole_y]
];

// --- Modules ---

// Helper for a robust, fast-rendering rounded box
module rounded_box(w, l, h, r) {
    hull() {
        translate([-w/2 + r, -l/2 + r, 0]) cylinder(r=r, h=h, $fn=32);
        translate([ w/2 - r, -l/2 + r, 0]) cylinder(r=r, h=h, $fn=32);
        translate([-w/2 + r,  l/2 - r, 0]) cylinder(r=r, h=h, $fn=32);
        translate([ w/2 - r,  l/2 - r, 0]) cylinder(r=r, h=h, $fn=32);
    }
}

// Enclosure Base
module enclosure_base() {
    difference() {
        union() {
            // Main outer body (bottom wall extends downwards from Z=0 to Z=-wall)
            translate([0, 0, -wall])
                rounded_box(cavity_w + 2*wall, cavity_l + 2*wall, cavity_h + wall, outer_r);
            
            // Corner bosses to anchor the heat-set inserts
            for (pos = hole_positions) {
                translate([pos[0], pos[1], 0])
                    cylinder(r=boss_r, h=cavity_h, $fn=32);
            }
        }
        
        // Subtract the internal cavity
        translate([-cavity_w/2, -cavity_l/2, 0])
            cube([cavity_w, cavity_l, cavity_h + 0.1]);
        
        // Subtract the heat-set insert bores
        for (pos = hole_positions) {
            translate([pos[0], pos[1], cavity_h - insert_hole_depth])
                cylinder(d=insert_hole_d, h=insert_hole_depth + 0.1, $fn=32);
        }
    }
}

// Enclosure Lid
module enclosure_lid() {
    difference() {
        // Main lid plate (sits on top of the base at Z=cavity_h)
        translate([0, 0, cavity_h])
            rounded_box(cavity_w + 2*wall, cavity_l + 2*wall, wall, outer_r);
        
        // Subtract the screw clearance holes
        for (pos = hole_positions) {
            translate([pos[0], pos[1], cavity_h - 0.1])
                cylinder(d=clearance_hole_d, h=wall + 0.2, $fn=32);
        }
    }
}

// --- Render Assemblies ---

// Render base in its default position
enclosure_base();

// Render lid in its assembled (or exploded) position
translate([0, 0, explode_z])
    enclosure_lid();