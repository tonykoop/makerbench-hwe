/*
 * Two-Part 3D-Printable Enclosure  –  Assembled View
 *
 * BOM:
 *   QTY 4   MB-SHCS-M3-16   M3 × 16 mm socket-head cap screw, alloy steel, hex socket
 *   QTY 4   MB-HSI-M3       M3 heat-set insert, brass
 *                            (length = 4.0 mm, OD = 4.6 mm, boss-hole Ø = 4.0 mm)
 *
 * Design checks:
 *   Internal cavity (assembled) : 50 × 40 × 32 mm  (req. ≥ 50 × 40 × 30 mm) ✓
 *   Wall thickness              : 2.0 mm ✓
 *   Screw stack-up              : LID_H 12 mm + INS_LEN 4 mm = 16 mm → MB-SHCS-M3-16 ✓
 *   Boss wall                   : (8 mm OD − 4 mm hole) / 2 = 2.0 mm ≥ 1.5 mm min ✓
 *   Lid clearance hole          : Ø 3.4 mm normal fit for M3 ✓
 */

// ── Dimensions ────────────────────────────────────────────────────────────────
CAV_X      = 50;
CAV_Y      = 40;
WALL       = 2.0;

BASE_CAV_Z = 22;   // base internal depth
LID_CAV_Z  = 10;   // lid  internal depth  →  assembled = 32 mm

EXT_X  = CAV_X + 2 * WALL;   // 54 mm
EXT_Y  = CAV_Y + 2 * WALL;   // 44 mm
BASE_H = WALL + BASE_CAV_Z;  // 24 mm
LID_H  = WALL + LID_CAV_Z;   // 12 mm

// ── Catalogue parameters ──────────────────────────────────────────────────────
// MB-SHCS-M3-16
SCREW_CLR_D = 3.4;   // clearance hole, normal fit
// MB-HSI-M3
INS_HOLE_D  = 4.0;   // boss bore diameter
INS_LEN     = 4.0;   // insert length

// ── Boss geometry ─────────────────────────────────────────────────────────────
// OD 8 mm → boss wall = (8 − 4) / 2 = 2.0 mm ≥ 1.5 mm min ✓
BOSS_OD = 8.0;
BOSS_R  = BOSS_OD / 2;   // 4.0 mm

// Boss centres — flush with outer walls, one per corner
BOSS_CX = EXT_X / 2 - BOSS_R;   // 27 − 4 = 23 mm
BOSS_CY = EXT_Y / 2 - BOSS_R;   // 22 − 4 = 18 mm

BOSS_POS = [
    [ BOSS_CX,  BOSS_CY ],
    [ BOSS_CX, -BOSS_CY ],
    [-BOSS_CX,  BOSS_CY ],
    [-BOSS_CX, -BOSS_CY ],
];

$fn = 64;
EPS = 0.01;

// ── Base ──────────────────────────────────────────────────────────────────────
// z = 0 … BASE_H (24 mm).  Open top.
// Heat-set inserts (MB-HSI-M3) pressed in from the top of each corner boss.
//
// NOTE: corner boss cylinders are unioned OUTSIDE the shell difference() so
// the cavity subtraction does not hollow them.  The boss fills the cavity
// corner and provides full wall thickness around the insert bore.
module base() {
    union() {
        // Outer shell: floor + four walls
        difference() {
            translate([-EXT_X/2, -EXT_Y/2, 0])
                cube([EXT_X, EXT_Y, BASE_H]);
            // Cavity — 2 mm floor retained, open at top
            translate([-CAV_X/2, -CAV_Y/2, WALL])
                cube([CAV_X, CAV_Y, BASE_CAV_Z + EPS]);
        }
        // Corner boss columns with insert bores
        for (p = BOSS_POS)
            translate([p[0], p[1], 0])
                difference() {
                    cylinder(h = BASE_H, r = BOSS_R);
                    // Insert bore: Ø 4.0 mm × 4.0 mm deep from top face
                    translate([0, 0, BASE_H - INS_LEN])
                        cylinder(h = INS_LEN + EPS, r = INS_HOLE_D / 2);
                }
    }
}

// ── Lid ───────────────────────────────────────────────────────────────────────
// z = BASE_H … BASE_H + LID_H (24 mm … 36 mm) in assembled position.
// Open bottom.  MB-SHCS-M3-16 screws enter from the top, travel 12 mm through
// the boss clearance hole, then engage 4 mm into the MB-HSI-M3 insert below.
module lid() {
    translate([0, 0, BASE_H]) {
        union() {
            // Outer shell: four walls + roof
            difference() {
                translate([-EXT_X/2, -EXT_Y/2, 0])
                    cube([EXT_X, EXT_Y, LID_H]);
                // Cavity — open bottom, 2 mm roof
                translate([-CAV_X/2, -CAV_Y/2, -EPS])
                    cube([CAV_X, CAV_Y, LID_CAV_Z + EPS]);
            }
            // Corner boss columns with screw clearance bores
            for (p = BOSS_POS)
                translate([p[0], p[1], 0])
                    difference() {
                        cylinder(h = LID_H, r = BOSS_R);
                        // Clearance hole Ø 3.4 mm through full boss height
                        translate([0, 0, -EPS])
                            cylinder(h = LID_H + 2 * EPS, r = SCREW_CLR_D / 2);
                    }
        }
    }
}

// ── Scene: two non-interfering solids in assembled position ───────────────────
// Base occupies z = 0 … 24 mm.
// Lid  occupies z = 24 … 36 mm.
// Solids share only the z = 24 mm parting plane (zero-volume contact).
base();
lid();