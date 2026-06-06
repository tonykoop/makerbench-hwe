// Two-part enclosure with M3 heat-set inserts
$fn = 32;

// Enclosure dimensions
ext_x = 75;
ext_y = 75;
cav_x = 70;
cav_y = 70;
wall = 2.5;

// Base: 14 mm total (2.5 mm bottom wall + 11.5 mm cavity)
base_h = 14;
base_cav_h = base_h - wall;

// Lid: 11 mm total (2 mm top wall + 9 mm cavity)
// Combined cavity depth: 11.5 + 9 = 20.5 mm
lid_h = 11;
lid_top_thick = 2;
lid_cav_h = lid_h - lid_top_thick;

// M3 fastener positions (10 mm inset from edges)
screw_pos = [
  [10, 10],
  [10, 65],
  [65, 10],
  [65, 65]
];

screw_clear_d = 3.5;      // M3 screw clearance hole
insert_bore_d = 5;        // M3 heat-set insert bore
insert_depth = 5;         // Insert embedding depth

module base() {
  difference() {
    cube([ext_x, ext_y, base_h], center = false);
    
    // Internal cavity
    translate([wall, wall, wall])
      cube([cav_x, cav_y, base_cav_h], center = false);
    
    // Heat-set insert bores (5 mm deep from top)
    for (pos = screw_pos) {
      translate([pos[0], pos[1], base_h - insert_depth])
        cylinder(d = insert_bore_d, h = insert_depth + 1, center = false);
    }
  }
}

module lid() {
  difference() {
    cube([ext_x, ext_y, lid_h], center = false);
    
    // Internal cavity (9 mm deep)
    translate([wall, wall, 0])
      cube([cav_x, cav_y, lid_cav_h], center = false);
    
    // M3 clearance holes (3.5 mm diameter)
    for (pos = screw_pos) {
      translate([pos[0], pos[1], -1])
        cylinder(d = screw_clear_d, h = lid_h + 2, center = false);
    }
  }
}

// Render base and lid in assembled position
base();
translate([0, 0, base_h])
  lid();