// ============================================================
//  Two-Part 3D-Printable Enclosure  –  Units: mm
//
//  Internal cavity : 70 × 70 × 20 mm (clear internal void)
//  Wall thickness  : 2.5 mm on all six faces
//
//  BOM
//  ─────────────────────────────────────────────────────────
//  Qty  Part #          Description
//    4  MB-SHCS-M3-06   M3 × 6 mm Socket Head Cap Screw,
//                       alloy steel, hex-socket drive;
//                       head ⌀ 5.5 mm × 3.0 mm H,
//                       under-head length 6 mm.
//                       Clearance hole: ⌀ 3.4 mm (normal fit).
//    4  MB-HSI-M3       M3 Brass Heat-Set Insert,
//                       L = 4.0 mm, OD = 4.6 mm.
//                       Boss bore ⌀ 4.0 mm per catalog.
//                       Min boss wall 1.5 mm per catalog →
//                       boss OD = 4.0 + 2×1.5 = 7.0 mm.
// ============================================================
//
//  Fastener geometry check
//  ─────────────────────────────────────────────────────────
//  Lid thickness          : 4.0 mm
//    Counterbore (top)    : ⌀ 6.0 mm × 3.0 mm deep
//    Through clearance    : ⌀ 3.4 mm × 1.0 mm deep (below cbore)
//  Screw path under head  : 1.0 mm (lid) + 4.0 mm (insert) = 5.0 mm
//    MB-SHCS-M3-06 under-head 6 mm ≥ 5.0 mm  ✓
//    Remaining 1.0 mm → tip-clearance pocket in boss bore.
//  Boss bore depth        : 4.0 mm (insert) + 1.5 mm (tip) = 5.5 mm
//    Boss-bore bottom Z   : 22.5 – 5.5 = 17.0 mm (above floor @ 2.5 mm) ✓
// ============================================================

$fn = 64;
EPS  = 0.01;   // anti-z-fight epsilon

// ── Enclosure parameters ─────────────────────────────────
WALL   = 2.5;
FLOOR  = 2.5;
CX     = 70;              // internal cavity X
CY     = 70;              // internal cavity Y
CZ     = 20;              // internal cavity Z
EX     = CX + 2*WALL;     // 75 mm exterior X
EY     = CY + 2*WALL;     // 75 mm exterior Y
BASE_H = CZ + FLOOR;      // 22.5 mm base exterior height

LID_H  = 4.0;             // lid thickness (3 mm cbore + 1 mm through-zone)

// ── MB-SHCS-M3-06 (screw) ────────────────────────────────
CLR_HOLE  = 3.4;   // M3 normal-fit clearance hole
CBORE_DIA = 6.0;   // counterbore dia (⌀5.5 head + 0.5 slip)
CBORE_DEP = 3.0;   // counterbore depth = head height

// ── MB-HSI-M3 (insert) ───────────────────────────────────
INS_HOLE_D = 4.0;   // boss bore ⌀ per catalog
INS_L      = 4.0;   // insert length
MIN_BWALL  = 1.5;   // min boss wall per catalog
BOSS_OD    = INS_HOLE_D + 2*MIN_BWALL;   // 7.0 mm
BOSS_H     = BASE_H;                      // boss top flush with base rim
BOSS_BORE  = INS_L + 1.5;                // 5.5 mm: insert + tip clearance

// ── Boss XY centres ───────────────────────────────────────
//  Place each boss tangent to both inner corner walls:
//    offset from outer face = WALL + BOSS_OD/2 = 2.5 + 3.5 = 6.0 mm
//  Gap from boss edge to inner wall face = BOSS_OD/2 – WALL = 1.0 mm ≥ 0
//  Gap from insert hole to boss outer wall = MIN_BWALL = 1.5 mm ✓
BOSS_OFF = WALL + BOSS_OD / 2;   // 6.0 mm from each outer face
BX = [BOSS_OFF, EX - BOSS_OFF];  // [6.0, 69.0]
BY = [BOSS_OFF, EY - BOSS_OFF];

// ── Echo manifest ────────────────────────────────────────
echo("=== ENCLOSURE BOM ===");
echo("4x  MB-SHCS-M3-06   M3 x 6 mm Socket Head Cap Screw (alloy steel)");
echo("4x  MB-HSI-M3        M3 Brass Heat-Set Insert  L=4mm OD=4.6mm");
echo(str("Base exterior  : ", EX, " x ", EY, " x ", BASE_H, " mm"));
echo(str("Lid  exterior  : ", EX, " x ", EY, " x ", LID_H,  " mm"));
echo(str("Internal cavity: ", CX, " x ", CY, " x ", CZ, " mm (clear void)"));
echo(str("Boss OD / bore : ", BOSS_OD, " mm / ", INS_HOLE_D, " mm dia x ",
         BOSS_BORE, " mm deep"));

// ============================================================
// MODULE  base
//   Origin: bottom-left-front outer corner.
//   Z = 0 … BASE_H = 22.5 mm
// ============================================================
module base() {
    // Hollow shell: outer cube minus interior cavity
    difference() {
        cube([EX, EY, BASE_H]);
        translate([WALL, WALL, FLOOR])
            cube([CX, CY, CZ + EPS]);   // full 70×70×20 clear void
    }
    // Corner boss columns with blind insert bores.
    // Added after shell difference → columns fill cavity corners.
    for (bx = BX) for (by = BY)
        translate([bx, by, 0])
            difference() {
                cylinder(d = BOSS_OD,    h = BOSS_H);
                // blind bore open to top: insert pressed from top after print
                translate([0, 0, BOSS_H - BOSS_BORE])
                    cylinder(d = INS_HOLE_D, h = BOSS_BORE + EPS);
            }
}

// ============================================================
// MODULE  lid
//   Origin: bottom-left-front; placed via translate([0,0,BASE_H]).
//   Z (local) = 0 … LID_H = 4.0 mm
//   Counterbore opens on the TOP face (local Z = LID_H).
// ============================================================
module lid() {
    difference() {
        cube([EX, EY, LID_H]);
        for (bx = BX) for (by = BY) {
            // Counterbore for screw head – open to top
            translate([bx, by, LID_H - CBORE_DEP])
                cylinder(d = CBORE_DIA, h = CBORE_DEP + EPS);
            // Clearance through-hole – 1 mm zone below counterbore
            translate([bx, by, -EPS])
                cylinder(d = CLR_HOLE, h = LID_H - CBORE_DEP + 2*EPS);
        }
    }
}

// ============================================================
// Render – assembled position (non-interfering)
//
//   base:  Z =  0.0 … 22.5 mm
//   lid:   Z = 22.5 … 26.5 mm   (rests on base rim; faces touch, no overlap)
//
//   Screw axis path (global Z, per corner):
//     26.5  – screw head top (flush with lid top surface)
//     23.5  – head bottom / cbore floor      (cbore 3 mm deep)
//     22.5  – lid bottom / base-rim plane    (1 mm clear hole below cbore)
//     22.5  – boss top / insert top
//     18.5  – insert bottom                  (4 mm insert)
//     17.5  – screw tip                      (0.5 mm above bore bottom)
//     17.0  – boss bore bottom               (5.5 mm deep blind hole)
// ============================================================
color("SteelBlue",      0.9) base();
color("LightSlateGray", 0.9) translate([0, 0, BASE_H]) lid();