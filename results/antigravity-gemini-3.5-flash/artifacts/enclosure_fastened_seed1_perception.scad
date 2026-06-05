/*
======================================================================
3D-PRINTABLE TWO-PART ENCLOSURE WITH INTEGRATED FASTENERS
======================================================================
Design Specifications:
- Internal Cavity: Clear volume of 50 x 40 x 30 mm (unobstructed by corner bosses)
- Wall Thickness: 2.0 mm
- Lid Fastening: 4x M3 Socket Head Cap Screws into M3 Heat-Set Inserts
- Joint Type: Stepped interlocking lip and groove for premium fit and alignment

Selected Off-the-Shelf Hardware:
1. Screws (4x):
   - Part Number: MB-SHCS-M3-12
   - Thread: M3 x 0.5 mm
   - Length: 12.0 mm
   - Head Diameter: 5.5 mm
   - Head Height: 3.0 mm
2. Heat-Set Inserts (4x):
   - Part Number: MB-HSI-M3
   - Thread: M3
   - Length: 4.0 mm
   - Outer Diameter: 4.6 mm
   - Recommended Boss Hole: 4.0 mm
   - Minimum Boss Wall around insert: 1.5 mm (our design provides 2.0 mm wall)

BILL OF MATERIALS (BOM):
----------------------------------------------------------------------
Qty | Part Number    | Category              | Description
----------------------------------------------------------------------
4   | MB-SHCS-M3-12  | Socket Head Cap Screw | M3 thread, 12mm length, Alloy Steel
4   | MB-HSI-M3      | Heat-Set Insert       | M3 thread, 4mm length, Brass
----------------------------------------------------------------------
*/

// ==========================================
// USER PARAMETERS & CONTROL
// ==========================================
// Explode distance (set to 0 for assembled view, or 10-30 for exploded inspection)
explode = 0;

// ==========================================
// DESIGN VARIABLES (Units: mm)
// ==========================================
wall = 2.0;               // Shell wall thickness
floor_thickness = 2.0;    // Bottom wall thickness
ceiling_thickness = 2.0;  // Top wall thickness

// Enclosure heights
base_h = 24.0;            // Base outer height (22mm internal depth)
lid_h = 10.0;             // Lid outer height (8mm internal depth)
// Total internal cavity height = (24-2) + (10-2) = 30.0 mm (exactly meets spec)

// Corner Pillar & Fastener coordinates
// To guarantee a 100% clear 50x40mm central rectangle:
// Screw centers are placed at x = +/- 29, y = +/- 24.
// With a pillar radius of 4.0mm, the closest pillar boundary is at x = 25, y = 20,
// which perfectly clears the corners of the 50x40mm volume.
screw_x = 29.0;
screw_y = 24.0;
pillar_centers = [
    [-screw_x, -screw_y],
    [ screw_x, -screw_y],
    [-screw_x,  screw_y],
    [ screw_x,  screw_y]
];

pillar_r = 4.0;           // Outer radius of corner pillars (8.0mm diameter)
outer_r = 6.0;            // Outer corner radius of the enclosure
inner_r = 4.0;            // Inner corner radius of the enclosure (outer_r - wall)

// Box Dimensions
inner_x = 2 * (screw_x + pillar_r); // 66.0 mm
inner_y = 2 * (screw_y + pillar_r); // 56.0 mm
outer_x = inner_x + 2 * wall;       // 70.0 mm
outer_y = inner_y + 2 * wall;       // 60.0 mm

// Fastener Hole & Boss Specs
insert_hole_r = 2.0;      // M3 heat-set insert hole radius (4.0mm diameter)
insert_hole_depth = 9.0;  // Depth for M3 insert + screw thread protrusion (prevents bottoming out)
screw_clearance_r = 1.7;  // M3 clearance hole radius (3.4mm diameter for "normal" fit)
screw_head_r = 3.0;       // M3 SHCS head recess radius (6.0mm diameter for head clearance)
screw_head_depth = 3.5;   // Recess depth (3.0mm head height + 0.5mm clearance for flush finish)

