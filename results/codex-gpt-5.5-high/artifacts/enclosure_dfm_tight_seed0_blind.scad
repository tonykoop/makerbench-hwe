$fn = 64;

// Units: mm
inner_x = 72;
inner_y = 72;
inner_h = 22;

wall = 2.5;
base_floor = 2.5;
lid_roof = 2.5;
lid_skirt_h = 3.0;
seal_gap = 0.35;

outer_x = 90;
outer_y = 90;
base_h = base_floor + inner_h;
lid_h = lid_roof + lid_skirt_h;

screw_x = 38;
screw_y = 38;

m3_clearance_d = 3.4;
m3_insert_bore_d = 4.6;
m3_insert_bore_depth = 6.2;

boss_d = 9.0;
boss_h = base_h;
boss_lighten_d = 2.2;

lid_boss_pad_d = 7.0;
lid_pad_h = lid_h;

preview_gap = 0.15;

echo("DFM_CHECK internal_cavity_mm =", inner_x, inner_y, inner_h);
echo("DFM_CHECK wall_nominal_mm =", wall);
echo("DFM_CHECK min_wall_target_mm >= 1.5");
echo("DFM_CHECK lid_clearance_hole_d_mm =", m3_clearance_d);
echo("DFM_CHECK base_insert_bore_d_mm =", m3_insert_bore_d);
echo("DFM_CHECK fastener_axes_aligned_by_shared_coordinates_mm =", 0);

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    linear_extrude(height = z)
        offset(r = r)
            square([x - 2*r, y - 2*r], center = true);
}

module screw_axes() {
    for (x = [-screw_x, screw_x])
        for (y = [-screw_y, screw_y])
            translate([x, y, 0])
                children();
}

module base_shell() {
    difference() {
        rounded_box([outer_x, outer_y, base_h], 3);

        translate([0, 0, base_floor])
            rounded_box([inner_x, inner_y, inner_h + 0.2], 1.5);

        translate([0, 0, base_floor + 4])
            rounded_box([inner_x + 10, inner_y + 10, inner_h - 3.8], 1.8);
    }
}

module base_bosses() {
    screw_axes()
        difference() {
            cylinder(d = boss_d, h = boss_h);

            translate([0, 0, base_h - m3_insert_bore_depth])
                cylinder(d = m3_insert_bore_d, h = m3_insert_bore_depth + 0.1);

            translate([0, 0, base_floor])
                cylinder(d = boss_lighten_d, h = base_h - base_floor - m3_insert_bore_depth - 0.8);
        }
}

module base_part() {
    difference() {
        union() {
            base_shell();
            base_bosses();

            translate([0, 0, base_h - 1.2])
                difference() {
                    rounded_box([inner_x + 2.4, inner_y + 2.4, 1.2], 1.3);
                    translate([0, 0, -0.05])
                        rounded_box([inner_x - 2.0, inner_y - 2.0, 1.4], 1.0);
                }
        }

        screw_axes()
            translate([0, 0, base_h - m3_insert_bore_depth])
                cylinder(d = m3_insert_bore_d, h = m3_insert_bore_depth + 0.2);
    }
}

module lid_shell() {
    difference() {
        union() {
            translate([0, 0, lid_skirt_h])
                rounded_box([outer_x, outer_y, lid_roof], 3);

            difference() {
                rounded_box([inner_x + 2.0 - seal_gap, inner_y + 2.0 - seal_gap, lid_skirt_h], 1.2);
                translate([0, 0, -0.05])
                    rounded_box([inner_x - 3.0, inner_y - 3.0, lid_skirt_h + 0.1], 0.8);
            }

            screw_axes()
                cylinder(d = lid_boss_pad_d, h = lid_pad_h);
        }

        screw_axes()
            translate([0, 0, -0.1])
                cylinder(d = m3_clearance_d, h = lid_h + 0.2);

        translate([0, 0, lid_skirt_h + 0.2])
            rounded_box([inner_x + 10, inner_y + 10, lid_roof + 0.2], 1.8);
    }
}

module lid_part() {
    translate([0, 0, base_h + preview_gap])
        lid_shell();
}

base_part();
lid_part();