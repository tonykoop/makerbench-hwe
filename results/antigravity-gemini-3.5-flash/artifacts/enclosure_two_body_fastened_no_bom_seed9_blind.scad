// =========================================================================
// 3D-Printable Two-Part Enclosure with M3 Fasteners
// Designed by Antigravity
// =========================================================================

/* [Enclosure Cavity Dimensions] */
// Minimum internal cavity length (X)
cavity_x = 70;         
// Minimum internal cavity width (Y)
cavity_y = 60;         
// Minimum internal cavity height (Z)
cavity_z = 30;         
// Wall thickness
wall_thickness = 2.0;  

/* [Lid Parameters] */
// Thickness of the lid
lid_thickness = 5.0;   

/* [Fastener Parameters (M3)] */
// Clearance hole diameter for M3 screw shank
screw_sh_dia = 3.3;    
// Counterbore diameter for M3 socket head
screw_hd_dia = 6.5;    
// Counterbore depth for M3 socket head
screw_hd_h = 3.5;      
// Bore diameter for M3 heat-set insert (standard knurled pocket)
insert_dia = 4.0;      
// Pocket depth for M3 heat-set insert
insert_depth = 5.5;    

/* [Visualization Options] */
// Exploded view gap (set to 0 for exact assembly position)
exploded_gap = 0;      
// Toggle screw and brass insert rendering
show_fasteners = true; 

/* [Rendering Quality] */
$fn = 64;              

// Derived layout parameters
// Screw positions are set outside the 70x60 cavity with clearance
screw_x = cavity_x / 2 + wall_thickness + 2.0; // 35 + 2 + 2 = 39 mm
screw_y = cavity_y / 2 + wall_thickness + 2.0; // 30 + 2 + 2 = 34 mm
boss_radius = 5.5;                             // Corner boss radius (provides wall around inserts)
base_height = cavity_z + wall_thickness;        // Total base height (32 mm)

// 2D Profile for Extrusion
module base_profile_2d() {
    union() {
        // Main rectangular body footprint
        square([cavity_x + 2*wall_thickness, cavity_y + 2*wall_thickness], center=true);
        
        // Corner bosses for the screws and heat-set inserts
        for (x = [-screw_x, screw_x]) {
            for (y = [-screw_y, screw_y]) {
                translate([x, y])
                    circle(r=boss_radius);
            }
        }
    }
}

// Base Enclosure Solid
module base() {
    color("#2B2B2B") { // Charcoal Gray
        difference() {
            // Main extruded outer shape
            linear_extrude(height=base_height) {
                base_profile_2d();
            }
            
            // Rectangular cavity
            translate([0, 0, wall_thickness]) {
                linear_extrude(height=cavity_z + 1.0) {
                    square([cavity_x, cavity_y], center=true);
                }
            }
            
            // Fastener pocket bores in the base corners
            for (x = [-screw_x, screw_x]) {
                for (y = [-screw_y, screw_y]) {
                    translate([x, y, base_height]) {
                        // Heat-set insert bore (top section of hole)
                        translate([0, 0, -insert_depth])
                            cylinder(d=insert_dia, h=insert_depth + 0.1);
                        
                        // Deep clearance hole below the insert to prevent screw bottoming
                        translate([0, 0, -13.0])
                            cylinder(d=screw_sh_dia, h=13.0 - insert_depth + 0.1);
                    }
                }
            }
        }
    }
}

// Lid Solid
module lid() {
    color("#FF5A5F") { // Matte Coral
        difference() {
            // Main extruded lid profile
            linear_extrude(height=lid_thickness) {
                base_profile_2d();
            }
            
            // Fastener clearance and counterbore holes
            for (x = [-screw_x, screw_x]) {
                for (y = [-screw_y, screw_y]) {
                    // Clearance hole through the lid
                    translate([x, y, -0.5])
                        cylinder(d=screw_sh_dia, h=lid_thickness + 1.0);
                    
                    // Counterbore for screw head from the top face
                    translate([x, y, lid_thickness - screw_hd_h])
                        cylinder(d=screw_hd_dia, h=screw_hd_h + 0.1);
                }
            }
        }
    }
}

// Brass Heat-Set Insert Visualization Model
module brass_insert() {
    color("#D4AF37") { // Brass / Gold color
        difference() {
            cylinder(d=4.6, h=5.0);
            translate([0, 0, -0.5])
                cylinder(d=3.0, h=6.0);
        }
    }
}

// M3 Socket Head Cap Screw Visualization Model
module m3_screw(length=12) {
    color("#7F8C8D") { // Steel Gray
        // Screw Head
        cylinder(d=5.5, h=3.0);
        // Screw Shank
        translate([0, 0, -length])
            cylinder(d=3.0, h=length);
    }
}

// --- ASSEMBLY RENDERING ---

// 1. Render Base Enclosure
base();

// 2. Render Lid (translated for assembly/explosion)
translate([0, 0, base_height + exploded_gap]) {
    lid();
}

// 3. Render Fasteners (if toggled on)
if (show_fasteners) {
    // Brass inserts installed in the base
    for (x = [-screw_x, screw_x]) {
        for (y = [-screw_y, screw_y]) {
            translate([x, y, base_height - 5.0])
                brass_insert();
        }
    }
    
    // M3 screws positioned inside the lid counterbores
    for (x = [-screw_x, screw_x]) {
        for (y = [-screw_y, screw_y]) {
            translate([x, y, base_height + exploded_gap + lid_thickness - 3.0])
                m3_screw(length=12);
        }
    }
}