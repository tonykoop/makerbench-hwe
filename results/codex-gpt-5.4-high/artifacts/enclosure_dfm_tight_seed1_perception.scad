$fn = 72;

cavity = [50, 40, 30];
wall = 2.0;
floor_t = 2.0;

lid_t = 2.0;
skirt_t = 2.0;
skirt_h = 4.0;
fit = 0.30;

boss_d = 8.6;          // 2.0 mm radial wall around a 4.6 mm insert bore
insert_d = 4.6;        // typical M3 heat-set insert pilot for printed plastics
insert_depth = 5.8;
clearance_d = 3.4;     // M3 printed clearance
bridge_w = 2.0;

exploded_gap = 0.80;   // keeps parts separate while preserving assembled XY alignment
eps = 0.05;

shell_outer = [cavity[0] + 2*wall, cavity[1] + 2*wall];
base_h = cavity[2] + floor_t;
lid_outer = [shell_outer[0] + 2*(skirt_t + fit), shell_outer[1] + 2*(skirt_t + fit)];

screw_x = shell_outer[0]/2 + 6.0;
screw_y = shell_outer[1]/2 + 6.0;
screw_xy = [for (sx = [-1, 1], sy = [-1, 1]) [sx*screw_x, sy*screw_y]];

assert(wall >= 1.5);
assert(skirt_t >= 1.5);
assert((boss_d - insert_d)/2 >= 1.5);
assert(screw_x - boss_d/2 - cavity[0]/2 >= 1.5);
assert(screw_y - boss_d/2 - cavity[1]/2 >= 1.5);

module orthogonal_bridges_2d(pt, rect, w) {
    let(
        xh = rect[0] / 2,
        yh = rect[1] / 2,
        x0 = pt[0] > 0 ? xh : pt[0],
        y0 = pt[1] > 0 ? yh : pt[1],
        xlen = abs(pt[0]) - xh,
        ylen = abs(pt[1]) - yh
    ) {
        if (xlen > 0)
            translate([x0, pt[1] - w/2]) square([xlen, w]);
        if (ylen > 0)
            translate([pt[0] - w/2, y0]) square([w, ylen]);
    }
}

module outline_with_ears_2d(rect) {
    union() {
        square(rect, center = true);
        for (pt = screw_xy) {
            translate(pt) circle(d = boss_d);
            orthogonal_bridges_2d(pt, rect, bridge_w);
        }
    }
}

module base_part() {
    difference() {
        linear_extrude(height = base_h)
            outline_with_ears_2d(shell_outer);

        translate([-cavity[0]/2, -cavity[1]/2, floor_t])
            cube([cavity[0], cavity[1], cavity[2] + eps]);

        for (pt = screw_xy) {
            translate([pt[0], pt[1], base_h - insert_depth])
                cylinder(h = insert_depth + eps, d = insert_d);

            translate([pt[0], pt[1], base_h - 1.0])
                cylinder(h = 1.0 + eps, d1 = insert_d, d2 = 5.2);
        }
    }
}

module lid_part() {
    lid_underside_z = base_h + exploded_gap;

    difference() {
        union() {
            translate([0, 0, lid_underside_z])
                linear_extrude(height = lid_t)
                    outline_with_ears_2d(lid_outer);

            translate([0, 0, lid_underside_z - skirt_h])
                linear_extrude(height = skirt_h)
                    difference() {
                        square(lid_outer, center = true);
                        square([shell_outer[0] + 2*fit, shell_outer[1] + 2*fit], center = true);
                    }
        }

        for (pt = screw_xy)
            translate([pt[0], pt[1], lid_underside_z - skirt_h - eps])
                cylinder(h = lid_t + skirt_h + 2*eps, d = clearance_d);
    }
}

base_part();
lid_part();