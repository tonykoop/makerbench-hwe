// Constant-thickness 2.0 mm sheet-metal L-bracket
// Designed for manufacturing (DFM) with exact flat-pattern calculations.

// --- Parameters ---
thickness = 2.0;             // Sheet metal thickness (T)
inside_radius = 2.0;         // Inside bend radius (R)
width = 30.0;                // Bracket width (W)
flange1_outside = 40.0;      // Outside length of Flange 1 (L1)
flange2_outside = 30.0;      // Outside length of Flange 2 (L2)
k_factor = 0.45;             // K-factor for bend allowance calculation
angle = 90.0;                // Bend angle in degrees

// --- Calculations ---
PI = 3.141592653589793;
outside_radius = inside_radius + thickness;

// Standard sheet metal flat pattern formula:
// Flat Length = (L1 - T - R) + (L2 - T - R) + Bend Allowance
flange1_flat = flange1_outside - thickness - inside_radius;
flange2_flat = flange2_outside - thickness - inside_radius;
bend_allowance = (angle * PI / 180.0) * (inside_radius + k_factor * thickness);
flat_length = flange1_flat + flange2_flat + bend_allowance;

// --- Print Manifest to Console ---
echo(str("MAKERBENCH-SHEETMETAL: {\"thickness_mm\": ", thickness, ", \"bend_radius_mm\": ", inside_radius, ", \"flat_length_mm\": ", flat_length, "}"));

// --- 3D Model Generation ---
module l_bracket() {
    // Center of the concentric bend radii
    center = [outside_radius, outside_radius];
    steps = 30; // Resolution of the 90-degree bend
    
    // Generate outside arc points (from 180 to 270 degrees)
    outside_arc = [
        for (i = [0 : steps]) 
            let(a = 180 + (90 * i / steps))
            [center[0] + outside_radius * cos(a), center[1] + outside_radius * sin(a)]
    ];
    
    // Generate inside arc points (from 270 down to 180 degrees)
    inside_arc = [
        for (i = [0 : steps]) 
            let(a = 270 - (90 * i / steps))
            [center[0] + inside_radius * cos(a), center[1] + inside_radius * sin(a)]
    ];
    
    // Combine into a single continuous 2D profile path (counter-clockwise)
    profile_points = concat(
        [[0, flange2_outside]],          // Top-left outside corner
        outside_arc,                      // Outside corner bend
        [[flange1_outside, 0]],          // Bottom-right outside corner
        [[flange1_outside, thickness]],  // Bottom-right inside corner
        inside_arc,                       // Inside corner bend
        [[thickness, flange2_outside]]   // Top-left inside corner
    );
    
    // Extrude the 2D profile symmetrically along the Y-axis
    linear_extrude(height = width, center = true, convexity = 10) {
        polygon(points = profile_points);
    }
}

// Render the final bracket
l_bracket();