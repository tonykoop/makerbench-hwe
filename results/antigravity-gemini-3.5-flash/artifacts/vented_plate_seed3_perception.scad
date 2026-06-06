// =========================================================================
// METADATA & DFM MANIFEST (BOM)
// =========================================================================
// Part Name:             Lightened Mounting Plate
// Outer Dimensions:      70.0 mm x 50.0 mm x 4.0 mm
// Mass / Volume Target:  < 50% of solid plate (Solid Volume = 14,000 mm³)
// Actual Part Volume:    ~5,123 mm³ (~36.6% of solid volume)
// Mass Reduction:        ~63.4% saved
// Min Wall Thickness:    3.0 mm (Perimeter & Ribs), 3.25 mm (Screw Bosses)
// Fastener Interface:    4x M4 Bolts (4.5 mm clearance holes, 56x36 mm spacing)
// Printability:          Flat layout, no overhangs, no support required
// =========================================================================

echo("--- PART MANIFEST ---");
echo("Part: Lightened Mounting Plate");
echo("Dimensions: 70 x 50 x 4.0 mm");
echo("Volume Reduction: ~63.4% (Mass is less than 50% of solid)");
echo("Minimum Wall Thickness: >= 2.0 mm (Actual minimum is 3.0 mm)");
echo("Mounting: 4x M4 clearance holes at 56x36 mm spacing");

$fn = 60; // High resolution for circular features

// --- Global Parametric Variables ---
width = 70.0;
length = 50.0;
thickness = 4.0;

// Fastener configuration (M4 Clearance)
hole_d = 4.5;
hole_r = hole_d / 2;
hole_x = 28.0; // +/- 28mm gives 56mm spacing
hole_y = 18.0; // +/- 18mm gives 36mm spacing

// Wall and rib thicknesses (all >= 2.0 mm)
wall_outer = 3.0;
wall_inner = 3.0;
boss_r = 5.5;  // Solid material boss around the mounting hole (wall thickness = 3.25mm)

// Helper module: 2D Rounded Rectangle
module rounded_rect(w, l, r) {
    hull() {
        translate([-w/2 + r, -l/2 + r]) circle(r);
        translate([ w/2 - r, -l/2 + r]) circle(r);
        translate([-w/2 + r,  l/2 - r]) circle(r);
        translate([ w/2 - r,  l/2 - r]) circle(r);
    }
}

// 2D Profile of a single quadrant pocket with boss protection
module pocket_quadrant() {
    difference() {
        // Base pocket region in top-right quadrant
        translate([wall_inner/2, wall_inner/2])
        hull() {
            r_pocket = 3.0; // Filleted corner to avoid stress concentrations
            w_pocket = width/2 - wall_outer - wall_inner/2;
            l_pocket = length/2 - wall_outer - wall_inner/2;
            
            translate([r_pocket, r_pocket]) circle(r_pocket);
            translate([w_pocket - r_pocket, r_pocket]) circle(r_pocket);
            translate([r_pocket, l_pocket - r_pocket]) circle(r_pocket);
            translate([w_pocket - r_pocket, l_pocket - r_pocket]) circle(r_pocket);
        }
        // Keep this area solid for the mounting bolt boss
        translate([hole_x, hole_y]) circle(r=boss_r);
    }
}

// Full 2D Plate template with mounting holes and lightening pockets
module plate_2d() {
    difference() {
        // 1. Outer plate boundary (with 2mm corner radius for safety/aesthetics)
        rounded_rect(width, length, 2.0);
        
        // 2. Symmetric pockets mirrored across both axes
        for (mx = [0, 1]) {
            for (my = [0, 1]) {
                mirror([mx, 0]) mirror([0, my]) pocket_quadrant();
            }
        }
        
        // 3. Four M4 mounting holes
        translate([ hole_x,  hole_y]) circle(r=hole_r);
        translate([-hole_x,  hole_y]) circle(r=hole_r);
        translate([ hole_x, -hole_y]) circle(r=hole_r);
        translate([-hole_x, -hole_y]) circle(r=hole_r);
    }
}

// Final 3D Extruded Plate
linear_extrude(height=thickness) {
    plate_2d();
}