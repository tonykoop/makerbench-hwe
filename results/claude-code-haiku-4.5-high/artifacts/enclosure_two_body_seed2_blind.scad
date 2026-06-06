// Two-part 3D-printable enclosure with 0.3 mm print clearance
// Internal cavity: 40 x 40 x 20.3 mm | Wall thickness: 2.5 mm

wall = 2.5;
cavity_x = 40;
cavity_y = 40;
outer_x = cavity_x + 2 * wall;
outer_y = cavity_y + 2 * wall;
clearance = 0.3;

// Base part (lower)
base_height = 15;
base = difference() {
  cube([outer_x, outer_y, base_height]);
  translate([wall, wall, wall]) 
    cube([cavity_x, cavity_y, base_height - wall]);
};

// Lid part (upper)
lid_height = 10;
lid_z = base_height + clearance;
lid = difference() {
  translate([0, 0, lid_z]) cube([outer_x, outer_y, lid_height]);
  translate([wall, wall, lid_z]) 
    cube([cavity_x, cavity_y, lid_height - wall]);
};

// Render both parts in assembled position
union() {
  base;
  lid;
}