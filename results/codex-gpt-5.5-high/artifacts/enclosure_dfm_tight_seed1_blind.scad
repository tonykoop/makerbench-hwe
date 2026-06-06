// Two-part 3D-printable enclosure, units mm
// Internal cavity: 50 x 40 x 30 mm minimum
// Nominal wall: 2.0 mm, minimum modeled wall: >= 1.5 mm
// M3 lid clearance holes: 3.4 mm dia
// M3 heat-set insert bores in base: 4.2 mm dia x 6.0 mm deep
// Fastener axes are shared by lid holes and base insert bores.

$fn = 72;

cavity_x = 50;
cavity_y = 40;
cavity_z = 30;

wall = 2.0;
floor_t = 2.0;
lid_t = 2.0;
lid_gap = 0.18;

base_x = 64;
base_y = 54;
base_h = floor_t + cavity_z;

lid_x = base_x;
lid_y = base_y;

corner_r = 4.0;
cavity_r = 2.0;

boss_od = 8.0;
boss_r = boss_od / 2;
boss_h = base_h;
boss_axis_inset = 6.0;

m3_clear_d = 3.4;
insert_bore_d = 4.2;
insert_bore_depth = 6.0;
insert_floor_keep = 2.0;

boss_light_d = 2.6;

lid_rib_t = 1.6;
lid_rib_h = 1.8;
lid_rib_inset = 9.5;

base_z0 = 0;
lid_z0 = base_h + lid_gap;

screw_axes = [
    [-base_x/2 + boss_axis_inset, -base_y/2 + boss_axis_inset],
    [ base_x/2 - boss_axis_inset, -base_y/2 + boss_axis_inset],
    [ base_x/2 - boss_axis_inset,  base_y/2 - boss_axis_inset],
    [-base_x/2 + boss_axis_inset,  base_y/2 - boss_axis_inset]
];

module rounded_rect_2d(x, y, r) {
    hull() {
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * (x/2 - r), sy * (y/2 - r)])
                circle(r = r);
    }
}

module rounded_box(x, y, z, r) {
    linear_extrude(height = z)
        rounded_rect_2d(x, y, r);
}

module screw_axis_cyl(d, h, zc) {
    translate([0, 0, zc])
        cylinder(d = d, h = h, center = true);
}

module base_shell_positive() {
    union() {
        rounded_box(base_x, base_y, base_h, corner_r);

        for (p = screw_axes)
            translate([p[0], p[1], 0])
                cylinder(r = boss_r, h = boss_h);
    }
}

module base_cutouts() {
    translate([0, 0, floor_t])
        linear_extrude(height = cavity_z + 0.4)
            rounded_rect_2d(cavity_x, cavity_y, cavity_r);

    translate([0, 0, floor_t + 5])
        linear_extrude(height = cavity_z)
            rounded_rect_2d(cavity_x + 8, cavity_y + 3.0, cavity_r);

    translate([0, 0, floor_t + 5])
        linear_extrude(height = cavity_z)
            rounded_rect_2d(cavity_x + 3.0, cavity_y + 8, cavity_r);

    for (p = screw_axes) {
        translate([p[0], p[1], base_h - insert_bore_depth])
            cylinder(d = insert_bore_d, h = insert_bore_depth + 0.3);

        translate([p[0], p[1], floor_t + insert_floor_keep])
            cylinder(d = boss_light_d, h = base_h - insert_bore_depth - floor_t - insert_floor_keep + 0.1);
    }

    for (sx = [-1, 1]) {
        translate([sx * (base_x/2 - wall/2), 0, floor_t + cavity_z/2])
            cube([0.5, cavity_y - 8, cavity_z - 8], center = true);
    }

    for (sy = [-1, 1]) {
        translate([0, sy * (base_y/2 - wall/2), floor_t + cavity_z/2])
            cube([cavity_x - 8, 0.5, cavity_z - 8], center = true);
    }
}

module base() {
    difference() {
        base_shell_positive();
        base_cutouts();
    }
}

module lid_positive() {
    union() {
        translate([0, 0, lid_z0])
            rounded_box(lid_x, lid_y, lid_t, corner_r);

        translate([0,  lid_rib_inset, lid_z0 - lid_rib_h])
            cube([cavity_x - 4, lid_rib_t, lid_rib_h], center = false);
        translate([-(cavity_x - 4)/2,  lid_rib_inset - lid_rib_t/2, lid_z0 - lid_rib_h])
            cube([cavity_x - 4, lid_rib_t, lid_rib_h]);

        translate([-(cavity_x - 4)/2, -lid_rib_inset - lid_rib_t/2, lid_z0 - lid_rib_h])
            cube([cavity_x - 4, lid_rib_t, lid_rib_h]);

        translate([ lid_rib_inset - lid_rib_t/2, -(cavity_y - 4)/2, lid_z0 - lid_rib_h])
            cube([lid_rib_t, cavity_y - 4, lid_rib_h]);

        translate([-lid_rib_inset - lid_rib_t/2, -(cavity_y - 4)/2, lid_z0 - lid_rib_h])
            cube([lid_rib_t, cavity_y - 4, lid_rib_h]);

        for (p = screw_axes)
            translate([p[0], p[1], lid_z0 - 1.0])
                cylinder(d = boss_od, h = lid_t + 1.0);
    }
}

module lid_cutouts() {
    for (p = screw_axes)
        translate([p[0], p[1], lid_z0 - 0.6])
            cylinder(d = m3_clear_d, h = lid_t + 2.2);

    translate([0, 0, lid_z0 - 0.1])
        linear_extrude(height = lid_t + 0.3)
            rounded_rect_2d(cavity_x - 10, cavity_y - 10, 2.0);
}

module lid() {
    difference() {
        lid_positive();
        lid_cutouts();
    }
}

color("lightgray") base();
color("steelblue") lid();