$fn = 64;

plate_w = 90;
plate_h = 70;
plate_t = 3.0;
wall = 2.0;
rib = 2.0;

inner_w = plate_w - 2 * wall;
inner_h = plate_h - 2 * wall;
window_w = (inner_w - rib) / 2;
window_h = (inner_h - rib) / 2;

difference() {
    cube([plate_w, plate_h, plate_t]);

    for (x = [wall, wall + window_w + rib]) {
        for (y = [wall, wall + window_h + rib]) {
            translate([x, y, -0.1])
                cube([window_w, window_h, plate_t + 0.2]);
        }
    }
}