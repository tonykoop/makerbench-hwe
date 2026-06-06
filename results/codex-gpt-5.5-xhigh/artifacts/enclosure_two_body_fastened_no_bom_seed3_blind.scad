// Units: mm
$fn = 72;

wall = 3.0;
cavity_x = 54;
cavity_y = 54;
cavity_z = 30;
base_floor = wall;
base_h = base_floor + cavity_z;

lid_th = 5.0;
assembly_gap = 0.20;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;

boss_d = 9.5;
insert_bore_d = 4.6;
insert_bore_depth = 7.0;

m3_clearance_d = 3.4;
m3_head_clearance_d = 6.3;
m3_head_counterbore_depth = 3.2;

fastener_margin = 8.0;
fastener_positions = [
    [ fastener_margin,  fastener_margin],
    [ outer_x - fastener_margin,  fastener_margin],
    [ outer_x - fastener_margin,  outer_y - fastener_margin],
    [ fastener_margin,  outer_y - fastener_margin]
];

module base_shell() {
    difference() {
        cube([outer_x, outer_y, base_h], center = false);

        translate([wall, wall, base_floor])
            cube([cavity_x, cavity_y, cavity_z + 0.05], center = false);
    }
}

module corner_bosses() {
    for (p = fastener_positions) {
        translate([p[0], p[1], base_floor])
            cylinder(d = boss_d, h = cavity_z, center = false);
    }
}

module base() {
    difference() {
        union() {
            base_shell();
            corner_bosses();
        }

        for (p = fastener_positions) {
            translate([p[0], p[1], base_h - insert_bore_depth])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.2, center = false);
        }
    }
}

module lid_blank() {
    cube([outer_x, outer_y, lid_th], center = false);
}

module lid() {
    difference() {
        lid_blank();

        for (p = fastener_positions) {
            translate([p[0], p[1], -0.1])
                cylinder(d = m3_clearance_d, h = lid_th + 0.2, center = false);

            translate([p[0], p[1], lid_th - m3_head_counterbore_depth])
                cylinder(d = m3_head_clearance_d, h = m3_head_counterbore_depth + 0.1, center = false);
        }
    }
}

base();

translate([0, 0, base_h + assembly_gap])
    lid();