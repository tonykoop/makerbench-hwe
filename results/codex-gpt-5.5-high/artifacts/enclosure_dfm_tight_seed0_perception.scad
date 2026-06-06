$fn = 72;

// Units: mm
internal_x = 72;
internal_y = 72;
internal_z = 20;

wall = 2.5;
min_web = 1.5;

outer_x = internal_x + 2 * wall;
outer_y = internal_y + 2 * wall;
base_floor = 2.5;
base_z = base_floor + internal_z;

lid_z = 3.0;
assembly_gap = 0.25;

corner_r = 4;
boss_r = 5.7;
boss_axis_x = 32.0;
boss_axis_y = 32.0;

m3_clear_r = 1.7;
m3_head_r = 3.1;
m3_head_depth = 1.0;

insert_bore_r = 2.35;
insert_bore_z = 7.0;
insert_lead_r = 2.65;
insert_lead_z = 1.0;

light_slot_w = 7;
light_slot_l = 48;

module rounded_box(size, r) {
    hull() {
        for (x = [-size[0] / 2 + r, size[0] / 2 - r])
            for (y = [-size[1] / 2 + r, size[1] / 2 - r])
                translate([x, y, 0])
                    cylinder(h = size[2], r = r);
    }
}

module screw_axes() {
    for (x = [-boss_axis_x, boss_axis_x])
        for (y = [-boss_axis_y, boss_axis_y])
            translate([x, y, 0])
                children();
}

module base_shell() {
    difference() {
        union() {
            rounded_box([outer_x, outer_y, base_z], corner_r);

            screw_axes()
                cylinder(h = base_z, r = boss_r);
        }

        translate([-internal_x / 2, -internal_y / 2, base_floor])
            cube([internal_x, internal_y, internal_z + 0.2]);

        screw_axes() {
            translate([0, 0, base_z - insert_bore_z + 0.01])
                cylinder(h = insert_bore_z + 0.2, r = insert_bore_r);

            translate([0, 0, base_z - insert_lead_z + 0.02])
                cylinder(h = insert_lead_z + 0.2, r = insert_lead_r);
        }

        for (x = [-22, 0, 22])
            translate([x - light_slot_w / 2, -light_slot_l / 2, -0.1])
                cube([light_slot_w, light_slot_l, base_floor + 0.2]);

        for (y = [-22, 22])
            translate([-light_slot_l / 2, y - light_slot_w / 2, -0.1])
                cube([light_slot_l, light_slot_w, base_floor + 0.2]);
    }
}

module lid() {
    translate([0, 0, base_z + assembly_gap])
        difference() {
            union() {
                rounded_box([outer_x, outer_y, lid_z], corner_r);

                translate([0, 0, -1.5])
                    difference() {
                        rounded_box([internal_x - 0.6, internal_y - 0.6, 1.5], 2.5);
                        translate([0, 0, -0.1])
                            rounded_box([internal_x - 5.6, internal_y - 5.6, 1.8], 2.0);
                    }
            }

            screw_axes() {
                translate([0, 0, -1.7])
                    cylinder(h = lid_z + 3.6, r = m3_clear_r);

                translate([0, 0, lid_z - m3_head_depth])
                    cylinder(h = m3_head_depth + 0.2, r = m3_head_r);
            }

            for (x = [-24, 0, 24])
                translate([x - light_slot_w / 2, -light_slot_l / 2, -0.1])
                    cube([light_slot_w, light_slot_l, lid_z + 0.2]);

            for (y = [-24, 24])
                translate([-light_slot_l / 2, y - light_slot_w / 2, -0.1])
                    cube([light_slot_l, light_slot_w, lid_z + 0.2]);
        }
}

echo("Internal cavity clear mm:", internal_x, internal_y, internal_z);
echo("Nominal wall mm:", wall);
echo("Minimum designed web/rib mm:", min_web);
echo("Fastener axes lid/base shared at +/- mm:", boss_axis_x, boss_axis_y);
echo("Approx solid block mm:", outer_x, outer_y, base_z + assembly_gap + lid_z);
echo("DFM target: shell/lightened mass fraction below 45 percent of solid block");

base_shell();
lid();