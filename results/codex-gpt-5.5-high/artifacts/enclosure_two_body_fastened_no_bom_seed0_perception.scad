// Units: mm
$fn = 72;

cavity_x = 70;
cavity_y = 70;
cavity_z = 20;

wall = 2.5;
floor_thickness = 2.5;
lid_thickness = 3.0;

boss_od = 9.0;
insert_bore_d = 4.6;      // typical pilot bore for M3 heat-set inserts
insert_bore_depth = 7.0;
m3_clearance_d = 3.4;     // normal M3 clearance hole

outer_x = cavity_x + 2 * wall + 2 * boss_od;
outer_y = cavity_y + 2 * wall + 2 * boss_od;
base_h = floor_thickness + cavity_z;
lid_z = base_h;

screw_margin = wall + boss_od / 2;
screw_positions = [
    [ screw_margin, screw_margin ],
    [ outer_x - screw_margin, screw_margin ],
    [ outer_x - screw_margin, outer_y - screw_margin ],
    [ screw_margin, outer_y - screw_margin ]
];

module screw_axis_holes(d, h, z0) {
    for (p = screw_positions) {
        translate([p[0], p[1], z0])
            cylinder(d = d, h = h);
    }
}

module base_shell() {
    difference() {
        union() {
            cube([outer_x, outer_y, base_h]);

            for (p = screw_positions) {
                translate([p[0], p[1], floor_thickness])
                    cylinder(d = boss_od, h = cavity_z);
            }
        }

        translate([
            (outer_x - cavity_x) / 2,
            (outer_y - cavity_y) / 2,
            floor_thickness
        ])
            cube([cavity_x, cavity_y, cavity_z + 0.02]);

        screw_axis_holes(
            insert_bore_d,
            insert_bore_depth + 0.02,
            base_h - insert_bore_depth
        );
    }
}

module lid() {
    difference() {
        cube([outer_x, outer_y, lid_thickness]);
        screw_axis_holes(m3_clearance_d, lid_thickness + 0.04, -0.02);
    }
}

base_shell();

translate([0, 0, lid_z])
    lid();