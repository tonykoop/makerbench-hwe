$fn = 64;

// Internal cavity requirement
inner_x = 50;
inner_y = 40;
inner_z = 30;

// Enclosure and fastener geometry
wall_t = 2.0;
floor_t = 2.0;
lid_t = 3.0;

m3_clear_d = 3.4;       // printed clearance hole for M3 screw
insert_bore_d = 4.3;    // typical printed bore for M3 heat-set insert
insert_bore_depth = 5.5;

// Layout
base_box_x = inner_x + 2 * wall_t;   // 54
base_box_y = inner_y + 2 * wall_t;   // 44
base_h = floor_t + inner_z;          // 32

lid_x = 72;
lid_y = 62;

screw_edge_margin = 7;
screw_dx = lid_x / 2 - screw_edge_margin;  // 29
screw_dy = lid_y / 2 - screw_edge_margin;  // 24

boss_r = 4.0;   // tangent to cavity envelope, leaves material around insert
view_gap = 0.6; // small exploded gap so both solids are visible and non-interfering

module screw_pattern() {
    for (sx = [-1, 1], sy = [-1, 1]) {
        translate([sx * screw_dx, sy * screw_dy, 0])
            children();
    }
}

module base_part() {
    difference() {
        union() {
            // Main shell
            translate([-base_box_x / 2, -base_box_y / 2, 0])
                cube([base_box_x, base_box_y, base_h]);

            // Corner bosses for heat-set inserts
            screw_pattern()
                cylinder(h = base_h, r = boss_r);
        }

        // Internal cavity: 50 x 40 x 30 mm clear, with 2 mm floor/walls
        translate([-inner_x / 2, -inner_y / 2, floor_t])
            cube([inner_x, inner_y, inner_z + 0.02]);

        // Blind insert bores, aligned with lid clearance holes
        screw_pattern()
            translate([0, 0, base_h - insert_bore_depth])
                cylinder(h = insert_bore_depth + 0.02, d = insert_bore_d);
    }
}

module lid_part() {
    difference() {
        translate([-lid_x / 2, -lid_y / 2, 0])
            cube([lid_x, lid_y, lid_t]);

        // Through clearance holes for M3 screws
        screw_pattern()
            translate([0, 0, -0.01])
                cylinder(h = lid_t + 0.02, d = m3_clear_d);
    }
}

base_part();
translate([0, 0, base_h + view_gap]) lid_part();