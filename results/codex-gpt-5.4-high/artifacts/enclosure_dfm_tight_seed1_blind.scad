$fn = 64;

eps = 0.01;

// Core enclosure geometry
wall    = 2.0;
floor_t = 2.0;
lid_t   = 2.0;

cavity_x = 62.0;
cavity_y = 52.0;
cavity_z = 30.0;

base_x = cavity_x + 2 * wall;
base_y = cavity_y + 2 * wall;
base_h = cavity_z + floor_t;

// Display as assembled but non-interfering
assembly_gap = 0.20;

// Fastener / insert geometry
screw_clear_d   = 3.4;  // M3 printed clearance
insert_bore_d   = 4.2;  // Typical printed pilot for M3 heat-set inserts
insert_lead_d   = 4.8;  // Lead-in chamfer diameter
insert_lead_h   = 0.8;
insert_total_h  = 5.8;
boss_d          = 7.2;  // Leaves 1.5 mm min wall around insert bore

// Preserve a guaranteed clear internal prism of at least 50 x 40 x 30 mm
boss_x = 25.0 + boss_d / 2;
boss_y = 20.0 + boss_d / 2;

module screw_pattern() {
    for (sx = [-1, 1], sy = [-1, 1]) {
        translate([sx * boss_x, sy * boss_y, 0]) children();
    }
}

module base_part() {
    difference() {
        union() {
            difference() {
                translate([-base_x / 2, -base_y / 2, 0])
                    cube([base_x, base_y, base_h]);

                translate([-cavity_x / 2, -cavity_y / 2, floor_t])
                    cube([cavity_x, cavity_y, cavity_z + eps]);
            }

            screw_pattern()
                translate([0, 0, floor_t])
                    cylinder(h = cavity_z, d = boss_d);
        }

        screw_pattern() {
            translate([0, 0, base_h - insert_total_h - eps])
                cylinder(h = insert_total_h - insert_lead_h + eps, d = insert_bore_d);

            translate([0, 0, base_h - insert_lead_h - eps])
                cylinder(h = insert_lead_h + 2 * eps, d1 = insert_bore_d, d2 = insert_lead_d);
        }
    }
}

module lid_part() {
    translate([0, 0, base_h + assembly_gap])
        difference() {
            translate([-base_x / 2, -base_y / 2, 0])
                cube([base_x, base_y, lid_t]);

            screw_pattern()
                translate([0, 0, -eps])
                    cylinder(h = lid_t + 2 * eps, d = screw_clear_d);
        }
}

base_part();
lid_part();