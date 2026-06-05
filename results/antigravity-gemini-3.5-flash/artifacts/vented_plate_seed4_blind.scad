// ==========================================
// Lightened 3D-Printable Mounting Plate
// ==========================================
// Dimensions: 70.0 mm x 60.0 mm x 3.0 mm
// Design for Manufacturing (DFM) details:
// - Outer dimensions are exactly 70 x 60 mm.
// - Grid pattern reduces printed mass to ~32.6% of a solid plate.
// - All walls, both internal and boundary, are exactly 2.0 mm thick.
// - Cutouts have 2.0 mm rounded corners to reduce stress concentration,
//   improve printability (prevents sharp corner warping), and enhance aesthetics.
// ==========================================

$fn = 60;
pi = 3.141592653589793;

// Outer Dimensions
plate_w = 70.0;
plate_h = 60.0;
plate_t = 3.0;

// Wall thickness parameter
wall_min = 2.0;

// Grid of cutouts (Columns x Rows)
nx = 5;
ny = 4;

// Calculate cutout dimensions dynamically to guarantee wall_min thickness
cutout_w = (plate_w - (nx + 1) * wall_min) / nx;
cutout_h = (plate_h - (ny + 1) * wall_min) / ny;
corner_r = 2.0;

// Output design metrics to console
echo("=== MOUNTING PLATE MANIFEST ===");
echo(str("Outer Dimensions: ", plate_w, " mm x ", plate_h, " mm x ", plate_t, " mm"));
echo(str("Internal Wall Spacing: ", wall_min, " mm"));
echo(str("Boundary Wall Spacing: ", wall_min, " mm"));
echo(str("Cutout Geometry: ", cutout_w, " mm width, ", cutout_h, " mm height, ", corner_r, " mm corner radius"));
echo(str("Total Cutouts: ", nx * ny));
echo(str("Volume/Mass Remaining: ", ( (plate_w * plate_h - (nx * ny * (cutout_w * cutout_h - (4 - pi) * corner_r * corner_r))) / (plate_w * plate_h) ) * 100, "% of solid"));
echo("===============================");

// 2D Profile of a single rounded rectangle cutout
module cutout_shape() {
    hull() {
        translate([corner_r, corner_r]) circle(r = corner_r);
        translate([cutout_w - corner_r, corner_r]) circle(r = corner_r);
        translate([cutout_w - corner_r, cutout_h - corner_r]) circle(r = corner_r);
        translate([corner_r, cutout_h - corner_r]) circle(r = corner_r);
    }
}

// Full 2D profile of the plate
module plate_profile() {
    difference() {
        // Outer bounds of the plate
        square([plate_w, plate_h]);
        
        // Subtract grid of cutouts
        for (i = [0 : nx - 1]) {
            for (j = [0 : ny - 1]) {
                x_pos = wall_min + i * (cutout_w + wall_min);
                y_pos = wall_min + j * (cutout_h + wall_min);
                translate([x_pos, y_pos]) cutout_shape();
            }
        }
    }
}

// 3D Model Generation (Single Solid Body)
linear_extrude(height = plate_t) {
    plate_profile();
}