// Joint Lips & Groves (Stepped Parting Line)
lip_height = 1.5;
groove_depth = 1.7;       // 0.2mm vertical clearance
joint_clearance = 0.1;    // Horizontal printer clearance on each side

// ==========================================
// CORE GEOMETRY MODULES
// ==========================================

// Helper for rounded-corner cuboids using cylinders and hull
module rounded_box(w, l, h, r) {
    hull() {
        translate([-w/2 + r, -l/2 + r, 0]) cylinder(h = h, r = r, $fn = 64);
        translate([ w/2 - r, -l/2 + r, 0]) cylinder(h = h, r = r, $fn = 64);
        translate([-w/2 + r,  l/2 - r, 0]) cylinder(h = h, r = r, $fn = 64);
        translate([ w/2 - r,  l/2 - r, 0]) cylinder(h = h, r = r, $fn = 64);
    }
}

// Base Module
module base() {
    difference() {
        union() {
            // Main solid body
            rounded_box(outer_x, outer_y, base_h, outer_r);
            
            // Solid corner pillars (extending from floor to parting line)
            for (pos = pillar_centers) {
                translate([pos[0], pos[1], floor_thickness])
                    cylinder(h = base_h - floor_thickness, r = pillar_r, $fn = 64);
            }
        }
        
        // Main internal cavity pocket
        translate([0, 0, floor_thickness])
            rounded_box(inner_x, inner_y, base_h, inner_r);
            
        // M3 Heat-Set Insert Holes
        for (pos = pillar_centers) {
            translate([pos[0], pos[1], base_h - insert_hole_depth])
                cylinder(h = insert_hole_depth + 1.0, r = insert_hole_r, $fn = 64);
        }
    }
    
    // Interlocking Lip (positioned on the inner half of the base walls)
    translate([0, 0, base_h])
        difference() {
            rounded_box(inner_x + wall, inner_y + wall, lip_height, inner_r + wall/2);
            translate([0, 0, -0.5])
                rounded_box(inner_x, inner_y, lip_height + 1.0, inner_r);
        }
}

// Lid Module
module lid() {
    difference() {
        union() {
            // Main solid lid body
            rounded_box(outer_x, outer_y, lid_h, outer_r);
            
            // Corner pillars inside the lid to support clamping force
            for (pos = pillar_centers) {
                translate([pos[0], pos[1], 0])
                    cylinder(h = lid_h - ceiling_thickness, r = pillar_r, $fn = 64);
            }
        }
        
        // Lid internal cavity pocket
        rounded_box(inner_x, inner_y, lid_h - ceiling_thickness, inner_r);
        
        // Interlocking groove to house the base lip (with tolerances to prevent binding)
        difference() {
            rounded_box(inner_x + wall + joint_clearance, inner_y + wall + joint_clearance, groove_depth, inner_r + wall/2 + joint_clearance/2);
            translate([0, 0, -0.5])
                rounded_box(inner_x - joint_clearance, inner_y - joint_clearance, groove_depth + 1.0, inner_r - joint_clearance/2);
        }
        
        // Screw clearance holes (entirely through the lid)
        for (pos = pillar_centers) {
            translate([pos[0], pos[1], -0.5])
                cylinder(h = lid_h + 1.0, r = screw_clearance_r, $fn = 64);
        }
        
        // Counterbores for screw heads (recessed below top face)
        for (pos = pillar_centers) {
            translate([pos[0], pos[1], lid_h - screw_head_depth])
                cylinder(h = screw_head_depth + 0.5, r = screw_head_r, $fn = 64);
        }
    }
}

// ==========================================
// ASSEMBLY RENDERING
// ==========================================

// Render Base in position
color("#1d3557") {
    base();
}

// Render Lid translated and aligned above the Base
color("#e63946") {
    translate([0, 0, base_h + explode]) {
        lid();
    }
}