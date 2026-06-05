// BOM:
// 4x MB-SHCS-M3-06 socket_head_cap_screw
// 4x MB-HSI-M3 heat_set_insert

$fn = 64;

// ---------- Catalog-selected hardware ----------
screw_part_number = "MB-SHCS-M3-06";
insert_part_number = "MB-HSI-M3";

screw_clearance_d = 3.4;      // MB-SHCS-M3-06 normal clearance
screw_head_d = 5.5;
screw_head_h = 3.0;
screw_length = 6.0;

insert_length = 4.0;          // MB-HSI-M3
insert_outer_d = 4.6;
insert_boss_hole_d = 4.0;
insert_min_wall = 1.5;

// ---------- Enclosure requirements ----------
cavity_x = 70;
cavity_y = 70;
cavity_z = 20;
wall = 2.5;

// ---------- Derived enclosure geometry ----------
floor_t = wall;
base_h = floor_t + cavity_z;   // preserves full 20 mm internal height
base_outer_x = 92;
base_outer_y = 92;

boss_outer_d = 8.0;            // 4.0 hole + 2.0 mm wall/side; exceeds insert min wall
boss_r = boss_outer_d / 2;

screw_offset_x = 40;
screw_offset_y = 40;

// Lid wraps around the exterior so the full internal cavity remains unobstructed.
lid_overhang = 3.0;
lid_outer_x = base_outer_x + 2 * lid_overhang;
lid_outer_y = base_outer_y + 2 * lid_overhang;
lid_total_h = 6.0;
lid_skirt_depth = 2.5;
lid_fit_clearance = 0.35;      // radial fit clearance for FDM assembly

head_counterbore_d = 6.0;      // modest clearance around 5.5 mm head
head_counterbore_depth = 1.5;  // partial recess; leaves 2.0 mm clamp thickness with 6 mm screw

insert_hole_depth = 4.8;       // 4.0 mm insert + bottom relief
insert_leadin_depth = 0.8;
insert_leadin_d = 4.8;

display_gap = 0.2;             // keeps rendered solids non-interfering while aligned in assembled position
eps = 0.01;

assert(boss_outer_d >= insert_outer_d + 2 * insert_min_wall, "Boss OD is too small for the selected insert.");
assert(screw_length >= (lid_total_h - lid_skirt_depth), "Selected screw is too short for lid clamp thickness.");

echo(str("BOM: 4x ", screw_part_number, ", 4x ", insert_part_number));
echo(str("Internal cavity: ", cavity_x, " x ", cavity_y, " x ", cavity_z, " mm"));

module screw_points() {
    for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * screw_offset_x, sy * screw_offset_y, 0])
            children();
}

module base_part() {
    difference() {
        translate([-base_outer_x / 2, -base_outer_y / 2, 0])
            cube([base_outer_x, base_outer_y, base_h]);

        // Main internal cavity: exactly 70 x 70 x 20 mm clear volume.
        translate([-cavity_x / 2, -cavity_y / 2, floor_t])
            cube([cavity_x, cavity_y, cavity_z + 2 * eps]);

        // Insert bores with a shallow lead-in for easier heat-set placement.
        screw_points() {
            translate([0, 0, base_h - insert_hole_depth])
                cylinder(d = insert_boss_hole_d, h = insert_hole_depth + eps);

            translate([0, 0, base_h - insert_leadin_depth])
                cylinder(d1 = insert_leadin_d, d2 = insert_boss_hole_d, h = insert_leadin_depth + eps);
        }
    }
}

module lid_part() {
    difference() {
        translate([-lid_outer_x / 2, -lid_outer_y / 2, 0])
            cube([lid_outer_x, lid_outer_y, lid_total_h]);

        // Exterior wrap/skirt; does not intrude into the 70 x 70 x 20 internal cavity.
        translate([-(base_outer_x + 2 * lid_fit_clearance) / 2,
                   -(base_outer_y + 2 * lid_fit_clearance) / 2,
                   0])
            cube([base_outer_x + 2 * lid_fit_clearance,
                  base_outer_y + 2 * lid_fit_clearance,
                  lid_skirt_depth + eps]);

        // Through holes and partial head recesses for M3 SHCS.
        screw_points() {
            translate([0, 0, -eps])
                cylinder(d = screw_clearance_d, h = lid_total_h + 2 * eps);

            translate([0, 0, lid_total_h - head_counterbore_depth])
                cylinder(d = head_counterbore_d, h = head_counterbore_depth + eps);
        }
    }
}

// Render as two separate solids, aligned in assembled position with a tiny display gap.
color([0.78, 0.80, 0.84]) base_part();
translate([0, 0, base_h - lid_skirt_depth + display_gap])
    color([0.90, 0.92, 0.96]) lid_part();