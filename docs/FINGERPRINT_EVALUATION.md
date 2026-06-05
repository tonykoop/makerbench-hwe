# Canonical Geometry Fingerprint — Evaluation (issue #56) + Migration (#151)

**Status: ADOPTED.** Tony approved the recommendation; issue #151 promoted the
geometric-summary fingerprint into production `geometry.canonical_sha256` (v2) and
re-hashed the stored result rows. The evaluation below is preserved as the
rationale of record; the **Adoption & version boundary** section at the end
documents what shipped. (The standalone `fingerprint_experimental.py` prototype
that backed this evaluation was retired into production — its behavior is now
`geometry.canonical_sha256` itself, covered by `tests/test_geometry.py`.)

## 1. The record, corrected

The #56 issue body was filed assuming PR #54 shipped a **vertex-set-only** hash.
It did not. During PR #54 review that change was reverted (commit
`bc0e32b` *"Address PR #54 review: … revert hash change"*), and the connectivity
decision was documented in `9ffdd98` *"…hash contract decision (#56)"*.

**The live contract today is connectivity-aware**, and a regression test
(`tests/test_geometry.py::test_canonical_sha256_distinguishes_retriangulation`)
locks it: two triangulations of the same vertex set hash *differently* on
purpose. So the real question #56 poses is not "is vertex-set-only right?" (that
was already tried and reverted) but:

> Should the long-term contract stay **connectivity-aware** (max collision
> resistance, but the rare retriangulation flip handled by re-collecting data),
> or move to a fingerprint that is **both** triangulation-invariant **and** still
> able to separate different solids?

## 2. The current contract and its trade-off

`canonical_sha256` (geometry.py) hashes the **unique rounded vertex set + the
canonicalized face set**:

```python
v = np.round(mesh.vertices, 4)
unique, inverse = np.unique(v, axis=0, return_inverse=True)
faces = np.sort(inverse[mesh.faces], axis=1)          # remap to unique-vertex ids
faces = np.unique(faces[keep_non_degenerate], axis=0) # order-independent face set
payload = unique.tobytes() + faces.tobytes()
```

**Guarantees:** invariant to vertex emission order and to coincident vertices
that round to the same point. Maximally collision-resistant — it even separates
two triangulations of the same surface.

