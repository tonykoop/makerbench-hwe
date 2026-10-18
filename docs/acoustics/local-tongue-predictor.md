# Local wood tongue-drum acoustic estimate (#1113)

Manager decision: use local Euler–Bernoulli finite elements over geometry-derived
width profiles. The library's public Wolfram widget is not a geometry-to-pitch
API. The optional Wolfram interface is a deterministic skipped stub. There are
no network calls, credentials, model runs or cloud deployments in this path.
This implements the revised #1126/#1127 scope. #1128 (grader/card integration)
and #1129 (measured tolerance study) wait until these foundations merge.

## Run a separate check

Use the pinned FreeCAD runtime from [FREECAD.md](../FREECAD.md), the normal
harness dependencies (NumPy, SciPy, Shapely), and a read-only source copied with
its linked `parts/` directory into ignored `runs/` storage. From the repository:

```bash
export MAKERBENCH_FREECAD_APPDIR=/tmp/mb-freecad-1.1.4/squashfs-root
python -m makerbench.acoustic_tongues runs/acoustics/input/assembly.FCStd \
  --root-band-mm 80 --species white_oak padauk sapele \
  --cache runs/acoustics/wolfram-stub-cache --out runs/acoustics/estimate.json
```

Input can also be the saved top-panel `.FCStd` or a standalone `.brep`/`.brp`.
`--panel-link` selects the assembly's native link (default `Top`); `--object`
selects the saved panel object (default `PanelBody`). Supported plates have a
uniform measured 12 mm thickness in their local XY plane, through kerfs and
single-root tongues at either local X end. Assembly translations/rotations do
not alter these panel-local measurements. Scaled links are refused. An arbitrary
rotated standalone BREP must be put in the supported local frame first.

The reader follows only the selected supplied panel link, validates native ZIP
and XML with the existing bounded native-document policy, then reads its saved
BREP. It does not execute native objects, recompute the document or treat its
dimension table as measurements. Unsaved/recompute-pending edits are therefore
outside the evidence. A no-network, resource-limited FreeCAD sandbox validates
the solid, rejects blind cuts/variable thickness, and discretizes the bottom
footprint with 0.02 mm deflection. No source geometry is published.

`--root-band-mm` is required: it places rigid clamp planes inward from each end.
For the traced fixture the existing geometry tooling uses an 80 mm end band;
that is a modelling assumption, not a measured vibration node or experimentally
validated clamp. The footprint is clipped at those planes; components touching
one plane and neither side edge are tongues. Wood joining both planes is frame.
Free length runs from the assumed clamp plane to the furthest tongue tip.
Tongue IDs sort by transverse root location, measured at 5 mm from the clamp
(or 10% of free length for short tongues), then side; they are not target-note IDs.

Default: 160 equal elements per tongue. Each midpoint supplies the total wood
width intersecting a transverse slice of that tongue. Two of the real curved
heads have split slices: the 1-D reduction adds interval widths and assumes one
out-of-plane displacement across them. It does not model lateral/torsional head
modes. Output retains widths, element edges, section multiplicity, dimensions,
native/BREP SHA-256 provenance and FreeCAD version. Neck width is the minimum
sampled width in the proximal 40%; head width is the maximum in the distal 55%.
Both definitions are reported explicitly; tip/edge detail below the sampling
resolution is not represented.

The solver assembles cubic Hermite stiffness and consistent mass matrices with
`I(x)=b(x)t^3/12`, `A(x)=b(x)t`, removes the two root DOFs, and solves the lowest
positive generalized eigenvalue. The tip has natural zero-moment/zero-shear
boundary conditions. Dimensions enter in mm and convert to SI for the solve.
See the [TU Delft beam derivation](https://teachbooks.tudelft.nl/computational-modelling/structural_linear/euler_bernouilli.html).

## Material assumptions and citations

These are longitudinal species-average static moduli at 12% moisture content,
not measurements of Tony's stock. Grain is assumed parallel to the tongue axis.
Do not interpret the extra reported decimals as material or tuning precision.

| CLI species | Scientific name | E (GPa) | Density (kg/m³) | Source |
| --- | --- | ---: | ---: | --- |
| `white_oak` | Quercus alba | 12.30 | 761.6 | [USDA Wood Handbook, FPL-GTR-282, Table 5-3a](https://www.fpl.fs.usda.gov/documnts/fplgtr/fplgtr282/fpl_gtr282.pdf) |
| `padauk` | Pterocarpus soyauxii / P. osun | 15.87 | 790 | [CIRAD/ITTO Tropical Timber Atlas, African padauk sheet](https://www.itto.int/files/itto_project_db_input/3028/Technical/E-TMT-SDP-010-12-R1-M-Tropical%20Timber%20Atlas.pdf), also [ATIBT species table](https://www.mytropicaltimber.org/en/products/74/padouk-dafrique) |
| `sapele` | Entandrophragma cylindricum | 13.96 | 690 | [ATIBT/CIRAD Sapelli species table](https://www.mytropicaltimber.org/en/products/81/sapelli) |

USDA's white-oak row supplies E = 12,300 MPa and specific gravity 0.68 based on
oven-dry mass and volume at 12% moisture. Density is **derived**, adding moisture
mass: `rho12 = 1000 * 0.68 * (1 + 12/100) = 761.6 kg/m³`.
The mass/volume basis is explained in [USDA's specific-gravity report](https://www.fpl.fs.usda.gov/documnts/fplgtr/fplgtr76.pdf).
No shear correction or dynamic-modulus calibration is applied to the tabulated E.

## Validation and limits

Focused tests compare uniform cantilevers across species, lengths, widths and
thicknesses at 40 and 160 elements with the requested independent formula:

`f1 = 1.875^2 / (2*pi*L^2) * sqrt(E*I/(rho*A))`.

Acceptance: relative error below 1%. The 160-element white-oak/padauk/sapele
200 mm control differs by at most 0.012% (including rounding beta to 1.875).
Tests also cover length/thickness/E/density scaling, uniform-width cancellation,
a wider tip lowering frequency, refinement of a stepped-width beam, dimension
extraction/translation, unsafe links, missing inputs and deterministic stub-cache
reuse/separation/corruption recovery. Opt-in generated native controls exercise
BREP intake and reject blind cuts and non-12-mm plates.

```bash
OPENBLAS_NUM_THREADS=1 MAKERBENCH_ACOUSTIC_INTEGRATION=1 \
python -m pytest -q tests/test_acoustic_beam.py tests/test_acoustic_tongues.py
```

This verifies the solver and geometry path, not acoustic accuracy on a real
instrument. Frame compliance, anisotropy/grain deviations, board-specific dynamic
E/density, shear/rotary inertia, shell/cavity coupling and damping are omitted.
The shape reconstruction and assumed clamp location introduce additional
uncertainty. Physical note measurements and tolerance calibration remain #1129.
No target mapping was supplied, so target Hz and cents-from-target are null;
nearest-note/cents-from-nearest is a frequency label, never a target error.

The JSON always names this separate `acoustic_prediction` check and excludes it
from base checks and published rates. It has no pass/fail tuning threshold.
Unsupported geometry or missing runtime/input yields `status: skipped` with a
reason and no predictions. The optional disk cache keys the stub model/version
and all supplied parameters/units; its response remains `is_stub: true`, skipped,
and is never evidence of Wolfram execution. The five base checks, published
rates, Morning Review card and disabled nightly schedule are untouched.
