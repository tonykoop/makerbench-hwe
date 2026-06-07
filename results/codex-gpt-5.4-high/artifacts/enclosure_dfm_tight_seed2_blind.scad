$fn = 72;

// Core requirements
inner_x = 40;
inner_y = 40;
inner_z = 20;
wall = 2.5;

// Base geometry
base_floor = 2.5;
base_h = inner_z + base_floor;
base_pod_r = 4.4;

// Lid geometry
lid_top = 2.5;
skirt_depth = 4.0;
lid_h = skirt_depth + lid_top;
skirt_clear = 0.20;          // 0.4 mm total fit clearance over the base outer walls
lid_outer = inner_x + 2 * wall + 2 * (wall + skirt_clear);
lid_pod_r = 5.0;

// Fasteners
clearance_d = 3.4;           // M3 printed clearance
insert_bore_d = 4.2;         // Typical pilot for M3 heat-set inserts
insert_depth = 5.6;
insert_lead = 0.8;

// Lightening while keeping >= 1.5 mm minimum material
floor_skin = 1.6;
roof_skin = 1.6;
floor_pocket_xy = 32;
roof_pocket_xy = 32;
wall_slot_len = 27;
wall_slot_h = 11.5;

// Main shell outer size
main_outer_x = inner_x + 2 * wall;
main_outer_y = inner_y + 2 * wall;

// Screw locations outside the guaranteed 40 x 40 cavity envelope
screw_offset = inner_x / 2 + wall + 3.5;
screw_pts = [
    [-screw_offset, -screw_offset],
    [ screw_offset, -screw_offset],
    [ screw_offset,  screw_offset],
    [-screw_offset,  screw_offset]
];

// Display the lid aligned to the base but lifted to avoid interference
display_gap = 3.0;

module base_part() {
    difference() {
        union() {
            translate([0, 0, base_h / 2])
                cube([main_outer_x, main_outer_y, base_h], center = true);

            for (p = screw_pts)
                translate([p[0], p[1], 0])
                    cylinder(h = base_h, r = base_pod_r);
        }

        // Guaranteed clear internal cavity: 40 x 40 x 20 mm
        translate([0, 0, base_floor + inner_z / 2])
            cube([inner_x, inner_y, inner_z + 0.05], center = true);

        // Side-wall lightening slots, leaving strong corner and top/bottom bands
        for (sx = [-1, 1])
            translate([sx * (main_outer_x / 2 - wall / 2), 0, base_h / 2])
                cube([wall + 0.5, wall_slot_len, wall_slot_h], center = true);

        for (sy = [-1, 1])
            translate([0, sy * (main_outer_y / 2 - wall / 2), base_h / 2])
                cube([wall_slot_len, wall + 0.5, wall_slot_h], center = true);

        // Floor pocket leaves a 1.6 mm minimum floor in the center
        translate([0, 0, floor_skin + (base_floor - floor_skin + 0.05) / 2])
            cube([floor_pocket_xy, floor_pocket_xy, base_floor - floor_skin + 0.05], center = true);

        // Heat-set insert bores with a lead-in chamfer
        for (p = screw_pts) {
            translate([p[0], p[1], base_h - insert_depth])
                cylinder(h = insert_depth + 0.05, d = insert_bore_d);

            translate([p[0], p[1], base_h - insert_lead])
                cylinder(h = insert_lead + 0.05, d1 = insert_bore_d + 0.8, d2 = insert_bore_d);
        }
    }
}

module lid_part() {
    difference() {
        union() {
            translate([0, 0, lid_h / 2])
                cube([lid_outer, lid_outer, lid_h], center = true);

            for (p = screw_pts)
                translate([p[0], p[1], 0])
                    cylinder(h = lid_h, r = lid_pod_r);
        }

        // Underside recess creates the outer locating skirt
        translate([0, 0, skirt_depth / 2])
            cube([main_outer_x + 2 * skirt_clear, main_outer_y + 2 * skirt_clear, skirt_depth + 0.05], center = true);

        // Clearance holes aligned exactly to the insert axes
        for (p = screw_pts)
            translate([p[0], p[1], -0.02])
                cylinder(h = lid_h + 0.04, d = clearance_d);

        // Thin the roof center while holding 1.6 mm minimum skin
        translate([0, 0, skirt_depth + (lid_top - roof_skin + 0.05) / 2])
            cube([roof_pocket_xy, roof_pocket_xy, lid_top - roof_skin + 0.05], center = true);
    }
}

base_part();
translate([0, 0, base_h + display_gap]) lid_part();