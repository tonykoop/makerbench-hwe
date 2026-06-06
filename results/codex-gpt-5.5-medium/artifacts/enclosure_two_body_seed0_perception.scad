$fn = 48;

// Units: mm
wall = 2.5;
clearance = 0.30;

cavity_x = 72;
cavity_y = 72;
cavity_z = 23;

bottom_thick = wall;
base_wall_h = cavity_z + 4;
base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_outer_z = bottom_thick + base_wall_h;

lid_top_thick = wall;
lid_overhang = 1.5;
lid_outer_x = base_outer_x + 2 * lid_overhang;
lid_outer_y = base_outer_y + 2 * lid_overhang;

plug_depth = 4;
plug_wall = wall;
plug_outer_x = cavity_x - 2 * clearance;
plug_outer_y = cavity_y - 2 * clearance;
plug_inner_x = plug_outer_x - 2 * plug_wall;
plug_inner_y = plug_outer_y - 2 * plug_wall;

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    hull() {
        translate([r, r, 0]) cylinder(h = z, r = r);
        translate([x - r, r, 0]) cylinder(h = z, r = r);
        translate([r, y - r, 0]) cylinder(h = z, r = r);
        translate([x - r, y - r, 0]) cylinder(h = z, r = r);
    }
}

module base() {
    difference() {
        rounded_box([base_outer_x, base_outer_y, base_outer_z], 4);

        translate([wall, wall, bottom_thick])
            rounded_box([cavity_x, cavity_y, base_wall_h + 0.2], 2);
    }
}

module lid() {
    union() {
        translate([-lid_overhang, -lid_overhang, base_outer_z])
            rounded_box([lid_outer_x, lid_outer_y, lid_top_thick], 4.5);

        translate([
            wall + clearance,
            wall + clearance,
            base_outer_z - plug_depth
        ])
            difference() {
                rounded_box([plug_outer_x, plug_outer_y, plug_depth], 2);
                translate([plug_wall, plug_wall, -0.1])
                    rounded_box([plug_inner_x, plug_inner_y, plug_depth + 0.2], 1);
            }
    }
}

base();
lid();