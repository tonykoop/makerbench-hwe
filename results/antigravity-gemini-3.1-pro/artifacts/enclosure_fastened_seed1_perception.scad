// =========================================================================
// BILL OF MATERIALS (BOM)
// =========================================================================
// - 1x Enclosure Base (3D-printed PETG/PLA)
// - 1x Enclosure Lid (3D-printed PETG/PLA)
// - 4x M3 Heat-Set Inserts (Part Number: MB-HSI-M3)
// - 4x M3 Socket Head Cap Screws, 10mm length (Part Number: MB-SHCS-M3-10)
// =========================================================================

$fn = 64; // High-quality circles

// --- DESIGN PARAMETERS ---
// Internal clear cavity dimensions
cavity_x = 50.0;
cavity_y = 40.0;
cavity_z = 30.0;

wall_thickness = 2.0;

// Hardware: MB-HSI-M3
insert_boss_hole_dia = 4.0;
insert_length = 4.0;
insert_min_boss_wall = 1.5;

// Hardware: MB-SHCS-M3-10
screw_head_dia = 5.5;
screw_head_height = 3.0;
screw_clearance_dia = 3.4; // normal clearance hole
screw_length = 10.0;

// Derived Dimensions
boss_radius = 4.0; // Outer radius of screw boss

// Screw center coordinates to ensure clear cavity of 50x40
screw_x = cavity_x / 2 + boss_radius + 0.5; // 25 + 4 + 0.5 = 29.5
screw_y = cavity_y / 2 + boss_radius + 0.5; // 20 + 4 + 0.5 = 24.5

// Inner cavity boundaries
inner_x = (screw_x + boss_radius) * 2; // (29.5 + 4) * 2 = 67.0
inner_y = (screw_y + boss_radius) * 2; // (24.5 + 4) * 2 = 57.0
inner_z = cavity_z;                    // 30.0

// Outer dimensions
outer_x = inner_x + 2 * wall_thickness; // 71.0
outer_y = inner_y + 2 * wall_thickness; // 61.0
outer_z = inner_z + wall_thickness;     // 32.0

// Step joint for self-alignment and dust sealing
step_w = 1.0;
step_h = 1.5;
joint_clearance = 0.15; // DFM tolerance gap

// Lid parameters
lid_thickness = 5.0;

// --- MODULES ---

module base() {
    difference() {
        // Main body with cavity and corner bosses
        union() {
            difference() {
                // Main outer block (from Z = -wall_thickness to Z = inner_z + step_h)
                translate([-outer_x/2, -outer_y/2, -wall_thickness])
                    cube([outer_x, outer_y, inner_z + wall_thickness + step_h]);
                
                // Subtract main internal cavity
                translate([-inner_x/2, -inner_y/2, 0])
                    cube([inner_x, inner_y, inner_z + step_h + 0.1]);
            }
            
            // Corner bosses for heat-set inserts (extending inside the cavity)
            for (x = [-screw_x, screw_x]) {
                for (y = [-screw_y, screw_y]) {
                    // Cylinder boss
                    translate([x, y, -wall_thickness])
                        cylinder(r=boss_radius, h=inner_z + wall_thickness + step_h);
                    
                    // Corner fill block to avoid wedge gap and ensure structural integrity
                    x_offset = (x < 0) ? -boss_radius : 0;
                    y_offset = (y < 0) ? -boss_radius : 0;
                    translate([x + x_offset, y + y_offset, -wall_thickness])
                        cube([boss_radius, boss_radius, inner_z + wall_thickness + step_h]);
                }
            }
        }
        
        // Subtract screw relief holes and heat-set insert holes
        for (x = [-screw_x, screw_x]) {
            for (y = [-screw_y, screw_y]) {
                // Boss hole for heat-set insert (MB-HSI-M3: 4.0 mm dia, 4.5 mm depth)
                translate([x, y, inner_z + step_h - insert_length - 0.5])
                    cylinder(d=insert_boss_hole_dia, h=insert_length + 0.6);
                
                // Through-hole for screw shank clearance/relief (3.4 mm)
                translate([x, y, -wall_thickness - 0.1])
                    cylinder(d=screw_clearance_dia, h=inner_z + wall_thickness + step_h + 0.2);
            }
        }
        
        // Subtract the outer step joint rebate
        translate([0, 0, inner_z])
            difference() {
                translate([-outer_x/2 - 0.1, -outer_y/2 - 0.1, 0])
                    cube([outer_x + 0.2, outer_y + 0.2, step_h + 0.1]);
                translate([-(outer_x - 2*step_w)/2, -(outer_y - 2*step_w)/2, -0.1])
                    cube([outer_x - 2*step_w, outer_y - 2*step_w, step_h + 0.3]);
            }
    }
}

module lid() {
    difference() {
        // Main lid block
        translate([-outer_x/2, -outer_y/2, inner_z])
            cube([outer_x, outer_y, lid_thickness + step_h]);
        
        // Subtract the inner recess of the step joint (creates outer mating rim)
        translate([-(outer_x - 2*step_w + 2*joint_clearance)/2, -(outer_y - 2*step_w + 2*joint_clearance)/2, inner_z - 0.1])
            cube([outer_x - 2*step_w + 2*joint_clearance, outer_y - 2*step_w + 2*joint_clearance, step_h + 0.1]);
        
        // Subtract screw holes and counterbores
        for (x = [-screw_x, screw_x]) {
            for (y = [-screw_y, screw_y]) {
                // Clearance hole for M3 screw shank (3.4 mm dia)
                translate([x, y, inner_z - 0.1])
                    cylinder(d=screw_clearance_dia, h=lid_thickness + step_h + 0.2);
                
                // Counterbore pocket for screw head (6.5 mm dia, 3.2 mm depth)
                translate([x, y, inner_z + lid_thickness + step_h - screw_head_height - 0.2])
                    cylinder(d=6.5, h=screw_head_height + 0.3);
            }
        }
    }
}

// --- RENDERING ---
// Render both parts in their assembled position with distinct colors
// Translucent lid allows visualization of the interior cavity and screw bosses

color([0.25, 0.28, 0.32, 1.0]) // Matte Charcoal Gray for the Base
    base();

color([0.95, 0.45, 0.10, 0.75]) // High-vis Orange Translucent Acrylic for the Lid
    lid();