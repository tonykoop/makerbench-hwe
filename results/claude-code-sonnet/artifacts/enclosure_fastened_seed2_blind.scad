// ================================================================
//  TWO-PART 3-D-PRINTABLE ENCLOSURE
//  Internal cavity : 40 × 40 × 20 mm   Wall thickness : 2.5 mm
//  Fastened by 4 M3 SHCS into brass heat-set inserts (one per corner)
//
//  BILL OF MATERIALS
//  ----------------------------------------------------------------
//  QTY  PART NUMBER      DESCRIPTION
//    4  MB-SHCS-M3-08    M3 × 8 mm socket-head cap screw, alloy steel
//    4  MB-HSI-M3        M3 brass heat-set insert, 4.0 mm long
//  ----------------------------------------------------------------
//
//  Part-selection rationale
//    Insert MB-HSI-M3: 4.0 mm long, 4.0 mm boss-hole dia,
//      min boss wall 1.5 mm → boss OD chosen 8.0 mm
//      gives wall = (8−4)/2 = 2.0 mm ≥ 1.5 mm ✓
//    Screw MB-SHCS-M3-08: shank 8 mm = 4 mm through-lid + 4 mm
//      in insert → full insert engagement ✓
//
//  Assembly
//    1. Press MB-HSI-M3 into the 4.0 mm boss pockets at the top
//       of the base (soldering iron ~200 °C, flush-to-surface).
//    2. Set lid on base (parting plane Z = 22.5 mm).
//    3. Drive MB-SHCS-M3-08 through the 3.4 mm clearance holes in
//       the lid boss pads; thread into inserts.
// ================================================================

$fn  = 64;
EPS  = 0.01;

// ── Enclosure geometry ──────────────────────────────────────────
WALL      = 2.5;
CAV_X     = 40.0;   // internal cavity X
CAV_Y     = 40.0;   // internal cavity Y
CAV_Z     = 20.0;   // internal cavity height

EXT_X     = CAV_X + 2*WALL;   // 45 mm
EXT_Y     = CAV_Y + 2*WALL;   // 45 mm
BASE_H    = WALL  + CAV_Z;    // 22.5 mm  (floor + cavity)

// ── MB-SHCS-M3-08 ───────────────────────────────────────────────
SCREW_LEN   = 8.0;    // shank length, bearing face to tip
HEAD_DIA    = 5.5;
HEAD_H      = 3.0;
CLR_HOLE    = 3.4;    // normal-fit clearance hole for M3

// ── MB-HSI-M3 ───────────────────────────────────────────────────
INSERT_LEN  = 4.0;
BOSS_HOLE   = 4.0;    // recommended boss-hole diameter
BOSS_OD     = 8.0;    // boss wall = (8−4)/2 = 2.0 mm ≥ 1.5 mm min ✓

// ── Lid boss-pad geometry ────────────────────────────────────────
//   Total lid thickness at boss  = cbore depth + shank in lid
//     cbore depth  = HEAD_H + 0.5 mm clearance  = 3.5 mm
//     shank in lid = SCREW_LEN − INSERT_LEN      = 4.0 mm
//     total lid h  =                               7.5 mm
//   Screw bearing face sits 4.0 mm above parting plane →
//   shank descends 8 mm → tip at 4.0 mm below parting plane,
//   which equals the insert bottom (4.0 mm deep) ✓
CBORE_DIA      = HEAD_DIA + 0.5;           //  6.0 mm
CBORE_DEPTH    = HEAD_H   + 0.5;           //  3.5 mm
SHANK_IN_LID   = SCREW_LEN - INSERT_LEN;   //  4.0 mm
LID_PLATE      = WALL;                      //  2.5 mm
LID_TOTAL_H    = CBORE_DEPTH + SHANK_IN_LID; // 7.5 mm
LID_PAD_PROTO  = LID_TOTAL_H - LID_PLATE;  //  5.0 mm above plate

// ── Boss corner positions from enclosure XY centre ───────────────
//   Boss radius = 4 mm; offset = CAV_X/2 − boss_r = 16 mm
//   → boss edge tangent to inner cavity wall ✓
BOSS_R   = BOSS_OD / 2;
BOSS_OFF = CAV_X / 2 - BOSS_R;    // 16 mm

BOSS_POS = [[ BOSS_OFF,  BOSS_OFF],
            [-BOSS_OFF,  BOSS_OFF],
            [-BOSS_OFF, -BOSS_OFF],
            [ BOSS_OFF, -BOSS_OFF]];

// ================================================================
//  BASE
//   External face at Z = 0.
//   Parting plane at Z = BASE_H = 22.5 mm.
//   Corner boss pillars rise from cavity floor (Z = WALL)
//   to the parting plane; insert pockets are 4 mm deep from top.
// ================================================================
module base() {
    difference() {
        cube([EXT_X, EXT_Y, BASE_H]);
        // internal cavity — open at top
        translate([WALL, WALL, WALL])
            cube([CAV_X, CAV_Y, CAV_Z + EPS]);
    }

    // four corner boss pillars
    for (p = BOSS_POS) {
        translate([EXT_X/2 + p[0], EXT_Y/2 + p[1], WALL]) {
            difference() {
                cylinder(d = BOSS_OD, h = CAV_Z);
                // insert pocket (from boss top, depth = INSERT_LEN)
                translate([0, 0, CAV_Z - INSERT_LEN])
                    cylinder(d = BOSS_HOLE, h = INSERT_LEN + EPS);
            }
        }
    }
}

// ================================================================
//  LID
//   Rendered in assembled position: lid-local Z = 0 coincides
//   with world Z = BASE_H (parting plane).
//   Flat plate WALL thick; boss pads protrude 5 mm above plate.
//   Each boss pad has a Ø3.4 mm clearance hole through full height
//   and a Ø6.0 × 3.5 mm counterbore for the screw head at the top.
// ================================================================
module lid() {
    difference() {
        union() {
            // flat plate (full footprint)
            cube([EXT_X, EXT_Y, LID_PLATE]);
            // boss pads above plate
            for (p = BOSS_POS) {
                translate([EXT_X/2 + p[0], EXT_Y/2 + p[1], LID_PLATE])
                    cylinder(d = BOSS_OD, h = LID_PAD_PROTO);
            }
        }
        // clearance holes + counterbores (one per corner)
        for (p = BOSS_POS) {
            translate([EXT_X/2 + p[0], EXT_Y/2 + p[1], 0]) {
                // clearance hole — full height of lid at boss
                translate([0, 0, -EPS])
                    cylinder(d = CLR_HOLE, h = LID_TOTAL_H + 2*EPS);
                // counterbore for screw head (from top of boss pad)
                translate([0, 0, LID_TOTAL_H - CBORE_DEPTH])
                    cylinder(d = CBORE_DIA, h = CBORE_DEPTH + EPS);
            }
        }
    }
}

// ================================================================
//  RENDER — both parts in assembled position (non-interfering)
//   Base top face and lid bottom face share the parting plane;
//   no volumetric overlap exists between the two solids.
// ================================================================
echo("BOM: 4x MB-SHCS-M3-08 — M3 x 8 mm SHCS, alloy steel");
echo("BOM: 4x MB-HSI-M3     — M3 brass heat-set insert, 4.0 mm long");

color("SteelBlue", 0.88) base();
color("SlateGray", 0.88) translate([0, 0, BASE_H]) lid();