$fn = 64;

eps = 0.05;

// Required cavity and shell
wall            = 2.5;
floor_t         = 2.5;
cavity_xy       = 85;    // leaves a clear central span > 70 mm even with corner bosses
cavity_h        = 20;
base_outer      = cavity_xy + 2 * wall;
base_h          = floor_t + cavity_h;

// Fastener / insert geometry
m3_clear_d      = 3.4;   // typical M3 printed clearance
insert_bore_d   = 4.4;   // typical pilot for M3 heat-set insert, tune to insert vendor
insert_depth    = 5.6;
boss_d          = 8.2;
boss_edge_inset = 5.8;   // screw axis from base outer edge; keeps min wall >= 1.5 mm

// Lid fit
fit_clear       = 0.35;  // radial clearance around base outer wall
lid_top_t       = 2.5;
lid_skirt_t     = 2.5;
lid_skirt_h     = 7.0;
lid_inner       = base_outer + 2 * fit_clear;
lid_outer       = lid_inner + 2 * lid_skirt_t;

// Lightening while holding min wall >= 1.5 mm
bottom_relief_xy = 72;
bottom_relief_d  = 0.9;  // leaves 1.6 mm base floor at relief center
top_relief_xy    = 70;
top_relief_d     = 0.8;  // leaves 1.7 mm lid top at relief center

// Display lid aligned over base, but lifted clear so solids do not interfere
display_gap     = 0.4;
lid_lift_z      = base_h + lid_skirt_h + display_gap;

module screw_pattern() {
    for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * (base_outer / 2 - boss_edge_inset),
                   sy * (base_outer / 2 - boss_edge_inset),
                   0])
            children();
}

module base_part() {
    difference() {
        union() {
            difference() {
                // Outer shell
                translate([-base_outer/2, -base_outer/2, 0])
                    cube([base_outer, base_outer, base_h]);

                // Main internal cavity
                translate([-cavity_xy/2, -cavity_xy/2, floor_t])
                    cube([cavity_xy, cavity_xy, cavity_h + eps]);

                // Bottom lightening pocket from underside
                translate([-bottom_relief_xy/2, -bottom_relief_xy/2, -eps])
                    cube([bottom_relief_xy, bottom_relief_xy, bottom_relief_d + eps]);
            }

            // Insert bosses rising from floor to lid interface plane
            screw_pattern()
                translate([0, 0, floor_t])
                    cylinder(h = cavity_h, d = boss_d);
        }

        // Heat-set insert bores from the top of the bosses
        screw_pattern()
            translate([0, 0, base_h - insert_depth])
                cylinder(h = insert_depth + eps, d = insert_bore_d);
    }
}

module lid_part() {
    difference() {
        union() {
            // Top plate
            translate([-lid_outer/2, -lid_outer/2, 0])
                cube([lid_outer, lid_outer, lid_top_t]);

            // Telescoping outer skirt
            difference() {
                translate([-lid_outer/2, -lid_outer/2, -lid_skirt_h])
                    cube([lid_outer, lid_outer, lid_skirt_h]);

                translate([-lid_inner/2, -lid_inner/2, -lid_skirt_h - eps])
                    cube([lid_inner, lid_inner, lid_skirt_h + 2*eps]);
            }
        }

        // M3 clearance holes through lid
        screw_pattern()
            translate([0, 0, -eps])
                cylinder(h = lid_top_t + 2*eps, d = m3_clear_d);

        // Top-side lightening pocket, kept clear of the screw field
        translate([-top_relief_xy/2, -top_relief_xy/2, lid_top_t - top_relief_d])
            cube([top_relief_xy, top_relief_xy, top_relief_d + eps]);
    }
}

// Render as two separate solids, aligned for assembly, with non-interference gap
base_part();
translate([0, 0, lid_lift_z]) lid_part();