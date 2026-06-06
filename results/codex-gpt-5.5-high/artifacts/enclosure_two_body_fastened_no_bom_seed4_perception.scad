$fn = 72;

wall = 3.0;

cavity_x = 50;
cavity_y = 60;
cavity_z = 20;

clearance = 0.30;

base_outer_x = cavity_x + 2 * wall;
base_outer_y = cavity_y + 2 * wall;
base_floor = wall;
base_z = base_floor + cavity_z;

lid_z = 3.0;
lid_overlap_depth = 2.0;
lid_lip_wall = 1.5;
lid_outer_x = base_outer_x;
lid_outer_y = base_outer_y;

m3_clearance_d = 3.4;
m3_head_counterbore_d = 6.2;
m3_head_counterbore_depth = 3.1;

insert_bore_d = 4.6;
insert_bore_depth = 6.2;

boss_d = 8.5;
boss_z = base_z - base_floor;

screw_margin = 9.0;
screw_positions = [
    [ screw_margin - base_outer_x / 2,  screw_margin - base_outer_y / 2],
    [ base_outer_x / 2 - screw_margin,  screw_margin - base_outer_y / 2],
    [ base_outer_x / 2 - screw_margin,  base_outer_y / 2 - screw_margin],
    [ screw_margin - base_outer_x / 2,  base_outer_y / 2 - screw_margin]
];

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    hull() {
        for (sx = [-1, 1], sy = [-1, 1]) {
            translate([sx * (x / 2 - r), sy * (y / 2 - r), 0])
                cylinder(h = z, r = r);
        }
    }
}

module base_solid() {
    difference() {
        union() {
            rounded_box([base_outer_x, base_outer_y, base_z], 4);

            for (p = screw_positions) {
                translate([p[0], p[1], base_floor])
                    cylinder(h = boss_z, d = boss_d);
            }
        }

        translate([0, 0, base_floor])
            rounded_box([cavity_x, cavity_y, cavity_z + 0.02], 2);

        for (p = screw_positions) {
            translate([p[0], p[1], base_z - insert_bore_depth])
                cylinder(h = insert_bore_depth + 0.03, d = insert_bore_d);
        }
    }
}

module lid_solid() {
    difference() {
        union() {
            translate([0, 0, base_z])
                rounded_box([lid_outer_x, lid_outer_y, lid_z], 4);

            translate([0, 0, base_z - lid_overlap_depth])
                difference() {
                    rounded_box([
                        cavity_x - 2 * clearance,
                        cavity_y - 2 * clearance,
                        lid_overlap_depth
                    ], 2);

                    translate([0, 0, -0.01])
                        rounded_box([
                            cavity_x - 2 * clearance - 2 * lid_lip_wall,
                            cavity_y - 2 * clearance - 2 * lid_lip_wall,
                            lid_overlap_depth + 0.02
                        ], 1.2);
                }
        }

        for (p = screw_positions) {
            translate([p[0], p[1], base_z - 0.02])
                cylinder(h = lid_z + 0.06, d = m3_clearance_d);

            translate([p[0], p[1], base_z + lid_z - m3_head_counterbore_depth])
                cylinder(h = m3_head_counterbore_depth + 0.03, d = m3_head_counterbore_d);
        }
    }
}

color("lightgray") base_solid();
color("steelblue") lid_solid();