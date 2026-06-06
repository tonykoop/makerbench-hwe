$fn = 72;

// =====================
// Units: mm
// =====================
wall        = 2.0;   // target wall thickness
cavity_x    = 52.0;  // internal clear cavity X (>=50)
cavity_y    = 42.0;  // internal clear cavity Y (>=40)
cavity_z    = 30.0;  // internal clear cavity Z (>=30)

base_x      = cavity_x + 2*wall;
base_y      = cavity_y + 2*wall;
base_z      = cavity_z + 2*wall; // includes bottom + top wall
lid_z       = 8.0;

// Fastener / insert interface
screw_margin      = 8.0;   // keep clearance from walls
insert_bore_dia    = 4.55;  // heat-set insert drill clearance (M3)
insert_depth      = 6.0;   // depth into base
lid_clear_dia      = 3.35;  // M3 clearance hole in lid

fast_x = [screw_margin, base_x - screw_margin];
fast_y = [screw_margin, base_y - screw_margin];

module base_part() {
    difference() {
        // Outer body
        cube([base_x, base_y, base_z], center = false);

        // Internal functional cavity (>=50 x 40 x 30 mm)
        translate([wall, wall, wall])
            cube([cavity_x, cavity_y, cavity_z], center = false);

        // Insert bores for heat-set inserts, axis-aligned with lid holes
        for (x = fast_x)
            for (y = fast_y)
                translate([x, y, base_z - insert_depth - 0.01])
                    cylinder(h = insert_depth + 0.02, d = insert_bore_dia, center = false);
    }
}

module lid_part() {
    difference() {
        // Outer lid body
        cube([base_x, base_y, lid_z], center = false);

        // Internal lightening pocket (keeps wall >= 2.0 and total mass aggressively low)
        translate([wall, wall, wall])
            cube([cavity_x, cavity_y, lid_z - 2*wall], center = false);

        // Clearance holes through lid at matching axes
        for (x = fast_x)
            for (y = fast_y)
                translate([x, y, -0.01])
                    cylinder(h = lid_z + 0.02, d = lid_clear_dia, center = false);
    }
}

// Render two separate solids in assembled position
color("lightgray")
    base_part();

translate([0, 0, base_z])
color("white")
    lid_part();