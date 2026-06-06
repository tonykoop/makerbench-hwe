// Two-part lightweight enclosure, assembled view
// Units: mm

$fn = 64;

// -------------------- Parameters --------------------
wall = 2.0;
min_wall = 1.5;

inner_x = 54;
inner_y = 44;
inner_z = 30;

base_floor = 2.0;
base_h = 20.0;
lid_h = 10.0;

seam_overlap = 1.2;
lid_skirt_h = 4.0;
lid_skirt_clearance = 0.25;

outer_x = inner_x + 2*wall;
outer_y = inner_y + 2*wall;

screw_d_clear = 3.4;      // M3 clearance
insert_bore_d = 4.8;      // heat-set insert bore, nominal
insert_bore_depth = 5.2;

boss_d = 8.8;
boss_wall = 1.8;

corner_margin = 8.0;

// Lightening window sizing
window_w = 14;
window_h = 8;
window_corner_r = 2.0;

// Assembly offset so solids do not interfere
assembly_gap = 4.0;

// Screw hole centers
hole_pts = [
    [corner_margin, corner_margin],
    [outer_x - corner_margin, corner_margin],
    [outer_x - corner_margin, outer_y - corner_margin],
    [corner_margin, outer_y - corner_margin]
];

// -------------------- Helpers --------------------
module rounded_rect_2d(x, y, r) {
    offset(r = r)
        square([x - 2*r, y - 2*r], center = true);
}

module shell_box(x, y, z, wall_t, floor_t=wall_t, top_t=wall_t) {
    difference() {
        cube([x, y, z], center = false);
        translate([wall_t, wall_t, floor_t])
            cube([x - 2*wall_t, y - 2*wall_t, z - floor_t - top_t], center = false);
    }
}

module corner_bosses(z0, boss_h) {
    for (p = hole_pts) {
        translate([p[0], p[1], z0])
            cylinder(h = boss_h, d = boss_d, center = false);
    }
}

module lightening_windows_on_sides(z0, z1) {
    // X sides
    for (yy = [16, outer_y - 16]) {
        translate([outer_x/2, yy, (z0 + z1)/2])
            rotate([0,90,0])
                linear_extrude(height = outer_x + 0.2, center = true)
                    rounded_rect_2d(window_w, z1 - z0, window_corner_r);
    }
    // Y sides
    for (xx = [16, outer_x - 16]) {
        translate([xx, outer_y/2, (z0 + z1)/2])
            rotate([90,0,0])
                linear_extrude(height = outer_y + 0.2, center = true)
                    rounded_rect_2d(window_w, z1 - z0, window_corner_r);
    }
}

module interior_ribs_base() {
    // Keep walls thin but reinforce bosses and span long sides
    rib_t = 1.6;
    rib_h = 8.0;
    z0 = base_floor;
    z1 = base_floor + rib_h;

    // Longitudinal ribs
    for (yy = [14, outer_y - 14]) {
        translate([wall + 2, yy - rib_t/2, z0])
            cube([outer_x - 2*(wall + 2), rib_t, rib_h], center = false);
    }

    // Transverse ribs between bosses
    for (xx = [14, outer_x - 14]) {
        translate([xx - rib_t/2, wall + 2, z0])
            cube([rib_t, outer_y - 2*(wall + 2), rib_h], center = false);
    }
}

module interior_ribs_lid() {
    rib_t = 1.5;
    rib_h = 3.0;
    z0 = lid_h - rib_h;
    for (yy = [14, outer_y - 14]) {
        translate([wall + 1.5, yy - rib_t/2, z0])
            cube([outer_x - 2*(wall + 1.5), rib_t, rib_h], center = false);
    }
    for (xx = [14, outer_x - 14]) {
        translate([xx - rib_t/2, wall + 1.5, z0])
            cube([rib_t, outer_y - 2*(wall + 1.5), rib_h], center = false);
    }
}

// -------------------- Base --------------------
module base_part() {
    difference() {
        union() {
            // Outer shell
            shell_box(outer_x, outer_y, base_h, wall, base_floor, wall);

            // Bosses for inserts
            corner_bosses(base_floor, insert_bore_depth + 1.2);

            // Internal ribs and supports
            interior_ribs_base();

            // Perimeter seam lip for lid registration
            translate([wall + seam_overlap, wall + seam_overlap, base_h - seam_overlap])
                cube([outer_x - 2*(wall + seam_overlap),
                      outer_y - 2*(wall + seam_overlap),
                      seam_overlap], center = false);
        }

        // Internal cavity
        translate([wall, wall, base_floor])
            cube([inner_x, inner_y, inner_z], center = false);

        // Lightening windows in side walls, preserving minimum wall thickness
        lightening_windows_on_sides(5.0, base_h - 4.0);

        // Insert bores in bosses
        for (p = hole_pts) {
            translate([p[0], p[1], base_floor + 0.2])
                cylinder(h = insert_bore_depth, d = insert_bore_d, center = false);
        }

        // Through reliefs to reduce mass around base bottom interior
        for (xx = [12, outer_x - 12]) {
            translate([xx - 4.0, wall + 4.0, 3.0])
                cube([8.0, outer_y - 2*(wall + 4.0), 6.0], center = false);
        }
    }
}

// -------------------- Lid --------------------
module lid_part() {
    difference() {
        union() {
            // Lid top shell
            shell_box(outer_x, outer_y, lid_h, wall, wall, wall);

            // Inner locating skirt
            translate([wall + lid_skirt_clearance,
                       wall + lid_skirt_clearance,
                       0.0])
                cube([inner_x - 2*lid_skirt_clearance,
                      inner_y - 2*lid_skirt_clearance,
                      lid_skirt_h], center = false);

            // Internal ribs
            interior_ribs_lid();
        }

        // Clear internal cavity for assembled lid
        translate([wall, wall, wall])
            cube([inner_x, inner_y, lid_h - wall], center = false);

        // Screw clearance holes
        for (p = hole_pts) {
            translate([p[0], p[1], -0.2])
                cylinder(h = lid_h + 0.4, d = screw_d_clear, center = false);
        }

        // Weight-reduction windows in lid top
        for (xx = [16, outer_x - 16]) {
            translate([xx, outer_y/2, lid_h/2])
                rotate([90,0,0])
                    linear_extrude(height = outer_y + 0.2, center = true)
                        rounded_rect_2d(12, 5.5, 1.5);
        }
        for (yy = [16, outer_y - 16]) {
            translate([outer_x/2, yy, lid_h/2])
                rotate([0,90,0])
                    linear_extrude(height = outer_x + 0.2, center = true)
                        rounded_rect_2d(12, 5.5, 1.5);
        }
    }
}

// -------------------- Assembled view --------------------
translate([0, 0, 0])
    base_part();

translate([0, 0, base_h + assembly_gap])
    lid_part();