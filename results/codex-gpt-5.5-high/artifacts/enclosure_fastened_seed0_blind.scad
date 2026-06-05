// BOM:
// 4x MB-SHCS-M3-08 socket-head cap screw, M3 x 8 mm, normal clearance hole 3.4 mm
// 4x MB-HSI-M3 brass heat-set insert, M3, 4.0 mm long, recommended boss hole 4.0 mm

$fn = 72;
eps = 0.05;

// Off-the-shelf hardware dimensions from catalog.
screw_clearance_d = 3.4;      // MB-SHCS-M3-08 normal clearance
screw_head_d = 5.5;
screw_head_h = 3.0;
insert_hole_d = 4.0;          // MB-HSI-M3 recommended printed boss hole
insert_len = 4.0;
insert_min_boss_wall = 1.5;

// Enclosure geometry.
wall = 2.5;
bottom_thk = 2.5;
lid_thk = 2.5;

outer_x = 105;
outer_y = 105;
base_h = 25.0;

inner_x = outer_x - 2 * wall;
inner_y = outer_y - 2 * wall;

required_clear_x = 70;
required_clear_y = 70;
required_clear_z = 20;
actual_clear_z = base_h - bottom_thk;

boss_od = 11.0;
boss_r = boss_od / 2;
boss_center_offset = 42.0;
boss_hole_depth = 8.0;

rib_w = 3.0;
rib_overlap = 0.3;

screw_positions = [
    [ boss_center_offset,  boss_center_offset],
    [-boss_center_offset,  boss_center_offset],
    [-boss_center_offset, -boss_center_offset],
    [ boss_center_offset, -boss_center_offset]
];

assert(inner_x >= required_clear_x);
assert(inner_y >= required_clear_y);
assert(actual_clear_z >= required_clear_z);
assert((boss_od - insert_hole_d) / 2 >= insert_min_boss_wall);
assert(boss_center_offset - boss_r >= required_clear_x / 2);
assert(boss_center_offset - boss_r >= required_clear_y / 2);

echo("BOM: 4x MB-SHCS-M3-08 socket-head cap screw; 4x MB-HSI-M3 brass heat-set insert");
echo(str("Central unobstructed cavity: ", required_clear_x, " x ", required_clear_y, " x ", actual_clear_z, " mm minimum"));
echo(str("Wall thickness: ", wall, " mm; lid thickness: ", lid_thk, " mm; base bottom thickness: ", bottom_thk, " mm"));
echo(str("M3 lid clearance holes: ", screw_clearance_d, " mm; insert boss holes: ", insert_hole_d, " mm; boss OD: ", boss_od, " mm"));

module base_shell() {
    difference() {
        translate([-outer_x / 2, -outer_y / 2, 0])
            cube([outer_x, outer_y, base_h]);

        translate([-inner_x / 2, -inner_y / 2, bottom_thk])
            cube([inner_x, inner_y, base_h - bottom_thk + eps]);
    }
}

module boss_and_ribs(x, y) {
    x_min = x > 0 ? x - boss_r : -inner_x / 2 - rib_overlap;
    x_max = x > 0 ? inner_x / 2 + rib_overlap : x + boss_r;
    y_min = y > 0 ? y - boss_r : -inner_y / 2 - rib_overlap;
    y_max = y > 0 ? inner_y / 2 + rib_overlap : y + boss_r;

    translate([x, y, 0])
        cylinder(h = base_h, d = boss_od);

    translate([(x_min + x_max) / 2, y, base_h / 2])
        cube([x_max - x_min, rib_w, base_h], center = true);

    translate([x, (y_min + y_max) / 2, base_h / 2])
        cube([rib_w, y_max - y_min, base_h], center = true);
}

module insert_blind_hole(x, y) {
    translate([x, y, base_h - boss_hole_depth])
        cylinder(h = boss_hole_depth + eps, d = insert_hole_d);
}

module base() {
    difference() {
        union() {
            base_shell();

            for (p = screw_positions)
                boss_and_ribs(p[0], p[1]);
        }

        for (p = screw_positions)
            insert_blind_hole(p[0], p[1]);
    }
}

module lid() {
    difference() {
        translate([-outer_x / 2, -outer_y / 2, base_h])
            cube([outer_x, outer_y, lid_thk]);

        for (p = screw_positions)
            translate([p[0], p[1], base_h - eps])
                cylinder(h = lid_thk + 2 * eps, d = screw_clearance_d);
    }
}

color([0.82, 0.84, 0.86])
    base();

color([0.18, 0.48, 0.80, 0.85])
    lid();