$fn = 72;

wall = 3.0;
inner_x = 56;
inner_y = 56;
inner_z = 33;

outer_x = inner_x + 2 * wall;
outer_y = inner_y + 2 * wall;
base_h = wall + inner_z;
lid_t = 3.0;

screw_clearance_d = 3.4;
insert_bore_d = 4.8;
insert_bore_depth = 6.2;

boss_d = 9.5;
boss_r = boss_d / 2;
boss_h = 11.0;
boss_axis_offset = wall + boss_r + 1.0;

corner_axes = [
    [ boss_axis_offset,  boss_axis_offset],
    [outer_x - boss_axis_offset,  boss_axis_offset],
    [outer_x - boss_axis_offset, outer_y - boss_axis_offset],
    [ boss_axis_offset, outer_y - boss_axis_offset]
];

module hole_at_axes(d, h, z0) {
    for (p = corner_axes)
        translate([p[0], p[1], z0])
            cylinder(d = d, h = h);
}

module corner_bosses() {
    for (p = corner_axes)
        translate([p[0], p[1], wall])
            cylinder(d = boss_d, h = boss_h);
}

module base_shell() {
    difference() {
        cube([outer_x, outer_y, base_h]);
        translate([wall, wall, wall])
            cube([inner_x, inner_y, inner_z + 0.2]);
    }
}

module base() {
    difference() {
        union() {
            base_shell();
            corner_bosses();
        }

        hole_at_axes(insert_bore_d, insert_bore_depth + 0.2, wall + boss_h - insert_bore_depth);
    }
}

module lid() {
    difference() {
        translate([0, 0, base_h])
            cube([outer_x, outer_y, lid_t]);

        hole_at_axes(screw_clearance_d, lid_t + 0.4, base_h - 0.2);
    }
}

base();
lid();