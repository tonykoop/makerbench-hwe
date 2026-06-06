$fn = 72;

// Units: mm
wall = 2.0;
internal_x = 54;
internal_y = 44;
internal_z = 30;

base_floor = 2.0;
lid_thickness = 4.0;

outer_x = internal_x + 2 * wall;
outer_y = internal_y + 2 * wall;
base_h = base_floor + internal_z;

corner_boss_d = 9.0;
boss_r = corner_boss_d / 2;
boss_inset = 5.6;

screw_clearance_d = 3.4;      // M3 normal clearance
head_counterbore_d = 6.2;     // M3 socket-head clearance
head_counterbore_depth = 3.2;

insert_bore_d = 4.6;          // typical M3 heat-set insert pilot bore
insert_bore_depth = 6.2;

eps = 0.02;

screw_positions = [
    [ outer_x/2 - boss_inset,  outer_y/2 - boss_inset],
    [-outer_x/2 + boss_inset,  outer_y/2 - boss_inset],
    [ outer_x/2 - boss_inset, -outer_y/2 + boss_inset],
    [-outer_x/2 + boss_inset, -outer_y/2 + boss_inset]
];

module screw_axis_holes_through_lid() {
    for (p = screw_positions) {
        translate([p[0], p[1], -eps])
            cylinder(d = screw_clearance_d, h = lid_thickness + 2*eps);

        translate([p[0], p[1], lid_thickness - head_counterbore_depth])
            cylinder(d = head_counterbore_d, h = head_counterbore_depth + eps);
    }
}

module insert_bores_in_base() {
    for (p = screw_positions) {
        translate([p[0], p[1], base_h - insert_bore_depth])
            cylinder(d = insert_bore_d, h = insert_bore_depth + eps);
    }
}

module base_solid_before_cut() {
    union() {
        cube([outer_x, outer_y, base_h], center = true);

        for (p = screw_positions) {
            translate([p[0], p[1], 0])
                cylinder(d = corner_boss_d, h = base_h, center = true);
        }
    }
}

module base() {
    difference() {
        translate([0, 0, base_h/2])
            base_solid_before_cut();

        translate([0, 0, base_floor + internal_z/2])
            cube([internal_x, internal_y, internal_z + eps], center = true);

        insert_bores_in_base();
    }
}

module lid() {
    difference() {
        translate([0, 0, base_h + lid_thickness/2])
            union() {
                cube([outer_x, outer_y, lid_thickness], center = true);

                for (p = screw_positions) {
                    translate([p[0], p[1], 0])
                        cylinder(d = corner_boss_d, h = lid_thickness, center = true);
                }
            }

        translate([0, 0, base_h])
            screw_axis_holes_through_lid();
    }
}

base();
lid();