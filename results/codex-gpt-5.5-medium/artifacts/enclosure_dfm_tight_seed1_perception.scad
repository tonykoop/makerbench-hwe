$fn = 64;

// Units: mm
// Two separate assembled solids: base z=0..36, lid z=36..40.
// Internal clear cavity: 54 x 44 x 32 mm between central boss keep-out edges.
// Nominal walls: 2.0 mm, minimum printed web: >= 1.5 mm.
// M3 lid clearance holes and M3 heat-set insert bores share the same axes.

outer_x = 70;
outer_y = 60;
base_h  = 36;
lid_t   = 4;

wall = 2.0;
floor_t = 2.0;

inner_x = outer_x - 2 * wall;
inner_y = outer_y - 2 * wall;
inner_h = base_h - floor_t;

boss_d = 8.0;
boss_r = boss_d / 2;
boss_h = 16.0;

insert_bore_d = 4.7;
insert_bore_depth = 7.0;

m3_clearance_d = 3.4;
screw_head_d = 6.2;
screw_head_depth = 2.2;

axis_x = outer_x / 2 - wall - boss_r - 1.5;
axis_y = outer_y / 2 - wall - boss_r - 1.5;

lip_h = 1.4;
lip_wall = 1.6;
lip_clearance = 0.35;

module screw_axes() {
    for (x = [-axis_x, axis_x])
        for (y = [-axis_y, axis_y])
            translate([x, y, 0])
                children();
}

module rounded_box(size, r) {
    hull() {
        for (x = [-size[0]/2 + r, size[0]/2 - r])
            for (y = [-size[1]/2 + r, size[1]/2 - r])
                translate([x, y, 0])
                    cylinder(h = size[2], r = r);
    }
}

module base_shell() {
    difference() {
        rounded_box([outer_x, outer_y, base_h], 3);

        translate([0, 0, floor_t])
            rounded_box([inner_x, inner_y, inner_h + 0.02], 2);

        // Exterior side lightening pockets, leaving 2 mm edge rims and floor/top bands.
        for (y = [-outer_y/2 - 0.01, outer_y/2 + 0.01])
            translate([0, y, 18])
                rotate([90, 0, 0])
                    rounded_box([42, 20, 3.0], 2);

        for (x = [-outer_x/2 - 0.01, outer_x/2 + 0.01])
            translate([x, 0, 18])
                rotate([90, 0, 90])
                    rounded_box([32, 20, 3.0], 2);
    }
}

module insert_bosses() {
    screw_axes()
        difference() {
            cylinder(h = boss_h, d = boss_d);
            translate([0, 0, boss_h - insert_bore_depth])
                cylinder(h = insert_bore_depth + 0.05, d = insert_bore_d);
            translate([0, 0, -0.05])
                cylinder(h = boss_h + 0.10, d = 2.7);
        }
}

module base_alignment_ribs() {
    rib_t = 1.6;
    rib_h = 10;
    rib_len_x = 22;
    rib_len_y = 16;

    for (y = [-inner_y/2 + rib_t/2, inner_y/2 - rib_t/2])
        translate([0, y, floor_t])
            cube([rib_len_x, rib_t, rib_h], center = false);

    for (x = [-inner_x/2 + rib_t/2, inner_x/2 - rib_t/2])
        translate([x, 0, floor_t])
            cube([rib_t, rib_len_y, rib_h], center = false);
}

module base() {
    union() {
        base_shell();
        insert_bosses();
        base_alignment_ribs();
    }
}

module lid() {
    difference() {
        union() {
            translate([0, 0, base_h])
                rounded_box([outer_x, outer_y, lid_t], 3);

            // Downward locating lip fits just inside base walls with clearance.
            translate([0, 0, base_h - lip_h])
                difference() {
                    rounded_box([inner_x - 2 * lip_clearance, inner_y - 2 * lip_clearance, lip_h], 1.6);
                    translate([0, 0, -0.01])
                        rounded_box([
                            inner_x - 2 * lip_clearance - 2 * lip_wall,
                            inner_y - 2 * lip_clearance - 2 * lip_wall,
                            lip_h + 0.02
                        ], 1.0);
                }
        }

        screw_axes() {
            translate([0, 0, base_h - 0.05])
                cylinder(h = lid_t + 0.15, d = m3_clearance_d);

            translate([0, 0, base_h + lid_t - screw_head_depth])
                cylinder(h = screw_head_depth + 0.08, d = screw_head_d);
        }

        // Lid lightening recesses on top surface, leaving screw lands and a sealed rim.
        translate([0, 0, base_h + lid_t - 1.25])
            rounded_box([38, 26, 1.35], 2);

        for (x = [-18, 18])
            translate([x, 0, base_h + lid_t - 1.25])
                rounded_box([10, 42, 1.35], 2);
    }
}

base();
lid();