$fn = 72;

wall = 2.0;
min_wall = 1.5;

inner_x = 54;
inner_y = 44;
cavity_h = 32;

outer_x = inner_x + 2 * wall;
outer_y = inner_y + 2 * wall;

base_bottom = 2.0;
base_h = base_bottom + cavity_h;

lid_t = 2.6;
lid_z = base_h;

corner_r = 4.0;

post_d = 7.2;
post_r = post_d / 2;
insert_bore_d = 4.7;
insert_bore_depth = 5.8;

m3_clearance_d = 3.4;
screw_head_d = 6.2;
screw_head_depth = 1.5;

axis_margin_x = 7.5;
axis_margin_y = 7.5;

boss_axes = [
    [axis_margin_x, axis_margin_y],
    [outer_x - axis_margin_x, axis_margin_y],
    [outer_x - axis_margin_x, outer_y - axis_margin_y],
    [axis_margin_x, outer_y - axis_margin_y]
];

vent_slot_w = 2.2;
vent_slot_l = 22;
vent_pitch = 5.0;

module rounded_rect_2d(x, y, r) {
    hull() {
        translate([r, r]) circle(r = r);
        translate([x - r, r]) circle(r = r);
        translate([x - r, y - r]) circle(r = r);
        translate([r, y - r]) circle(r = r);
    }
}

module rounded_box(x, y, z, r) {
    linear_extrude(height = z)
        rounded_rect_2d(x, y, r);
}

module screw_axis_markers() {
    for (p = boss_axes)
        translate([p[0], p[1], -0.2])
            cylinder(d = 0.6, h = base_h + lid_t + 0.4);
}

module base_shell() {
    difference() {
        rounded_box(outer_x, outer_y, base_h, corner_r);

        translate([wall, wall, base_bottom])
            rounded_box(inner_x, inner_y, cavity_h + 0.25, max(corner_r - wall, 1.2));

        for (p = boss_axes) {
            translate([p[0], p[1], base_h - insert_bore_depth])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.35);

            translate([p[0], p[1], base_bottom])
                cylinder(d = 2.6, h = base_h + 0.4);
        }

        for (x = [outer_x / 2 - vent_pitch * 2 : vent_pitch : outer_x / 2 + vent_pitch * 2])
            translate([x - vent_slot_w / 2, wall + 6, -0.1])
                cube([vent_slot_w, vent_slot_l, base_bottom + 0.2]);

        for (x = [outer_x / 2 - vent_pitch * 2 : vent_pitch : outer_x / 2 + vent_pitch * 2])
            translate([x - vent_slot_w / 2, outer_y - wall - 6 - vent_slot_l, -0.1])
                cube([vent_slot_w, vent_slot_l, base_bottom + 0.2]);
    }
}

module base_posts() {
    for (p = boss_axes)
        difference() {
            translate([p[0], p[1], base_bottom])
                cylinder(d = post_d, h = cavity_h);

            translate([p[0], p[1], base_h - insert_bore_depth])
                cylinder(d = insert_bore_d, h = insert_bore_depth + 0.35);

            translate([p[0], p[1], base_bottom - 0.1])
                cylinder(d = 2.6, h = cavity_h + 0.3);
        }
}

module base_lightening_ribs() {
    rib_t = 1.6;
    rib_h = 7;

    difference() {
        union() {
            translate([wall, outer_y / 2 - rib_t / 2, base_bottom])
                cube([inner_x, rib_t, rib_h]);

            translate([outer_x / 2 - rib_t / 2, wall, base_bottom])
                cube([rib_t, inner_y, rib_h]);
        }

        translate([outer_x / 2, outer_y / 2, base_bottom - 0.1])
            cylinder(d = 18, h = rib_h + 0.2);

        for (p = boss_axes)
            translate([p[0], p[1], base_bottom - 0.1])
                cylinder(d = post_d + 0.8, h = rib_h + 0.2);
    }
}

module base() {
    color([0.18, 0.42, 0.70])
        union() {
            base_shell();
            base_posts();
            base_lightening_ribs();
        }
}

module lid() {
    color([0.95, 0.62, 0.22])
        translate([0, 0, lid_z])
            difference() {
                rounded_box(outer_x, outer_y, lid_t, corner_r);

                for (p = boss_axes) {
                    translate([p[0], p[1], -0.2])
                        cylinder(d = m3_clearance_d, h = lid_t + 0.4);

                    translate([p[0], p[1], lid_t - screw_head_depth])
                        cylinder(d = screw_head_d, h = screw_head_depth + 0.25);
                }

                for (x = [outer_x / 2 - vent_pitch * 2 : vent_pitch : outer_x / 2 + vent_pitch * 2])
                    translate([x - vent_slot_w / 2, outer_y / 2 - vent_slot_l / 2, -0.1])
                        cube([vent_slot_w, vent_slot_l, lid_t + 0.2]);
            }
}

base();
lid();