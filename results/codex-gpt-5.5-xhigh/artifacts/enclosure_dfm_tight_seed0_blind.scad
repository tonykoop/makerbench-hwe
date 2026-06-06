$fn = 72;

// Units: mm
// Internal cavity target: >=70 x 70 x 20
// Main wall thickness: 2.5
// Minimum rib/web thickness used: 1.5
// M3 lid clearance: 3.4
// M3 heat-set insert bore: 4.6, blind bore depth 5.8

cavity_x = 70;
cavity_y = 70;
cavity_z = 20;

wall = 2.5;
floor_th = 2.5;
lid_th = 2.5;
lid_rim_h = 2.0;
lid_rim_wall = 1.5;
fit_clearance = 0.35;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;
base_h = floor_th + cavity_z;
lid_z = base_h;

corner_r = 4;
boss_od = 8.5;
boss_wall = 1.95;
insert_bore_d = 4.6;
insert_bore_depth = 5.8;
m3_clearance_d = 3.4;
screw_head_d = 6.4;
screw_head_depth = 1.4;

boss_margin = 8.5;
boss_x = outer_x / 2 - boss_margin;
boss_y = outer_y / 2 - boss_margin;

function clamp(v, lo, hi) = min(max(v, lo), hi);

module rounded_box(size=[10,10,10], r=2) {
    hull() {
        for (x = [-size[0]/2 + r, size[0]/2 - r])
        for (y = [-size[1]/2 + r, size[1]/2 - r])
            translate([x, y, 0])
                cylinder(h=size[2], r=r);
    }
}

module hole_at_fasteners(d, h, z0=-0.01) {
    for (x = [-boss_x, boss_x])
    for (y = [-boss_y, boss_y])
        translate([x, y, z0])
            cylinder(d=d, h=h);
}

module base_bosses() {
    for (x = [-boss_x, boss_x])
    for (y = [-boss_y, boss_y])
        translate([x, y, floor_th])
            cylinder(d=boss_od, h=cavity_z);
}

module base_lightening_windows() {
    for (x = [-outer_x/2 - 0.01, outer_x/2 - 1.5])
        translate([x, 0, floor_th + 7.5])
            cube([1.52, 42, 11], center=true);

    for (y = [-outer_y/2 - 0.01, outer_y/2 - 1.5])
        translate([0, y, floor_th + 7.5])
            cube([42, 1.52, 11], center=true);
}

module base() {
    difference() {
        union() {
            difference() {
                rounded_box([outer_x, outer_y, base_h], corner_r);
                translate([0, 0, floor_th])
                    rounded_box([cavity_x, cavity_y, cavity_z + 0.02], clamp(corner_r - wall, 0.8, 3));
            }

            base_bosses();

            translate([0, -outer_y/2 + wall/2, floor_th + cavity_z/2])
                cube([42, wall, cavity_z], center=true);
            translate([0, outer_y/2 - wall/2, floor_th + cavity_z/2])
                cube([42, wall, cavity_z], center=true);
            translate([-outer_x/2 + wall/2, 0, floor_th + cavity_z/2])
                cube([wall, 42, cavity_z], center=true);
            translate([outer_x/2 - wall/2, 0, floor_th + cavity_z/2])
                cube([wall, 42, cavity_z], center=true);
        }

        translate([0, 0, base_h - insert_bore_depth])
            hole_at_fasteners(insert_bore_d, insert_bore_depth + 0.02);

        base_lightening_windows();
    }
}

module lid_rim() {
    rim_outer_x = cavity_x - 2 * fit_clearance;
    rim_outer_y = cavity_y - 2 * fit_clearance;
    rim_inner_x = rim_outer_x - 2 * lid_rim_wall;
    rim_inner_y = rim_outer_y - 2 * lid_rim_wall;

    difference() {
        translate([0, 0, lid_z - lid_rim_h])
            rounded_box([rim_outer_x, rim_outer_y, lid_rim_h], 2.0);
        translate([0, 0, lid_z - lid_rim_h - 0.01])
            rounded_box([rim_inner_x, rim_inner_y, lid_rim_h + 0.03], 1.0);
    }
}

module lid_stiffening_ribs() {
    rib_t = 1.5;
    rib_h = 1.8;

    translate([0, 0, lid_z - rib_h])
        cube([46, rib_t, rib_h], center=true);
    translate([0, 0, lid_z - rib_h])
        cube([rib_t, 46, rib_h], center=true);

    for (x = [-boss_x, boss_x])
    for (y = [-boss_y, boss_y])
        translate([x, y, lid_z - rib_h])
            cylinder(d=7.2, h=rib_h);
}

module lid() {
    difference() {
        union() {
            translate([0, 0, lid_z])
                rounded_box([outer_x, outer_y, lid_th], corner_r);
            lid_rim();
            lid_stiffening_ribs();
        }

        translate([0, 0, lid_z - 0.01])
            hole_at_fasteners(m3_clearance_d, lid_th + 0.02);

        translate([0, 0, lid_z + lid_th - screw_head_depth])
            hole_at_fasteners(screw_head_d, screw_head_depth + 0.02);

        translate([0, 0, lid_z - 0.01])
            rounded_box([44, 44, lid_th + 0.02], 3.0);
    }
}

color("lightgray") base();
color("steelblue") lid();