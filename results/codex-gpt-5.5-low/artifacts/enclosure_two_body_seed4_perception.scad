// Two-part 3D-printable enclosure, assembled position
// Units: mm

$fn = 64;

// Requirements
internal_x = 50;
internal_y = 60;
internal_z = 20;
wall = 3.0;

// Print clearances
radial_clearance = 0.35;
vertical_clearance = 0.25;

// Lid/base split
base_internal_depth = 18;
lid_internal_depth = internal_z - base_internal_depth;

// Fit geometry
lip_height = 4.0;
lip_wall = 1.8;

// Derived dimensions
outer_x = internal_x + 2 * wall;
outer_y = internal_y + 2 * wall;
base_outer_z = base_internal_depth + wall;
lid_outer_z = lid_internal_depth + wall + lip_height;

base_inner_x = internal_x;
base_inner_y = internal_y;

lid_socket_x = outer_x + 2 * radial_clearance;
lid_socket_y = outer_y + 2 * radial_clearance;

lid_outer_x = lid_socket_x + 2 * wall;
lid_outer_y = lid_socket_y + 2 * wall;

base_lip_outer_x = internal_x - 2 * radial_clearance;
base_lip_outer_y = internal_y - 2 * radial_clearance;
base_lip_inner_x = base_lip_outer_x - 2 * lip_wall;
base_lip_inner_y = base_lip_outer_y - 2 * lip_wall;

corner_r = 4;
inner_r = max(0.5, corner_r - wall);
lip_outer_r = max(0.5, inner_r - radial_clearance);
lip_inner_r = max(0.5, lip_outer_r - lip_wall);

module rounded_box(size, r) {
    x = size[0];
    y = size[1];
    z = size[2];

    hull() {
        for (sx = [-1, 1], sy = [-1, 1]) {
            translate([
                sx * (x / 2 - r),
                sy * (y / 2 - r),
                0
            ])
                cylinder(h = z, r = r);
        }
    }
}

module rounded_tube(outer_size, inner_size, height, outer_r, inner_r) {
    difference() {
        rounded_box([outer_size[0], outer_size[1], height], outer_r);
        translate([0, 0, -0.05])
            rounded_box([inner_size[0], inner_size[1], height + 0.10], inner_r);
    }
}

module base() {
    union() {
        difference() {
            rounded_box([outer_x, outer_y, base_outer_z], corner_r);

            translate([0, 0, wall])
                rounded_box(
                    [base_inner_x, base_inner_y, base_outer_z + 0.10],
                    inner_r
                );
        }

        translate([0, 0, base_outer_z - vertical_clearance])
            rounded_tube(
                [base_lip_outer_x, base_lip_outer_y],
                [base_lip_inner_x, base_lip_inner_y],
                lip_height,
                lip_outer_r,
                lip_inner_r
            );
    }
}

module lid() {
    translate([0, 0, base_outer_z])
        difference() {
            rounded_box([lid_outer_x, lid_outer_y, lid_outer_z], corner_r + wall + radial_clearance);

            translate([0, 0, wall])
                rounded_box(
                    [lid_socket_x, lid_socket_y, lid_outer_z + 0.10],
                    corner_r + radial_clearance
                );

            translate([0, 0, wall - 0.05])
                rounded_box(
                    [base_lip_outer_x + 2 * radial_clearance, base_lip_outer_y + 2 * radial_clearance, lip_height + vertical_clearance + 0.10],
                    lip_outer_r + radial_clearance
                );
        }
}

base();
lid();