**The trade-off (the #56 defect):** because the *face set* is in the payload, the
digest is **not** invariant when OpenSCAD/CGAL retriangulates the same surface
across recompiles — e.g. a coplanar quad split on the opposite diagonal. Observed
once in 120 rows (`gemini-3.5-flash × enclosure_fastened × blind`): identical
vertex coordinates and counts every run, but **2 distinct face-payload hashes
across 3 recompiles**. That made CI `regrade-results` a coin-flip for that row.
Today this is handled **at the data level** — re-collecting a v2-stable source —
not by the hash. That workaround is real but fragile: it needs manual
intervention every time CGAL happens to retriangulate.

## 3. The anti-cheat backstop (why the fingerprint need not be maximal)

The fingerprint is **not** the only anti-cheat line. The public grader
independently recompiles and re-measures the submitted source
(`evaluator.evaluate()` → `render.compile_to_mesh` → geometric levels), and the
integrity policy re-grades submitted artifacts server-side. A submission cannot
pass by *claiming* geometry it does not actually produce. The fingerprint's job
is narrower: a stable, reproducible identity for "this exact submitted solid", so
a stored score can be tied to a re-derivable artifact. That means a fingerprint
which is reproducible and *reasonably* collision-resistant is sufficient — it does
not have to distinguish every possible triangulation, because the grader, not the
hash, is the geometric source of truth.

## 4. Candidates assessed

### (a) Vertex-set-only — *reverted, kept only as a baseline*
Hash the unique rounded vertex set, drop faces. Reproducible across
retriangulation, but **collides** whenever two solids share a vertex array,
regardless of how it is wired into a surface. Reverted in #54 for exactly this;
it weakens the fingerprint more than necessary.

### (b) Canonical edge set from a deterministic re-triangulation
Re-triangulate every coplanar region by a *canonical* rule (e.g. constrained
Delaunay with a fixed tie-break), then hash the resulting edge set. Topology-
preserving **and** reproducible *in principle*. In practice it is the most
expensive and riskiest option: it needs a robust coplanar-region grouping and a
deterministic triangulator, each a fresh source of cross-platform nondeterminism
(floating-point predicates, library version drift) and a heavier dependency. High
cost, high risk, marginal benefit over (c). **Not recommended.**

### (c) Geometric-summary — *recommended candidate*
Hash the unique rounded vertex set **plus surface-invariant scalars**: quantized
`|volume|`, `area`, and the rounded axis-aligned bounding box.

```python
payload = unique_rounded_vertices.tobytes()
          + np.array([round(abs(volume),4), round(area,4)]).tobytes()
          + round(bounds,4).tobytes()
```

Volume, area, and bbox are integrals/extents of the **surface itself**, so a
different triangulation of the same surface leaves them unchanged →
**triangulation-invariant / reproducible**. But two genuinely different solids
sharing a vertex array differ in area and/or volume → **collision-resistant**
where (a) is not. `abs(volume)` makes it winding/orientation-invariant. numpy
only — **no new dependency**.

## 5. Evidence

Originally measured during the #56 evaluation; the invariance/collision behavior
is now locked on production in [`tests/test_geometry.py`](../tests/test_geometry.py)
(12-char digests; ✓ = behaves as desired):

| Case | live (connectivity-aware) | vertex-set-only (a) | geometric-summary (c) | desired |
| --- | --- | --- | --- | --- |
| **1.** Retriangulation of the same watertight solid (box vs its convex hull) | differs ✗ | equal ✓ | **equal ✓** | equal (reproducible) |
| **2.** Coplanar quad split on opposite diagonals | differs ✗ | equal ✓ | **equal ✓** | equal (reproducible) |
| **3.** Different solids sharing one vertex array (cube vs tetra on the same 8 corners) | differs ✓ | **collides ✗** | **differs ✓** | differ (collision-resistant) |
| **4.** Vertex/face reorder, duplicate rounded vertex | equal ✓ | equal ✓ | equal ✓ | equal |

Cases 1–2 are the #56 nondeterminism: the live hash flips, geometric-summary is
stable. Case 3 is the reason vertex-set-only was reverted: it collides,
geometric-summary separates. **Geometric-summary is the only column that gets
every row right.**

## 6. Comparison on the four axes

| Axis | live (connectivity-aware) | vertex-set-only (a) | edge-set (b) | geometric-summary (c) |
| --- | --- | --- | --- | --- |
| **Reproducibility** (stable across recompiles) | ✗ flips on retriangulation | ✓ | ✓ (if triangulator is deterministic) | ✓ |
| **Collision-resistance** (separates different solids) | ★ maximal | ✗ vertex-array collisions | ★ topology-level | ✓ via volume/area/bbox |
| **Determinism** | ✓ | ✓ | ⚠ predicate / version sensitive | ✓ |
| **Dependency cost** | numpy | numpy | heavy (Delaunay) | **numpy** |

## 7. Recommendation

**Adopt the geometric-summary fingerprint (c) as the intended long-term contract,
to land via a deliberate, versioned hash migration — not now, and not silently.**

Rationale:
- It is the **only** candidate that is simultaneously reproducible (fixing the
  real, demonstrated `regrade-results` flip) and collision-resistant enough to
  avoid the vertex-array collisions that got (a) reverted — Pareto-dominant over
  both (a) and the live hash on the axes that matter.
- It removes the fragile "re-collect a v2-stable source by hand" workaround,
  replacing a data-level patch with a reproducible-by-construction identity.
- Zero new dependency; the scalars are already computed by trimesh during grading.
- The independent geometric grader remains the anti-cheat source of truth, so the
  small loss of discrimination versus the maximally-connectivity-aware live hash
  is backstopped (§3).

**Until that migration**, keep the live connectivity-aware `canonical_sha256`
exactly as is. Do **not** hot-swap it: the digest is embedded in every stored
result row and in `regrade-results` expectations, so any change is a
`benchmark_version`-gated migration that re-hashes all stored rows in one audited
pass (consistent with `docs/VERSIONING.md` and `docs/CONTAMINATION_RESPONSE.md`'s
"preserve-and-relabel, never silently edit"). The maintainer owns that call and
its timing; this evaluation only makes the case and ships the prototype + tests so
the decision rests on runnable evidence.

## 8. What adoption would require (for the migration PR, when chosen)

1. Promote `geometric_summary_sha256` into `geometry.py` behind a new
   `benchmark_version` (or an explicit `hash_contract` field on the row schema).
2. Re-hash all stored result rows in one pass; update `regrade-results` golden
   expectations; bump the version so old and new hashes are never compared.
3. Replace `test_canonical_sha256_distinguishes_retriangulation` with the
   reproducibility expectation (retriangulations must now hash **equally**), and
   keep the collision test (different solids must still differ).
4. Leave the geometric grader unchanged — it stays the geometric source of truth.

## 9. Reproduce

```bash
PY=/path/to/.venv/bin/python
PYTHONPATH=. $PY -m pytest -q tests/test_geometry.py   # v2 invariance + collision + version
```

## 10. Adoption & version boundary (#151 — what shipped)

The geometric-summary fingerprint is now **production** `geometry.canonical_sha256`
(`makerbench/geometry.py`), exposed as **v2** via `geometry.CANONICAL_HASH_VERSION`.

**Hash version marker.** `GradeResult.artifact_hash_version` carries the contract
version of each stored `artifact_sha256`:

| value | meaning |
| --- | --- |
| `None` | legacy **v1** — connectivity-aware (rounded vertex set + face set) |
| `2` | **v2** — geometric-summary (vertex set + `|volume|` + area + bbox) |

`evaluator.evaluate()` stamps `2` on every new grade that produces a hash.

**Stored-row migration.** `makerbench/migrate_fingerprint.py` re-hashed the live
result rows deterministically — recompiling each submitted SCAD source through the
same `evaluate()` path `regrade-results` uses, asserting the recomputed **score is
unchanged**, then writing back only `artifact_sha256` + `artifact_hash_version`
(surgical, hash-fields-only diff). Rows without a persisted scad source (legacy
empty-dossier rows) cannot be recomputed and are **left at v1** — a documented,
frozen boundary, not a silent edit.

**Archived snapshots stay frozen.** `site/data/archive/*` is immutable historical
record and is never rewritten; those boards (and any `None`-versioned row) are v1
by definition. v1 and v2 digests are never compared — the marker makes the
boundary explicit (consistent with `VERSIONING.md` and `CONTAMINATION_RESPONSE.md`'s
"preserve-and-relabel, never silently edit"). `site/data/leaderboard.json` carries
no geometry hashes, so **no score or row moved** in the migration.

To re-run the migration (idempotent — recomputing already-v2 rows reproduces the
same hashes):

```bash
PYTHONPATH=. $PY -m makerbench.migrate_fingerprint --check   # report, no writes
PYTHONPATH=. $PY -m makerbench.migrate_fingerprint           # apply
PYTHONPATH=. $PY -m makerbench.cli regrade-results --path '<changed result files>'
```
