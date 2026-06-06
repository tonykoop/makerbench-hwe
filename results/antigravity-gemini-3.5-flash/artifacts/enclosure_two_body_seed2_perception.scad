// Parameters
cavity_width = 40.0;
cavity_length = 40.0;
cavity_height_base = 15.0;
cavity_height_lid = 5.0;
wall_thickness = 2.5;
lip_height = 2.0;
clearance = 0.2; // 3D printing clearance

// Radii calculations for rounded corners to maintain uniform wall thickness
r_outer = 4.0;
r_inner = r_outer - wall_thickness; // 1.5
lip_width = wall_thickness / 2;     // 1.25
r_lip_outer = r_outer - lip_width;  // 2.75
r_pocket = r_lip_outer + clearance; // 2.95

// Derived dimensions
outer_width = cavity_width + 2 * wall_thickness;
outer_length = cavity_length + 2 * wall_thickness;

module rounded_box(w, l, h, r) {
    translate([-w/2, -l/2, 0]) {
        hull() {
            translate([r, r, 0]) cylinder(r=r, h=h, $fn=64);
            translate([w-r, r, 0]) cylinder(r=r, h=h, $fn=64);
            translate([r, l-r, 0]) cylinder(r=r, h=h, $fn=64);
            translate([w-r, l-r, 0]) cylinder(r=r, h=h, $fn=64);
        }
    }
}

module base() {
    difference() {
        union() {
            // Main body
            translate([0, 0, -wall_thickness])
                rounded_box(outer_width, outer_length, cavity_height_base + wall_thickness, r_outer);
            
            // Mating lip
            translate([0, 0, cavity_height_base])
                rounded_box(
                    cavity_width + 2*lip_width,
                    cavity_length + 2*lip_width,
                    lip_height,
                    r_lip_outer
                );
        }
        
        // Inner cavity cutout
        translate([0, 0, 0])
            rounded_box(
                cavity_width,
                cavity_length,
                cavity_height_base + lip_height + 1.0,
                r_inner
            );
    }
}

module lid() {
    difference() {
        // Main lid body
        translate([0, 0, cavity_height_base])
            rounded_box(
                outer_width,
                outer_length,
                cavity_height_lid + wall_thickness,
                r_outer
            );
        
        union() {
            // Pocket for the base lip (with clearance)
            translate([0, 0, cavity_height_base - 0.1])
                rounded_box(
                    cavity_width + 2*(lip_width + clearance),
                    cavity_length + 2*(lip_width + clearance),
                    lip_height + clearance + 0.1,
                    r_pocket
                );
            
            // Lid inner cavity
            translate([0, 0, cavity_height_base + lip_height + clearance])
                rounded_box(
                    cavity_width,
                    cavity_length,
                    cavity_height_lid - (lip_height + clearance) + 0.1,
                    r_inner
                );
        }
    }
}

// Render both parts in their assembled configuration
color("LightBlue") base();
color("LightGreen") lid();