$fn = 72;

// Units: mm
eps = 0.05;

wall = 2.0;
bottom = wall;

body_x = 60;
body_y = 50;
cavity_z = 30;
body_z = bottom + cavity_z;

cavity_x = body_x - 2 * wall;
cavity_y = body_y - 2 * wall;

// Corner fastener lugs outside the main cavity wall
lug = 12;
fastener_x = body_x / 2 + lug / 2;
fastener_y = body_y / 2 + lug / 2;

// Lid and fasteners
lid_t = 3.0;
gap = 0.3;
lid_x = body_x + 2 * lug;
lid_y = body_y + 2 * lug;

m3_clear = 3.5;       // M3 socket-head cap screw clearance hole
insert_bore = 4.5;    // Generic M3 heat-set insert bore
insert_depth = 5.5;

lid_center_z = body_z + gap + lid_t / 2;

module base() {
    difference() {
        union() {
            // Main tub
            translate([0, 0, body_z / 2])
                cube([body_x, body_y, body_z], center = true);

            // Corner insert lugs
            for (sx = [-1, 1])
                for (sy = [-1, 1])
                    translate([sx * fastener_x, sy * fastener_y, body_z / 2])
                        cube([lug, lug, body_z], center = true);
        }

        // Internal cavity: 56 x 46 x 30 mm with 2.0 mm walls and 2.0 mm bottom
        translate([0, 0, bottom + cavity_z / 2 + eps / 2])
            cube([cavity_x, cavity_y, cavity_z + eps], center = true);

        // Blind bores for heat-set inserts, one near each corner
        for (sx = [-1, 1])
            for (sy = [-1, 1])
                translate([sx * fastener_x, sy * fastener_y, body_z - insert_depth / 2])
                    cylinder(h = insert_depth + eps, d = insert_bore, center = true);
    }
}

module lid() {
    difference() {
        translate([0, 0, lid_center_z])
            cube([lid_x, lid_y, lid_t], center = true);

        // Clearance holes through the lid, aligned to the base insert bores
        for (sx = [-1, 1])
            for (sy = [-1, 1])
                translate([sx * fastener_x, sy * fastener_y, lid_center_z])
                    cylinder(h = lid_t + 2 * eps, d = m3_clear, center = true);
    }
}

base();
lid();