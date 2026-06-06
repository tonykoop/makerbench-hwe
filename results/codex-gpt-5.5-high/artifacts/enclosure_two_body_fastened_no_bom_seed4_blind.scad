$fn = 96;

// Units: mm
wall = 3.0;

cavity_x = 62;
cavity_y = 72;
cavity_z = 22;

base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_h = cavity_z + wall;

lid_th = 4.0;

corner_offset = 9.0;
screw_clearance_d = 3.4;     // M3 normal clearance
screw_head_clearance_d = 6.2; // M3 socket-head cap screw head clearance/counterbore
screw_head_depth = 3.2;

insert_bore_d = 4.6;         // common pilot bore for M3 heat-set inserts
insert_bore_depth = 7.0;
boss_d = 9.0;

eps = 0.02;

hole_x = base_outer_x / 2 - corner_offset;
hole_y = base_outer_y / 2 - corner_offset;
hole_positions = [
    [ hole_x,  hole_y],
    [-hole_x,  hole_y],
    [-hole_x, -hole_y],
    [ hole_x, -hole_y]
];

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    linear_extrude(height = z)
        offset(r = r)
            square([x - 2 * r, y - 2 * r], center = true);
}

module base_shell() {
    difference() {
        rounded_box([base_outer_x, base_outer_y, base_h], 4);

        translate([0, 0, wall])
            rounded_box([cavity_x, cavity_y, cavity_z + eps], 2);
    }
}

module insert_bosses() {
    for (p = hole_positions)
        translate([p[0], p[1], wall])
            cylinder(d = boss_d, h = cavity_z);
}

module base_insert_bores() {
    for (p = hole_positions)
        translate([p[0], p[1], base_h - insert_bore_depth + eps])
            cylinder(d = insert_bore_d, h = insert_bore_depth + 2 * eps);
}

module base() {
    difference() {
        union() {
            base_shell();
            insert_bosses();
        }

        base_insert_bores();
    }
}

module lid() {
    translate([0, 0, base_h])
        difference() {
            rounded_box([base_outer_x, base_outer_y, lid_th], 4);

            for (p = hole_positions) {
                translate([p[0], p[1], -eps])
                    cylinder(d = screw_clearance_d, h = lid_th + 2 * eps);

                translate([p[0], p[1], lid_th - screw_head_depth])
                    cylinder(d = screw_head_clearance_d, h = screw_head_depth + eps);
            }
        }
}

base();
lid();