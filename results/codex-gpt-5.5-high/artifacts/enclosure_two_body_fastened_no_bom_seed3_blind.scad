$fn = 64;

// Units: mm
wall = 3.0;

inner_x = 56;
inner_y = 56;
cavity_z = 30;

base_h = wall + cavity_z;
lid_th = 6.0;

outer_x = inner_x + 2 * wall;
outer_y = inner_y + 2 * wall;

m3_clearance_d = 3.4;
m3_head_clearance_d = 5.8;
m3_head_counterbore_depth = 3.4;

insert_bore_d = 4.6;
insert_bore_depth = 8.0;

boss_d = 9.0;
boss_r = boss_d / 2;
screw_offset = 8.5;

eps = 0.05;

hole_positions = [
    [ screw_offset,  screw_offset],
    [ outer_x - screw_offset,  screw_offset],
    [ screw_offset,  outer_y - screw_offset],
    [ outer_x - screw_offset,  outer_y - screw_offset]
];

module rounded_rect_2d(w, h, r) {
    hull() {
        translate([r, r]) circle(r = r);
        translate([w - r, r]) circle(r = r);
        translate([r, h - r]) circle(r = r);
        translate([w - r, h - r]) circle(r = r);
    }
}

module screw_hole_lid() {
    translate([0, 0, -eps])
        cylinder(d = m3_clearance_d, h = lid_th + 2 * eps);

    translate([0, 0, lid_th - m3_head_counterbore_depth])
        cylinder(d = m3_head_clearance_d, h = m3_head_counterbore_depth + eps);
}

module insert_bore_base() {
    translate([0, 0, base_h - insert_bore_depth])
        cylinder(d = insert_bore_d, h = insert_bore_depth + eps);
}

module base_shell_positive() {
    difference() {
        linear_extrude(height = base_h)
            rounded_rect_2d(outer_x, outer_y, 4);

        translate([wall, wall, wall])
            cube([inner_x, inner_y, cavity_z + eps]);
    }
}

module base_bosses_positive() {
    for (p = hole_positions) {
        translate([p[0], p[1], wall])
            cylinder(d = boss_d, h = cavity_z);
    }
}

module base() {
    difference() {
        union() {
            base_shell_positive();
            base_bosses_positive();
        }

        for (p = hole_positions) {
            translate([p[0], p[1], 0])
                insert_bore_base();
        }
    }
}

module lid() {
    difference() {
        translate([0, 0, base_h])
            linear_extrude(height = lid_th)
                rounded_rect_2d(outer_x, outer_y, 4);

        for (p = hole_positions) {
            translate([p[0], p[1], base_h])
                screw_hole_lid();
        }
    }
}

base();
lid();