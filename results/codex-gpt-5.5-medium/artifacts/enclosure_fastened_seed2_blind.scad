/*
BOM:
- 4x MB-SHCS-M3-08 socket head cap screw, M3 x 8 mm, alloy steel
- 4x MB-HSI-M3 heat-set insert, M3, brass, 4.0 mm length, 4.0 mm recommended boss hole

Design notes:
- Internal unobstructed central cavity: 44 x 44 x 22 mm minimum.
- Wall thickness: 2.5 mm.
- Lid clearance holes: 3.4 mm normal M3 clearance.
- Lid counterbores: 5.8 mm diameter x 3.2 mm deep for 5.5 mm socket heads.
- Insert boss holes: 4.0 mm diameter x 4.6 mm deep for MB-HSI-M3.
*/

$fn = 72;
eps = 0.02;

// Catalog-selected hardware
screw_clearance_d = 3.4;
screw_head_d = 5.5;
screw_head_clearance_d = 5.8;
screw_head_h = 3.0;
insert_boss_hole_d = 4.0;
insert_len = 4.0;

// Enclosure dimensions
wall = 2.5;
floor_t = 2.5;
lid_t = 3.2;
internal_x = 64;
internal_y = 64;
internal_z = 22;
outer_x = internal_x + 2 * wall;
outer_y = internal_y + 2 * wall;
base_h = floor_t + internal_z;

// Lid registration lip
lip_clearance = 0.35;
lip_wall = 1.4;
lip_h = 2.0;
lip_outer_x = internal_x - 2 * lip_clearance;
lip_outer_y = internal_y - 2 * lip_clearance;
lip_inner_x = lip_outer_x - 2 * lip_wall;
lip_inner_y = lip_outer_y - 2 * lip_wall;

// Fastener layout
boss_od = 9.0;
boss_r = boss_od / 2;
boss_h = internal_z;
boss_xy = 25;

// Small printability details
corner_r = 1.2;
lid_corner_r = 1.2;

module rounded_box(size=[10,10,10], r=1) {
    x = size[0];
    y = size[1];
    z = size[2];
    linear_extrude(height=z)
        offset(r=r)
            square([x - 2*r, y - 2*r], center=true);
}

module screw_positions() {
    for (x = [-boss_xy, boss_xy])
        for (y = [-boss_xy, boss_xy])
            translate([x, y, 0])
                children();
}

module base_shell_positive() {
    rounded_box([outer_x, outer_y, base_h], corner_r);
}

module base_cavity_cut() {
    translate([0, 0, floor_t])
        rounded_box([internal_x, internal_y, internal_z + eps], corner_r);
}

module insert_bosses_positive() {
    screw_positions()
        translate([0, 0, floor_t])
            cylinder(d=boss_od, h=boss_h);
}

module insert_boss_holes_cut() {
    screw_positions()
        translate([0, 0, base_h - insert_len - 0.6])
            cylinder(d=insert_boss_hole_d, h=insert_len + 0.8);
}

module base() {
    difference() {
        union() {
            base_shell_positive();
            insert_bosses_positive();
        }
        base_cavity_cut();
        insert_boss_holes_cut();
    }
}

module lid_plate_positive() {
    translate([0, 0, base_h])
        rounded_box([outer_x, outer_y, lid_t], lid_corner_r);
}

module lid_lip_positive() {
    translate([0, 0, base_h - lip_h])
        difference() {
            rounded_box([lip_outer_x, lip_outer_y, lip_h], 0.8);
            translate([0, 0, -eps])
                rounded_box([lip_inner_x, lip_inner_y, lip_h + 2*eps], 0.4);
        }
}

module lid_screw_cuts() {
    screw_positions() {
        translate([0, 0, base_h - eps])
            cylinder(d=screw_clearance_d, h=lid_t + 2*eps);
        translate([0, 0, base_h + lid_t - screw_head_h - 0.2])
            cylinder(d=screw_head_clearance_d, h=screw_head_h + 0.4);
    }
}

module lid() {
    difference() {
        union() {
            lid_plate_positive();
            lid_lip_positive();
        }
        lid_screw_cuts();
    }
}

color("lightgray") base();
color("steelblue") lid();