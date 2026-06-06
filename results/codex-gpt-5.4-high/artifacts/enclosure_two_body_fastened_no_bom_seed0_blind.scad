$fn = 72;

// Two-part enclosure with a 70 x 70 x 20 mm internal cavity,
// 2.5 mm walls/floor, M3 clearance holes in the lid, and
// matching blind heat-set insert bores in the base.

eps = 0.01;

// Required enclosure dimensions
cavity_x = 70;
cavity_y = 70;
cavity_z = 20;

wall_t  = 2.5;
floor_t = 2.5;
lid_t   = 4.0;

// Fastener geometry
m3_clear_d         = 3.4;  // printed clearance for M3 screw shank
m3_shcs_head_d     = 6.2;  // clearance for M3 socket-head cap screw head
m3_shcs_head_depth = 3.2;  // counterbore depth in lid

insert_bore_d      = 4.6;  // generic M3 heat-set insert bore
insert_bore_depth  = 5.2;  // blind bore depth from top of base
insert_lead_d      = 5.2;  // lead-in chamfer diameter at top
insert_lead_h      = 1.0;

// Overall base body
outer_x = cavity_x + 2 * wall_t;
outer_y = cavity_y + 2 * wall_t;
base_h  = floor_t + cavity_z;

// Corner bosses / ears
boss_d = 12.0;
boss_r = boss_d / 2;

// Set so each boss overlaps the rectangular shell by 3.5 mm on each axis
screw_offset_x = outer_x / 2 + boss_r - 3.5;
screw_offset_y = outer_y / 2 + boss_r - 3.5;

// Small display gap so lid and base are separate, non-interfering solids
display_gap = 0.5;

module screw_pattern() {
    for (sx = [-1, 1], sy = [-1, 1]) {
        translate([sx * screw_offset_x, sy * screw_offset_y, 0]) children();
    }
}

module base_blank() {
    union() {
        translate([-outer_x / 2, -outer_y / 2, 0])
            cube([outer_x, outer_y, base_h]);

        screw_pattern()
            cylinder(h = base_h, d = boss_d);
    }
}

module lid_blank() {
    union() {
        translate([-outer_x / 2, -outer_y / 2, 0])
            cube([outer_x, outer_y, lid_t]);

        screw_pattern()
            cylinder(h = lid_t, d = boss_d);
    }
}

module base_part() {
    difference() {
        base_blank();

        // Main internal cavity: 70 x 70 x 20 mm
        translate([-cavity_x / 2, -cavity_y / 2, floor_t])
            cube([cavity_x, cavity_y, cavity_z + eps]);

        // Blind insert bores from the top face of the base
        screw_pattern() {
            translate([0, 0, base_h - insert_bore_depth])
                cylinder(h = insert_bore_depth + eps, d = insert_bore_d);

            translate([0, 0, base_h - insert_lead_h])
                cylinder(h = insert_lead_h + eps, d1 = insert_lead_d, d2 = insert_bore_d);
        }
    }
}

module lid_part() {
    difference() {
        lid_blank();

        // Through clearance holes for M3 screws
        screw_pattern()
            translate([0, 0, -eps])
                cylinder(h = lid_t + 2 * eps, d = m3_clear_d);

        // Counterbores for M3 socket-head cap screws
        screw_pattern()
            translate([0, 0, lid_t - m3_shcs_head_depth])
                cylinder(h = m3_shcs_head_depth + eps, d = m3_shcs_head_d);
    }
}

// Render both parts aligned in assembled XY position, separated slightly in Z
base_part();
translate([0, 0, base_h + display_gap]) lid_part();