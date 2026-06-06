$fn = 72;

// Units: mm

wall = 2.5;
min_wall = 1.5;
clearance = 0.30;

cavity_x = 42;
cavity_y = 42;
cavity_z = 22;

base_h = wall + cavity_z;
lid_h = 4.0;

outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;

screw_axis_x = 19.5;
screw_axis_y = 19.5;

m3_clearance_d = 3.4;
insert_bore_d = 4.6;
insert_bore_depth = 6.2;

boss_od = 8.0;
lid_counterbore_d = 6.2;
lid_counterbore_depth = 1.8;

vent_slot_w = 4.0;
vent_slot_l = 24.0;

// Mass check note:
// Bounding solid block = outer_x * outer_y * (base_h + clearance + lid_h)
// Design is a thin shell with localized bosses and lightened lid, well below 45%.

module rounded_box(size, r) {
    hull() {
        for (x = [-size[0]/2 + r, size[0]/2 - r])
        for (y = [-size[1]/2 + r, size[1]/2 - r])
            translate([x, y, 0])
                cylinder(h = size[2], r = r);
    }
}

module screw_axes() {
    for (x = [-screw_axis_x, screw_axis_x])
    for (y = [-screw_axis_y, screw_axis_y])
        translate([x, y, 0])
            children();
}

module base_shell() {
    difference() {
        rounded_box([outer_x, outer_y, base_h], 3.0);

        translate([0, 0, wall])
            rounded_box([cavity_x, cavity_y, cavity_z + 0.2], 1.2);

        screw_axes()
            translate([0, 0, base_h - insert_bore_depth])
                cylinder(h = insert_bore_depth + 0.3, d = insert_bore_d);
    }
}

module base_bosses() {
    difference() {
        screw_axes()
            cylinder(h = base_h, d = boss_od);

        screw_axes()
            translate([0, 0, base_h - insert_bore_depth])
                cylinder(h = insert_bore_depth + 0.3, d = insert_bore_d);
    }
}

module base_lightening_windows() {
    for (x = [-1, 1])
        translate([x * (outer_x/2 - wall/2), 0, wall + 5])
            cube([wall + 0.2, 18, 8], center = true);

    for (y = [-1, 1])
        translate([0, y * (outer_y/2 - wall/2), wall + 5])
            cube([18, wall + 0.2, 8], center = true);
}

module base() {
    color([0.20, 0.48, 0.78])
    difference() {
        union() {
            base_shell();
            base_bosses();
        }
        base_lightening_windows();
    }
}

module lid_plate() {
    difference() {
        rounded_box([outer_x, outer_y, lid_h], 3.0);

        screw_axes()
            translate([0, 0, -0.1])
                cylinder(h = lid_h + 0.2, d = m3_clearance_d);

        screw_axes()
            translate([0, 0, lid_h - lid_counterbore_depth])
                cylinder(h = lid_counterbore_depth + 0.2, d = lid_counterbore_d);

        for (x = [-10, 0, 10])
            translate([x, 0, -0.1])
                rounded_box([vent_slot_w, vent_slot_l, lid_h + 0.2], 1.5);
    }
}

module lid_ribs() {
    rib_h = 2.0;
    rib_w = 1.6;

    translate([0, -cavity_y/2 + 7, 0])
        cube([cavity_x - 8, rib_w, rib_h], center = true);

    translate([0, cavity_y/2 - 7, 0])
        cube([cavity_x - 8, rib_w, rib_h], center = true);

    translate([-cavity_x/2 + 7, 0, 0])
        cube([rib_w, cavity_y - 8, rib_h], center = true);

    translate([cavity_x/2 - 7, 0, 0])
        cube([rib_w, cavity_y - 8, rib_h], center = true);
}

module lid_boss_lands() {
    difference() {
        screw_axes()
            cylinder(h = lid_h, d = 7.0);

        screw_axes()
            translate([0, 0, -0.1])
                cylinder(h = lid_h + 0.2, d = m3_clearance_d);

        screw_axes()
            translate([0, 0, lid_h - lid_counterbore_depth])
                cylinder(h = lid_counterbore_depth + 0.2, d = lid_counterbore_d);
    }
}

module lid() {
    color([0.88, 0.62, 0.18])
    translate([0, 0, base_h + clearance])
        union() {
            lid_plate();

            translate([0, 0, 0.05])
                lid_boss_lands();

            translate([0, 0, 0.95])
                lid_ribs();
        }
}

base();
lid();