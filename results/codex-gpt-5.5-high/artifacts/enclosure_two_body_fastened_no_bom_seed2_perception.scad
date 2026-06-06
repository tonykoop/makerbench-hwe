$fn = 64;

wall = 2.5;

cavity_x = 40;
cavity_y = 40;
cavity_z = 20;

outer_x = 58;
outer_y = 58;

base_floor = wall;
base_h = base_floor + cavity_z;
lid_h = 5;

m3_clearance_d = 3.4;
m3_insert_bore_d = 4.6;
m3_insert_bore_depth = 7.0;
screw_head_counterbore_d = 6.2;
screw_head_counterbore_depth = 3.2;

fastener_offset = 7.5;
fastener_positions = [
    [ outer_x/2 - fastener_offset,  outer_y/2 - fastener_offset],
    [-outer_x/2 + fastener_offset,  outer_y/2 - fastener_offset],
    [-outer_x/2 + fastener_offset, -outer_y/2 + fastener_offset],
    [ outer_x/2 - fastener_offset, -outer_y/2 + fastener_offset]
];

module screw_axes() {
    for (p = fastener_positions)
        translate([p[0], p[1], 0])
            children();
}

module base() {
    difference() {
        cube([outer_x, outer_y, base_h], center = false);

        translate([(outer_x - cavity_x)/2, (outer_y - cavity_y)/2, base_floor])
            cube([cavity_x, cavity_y, cavity_z + 0.1], center = false);

        translate([outer_x/2, outer_y/2, base_h - m3_insert_bore_depth])
            screw_axes()
                cylinder(d = m3_insert_bore_d, h = m3_insert_bore_depth + 0.2);
    }
}

module lid() {
    difference() {
        translate([0, 0, base_h])
            cube([outer_x, outer_y, lid_h], center = false);

        translate([outer_x/2, outer_y/2, base_h - 0.1])
            screw_axes()
                cylinder(d = m3_clearance_d, h = lid_h + 0.2);

        translate([outer_x/2, outer_y/2, base_h + lid_h - screw_head_counterbore_depth])
            screw_axes()
                cylinder(d = screw_head_counterbore_d, h = screw_head_counterbore_depth + 0.2);
    }
}

base();
lid();