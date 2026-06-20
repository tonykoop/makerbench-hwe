"""build_inspect payload contract tests (mb#107 run-viewer).

Tests for the pure ``build_inspect(mesh_manifest, registry, benchmark_version)``
function in ``site/build_data.py``.  The function returns a submission-only
inspect payload — no oracle geometry is ever included.

If ``build_inspect`` has not landed yet, the import will raise ``AttributeError``
and every test will be collected but fail at the call site.  That is expected
while the concurrent implementation agent is working.  Syntax correctness can
still be verified with ``python3 -m py_compile`` even before that landing.
"""

from __future__ import annotations

from html.parser import HTMLParser
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "makerbench_site_build_data",
    ROOT / "site" / "build_data.py",
)
build_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_data)

build_inspect = build_data.build_inspect  # type: ignore[attr-defined]


class _HeadMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = attrs_dict.get("name") or attrs_dict.get("property")
            if key:
                self.meta[key] = attrs_dict.get("content", "")
        elif tag == "link" and attrs_dict.get("rel"):
            self.links[attrs_dict["rel"]] = attrs_dict.get("href", "")

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _entry(
    task_id: str,
    *,
    model_identifier: str = "demo-model",
    track: str = "blind",
    seed: int = 0,
    score: int = 4,
    mesh: str = "assets/meshes/demo.stl",
    face_count: int = 1200,
    mass_g: float | None = 34.56,
    min_wall_mm: float | None = 1.997,
    bbox_mm: float | None = 54.0,
    source_sha256: str = "abc123",
    dfm_min_wall_mm: float | None = None,
    interference_zones: list | None = None,
) -> dict:
    """Construct a realistic by_task entry matching the meshes.json shape."""
    entry: dict = {
        "task_id": task_id,
        "model_identifier": model_identifier,
        "track": track,
        "seed": seed,
        "score": score,
        "mesh": mesh,
        "face_count": face_count,
        "quality": {
            "mass_g": mass_g,
            "min_wall_mm": min_wall_mm,
            "bbox_mm": bbox_mm,
        },
        "source_sha256": source_sha256,
    }
    if dfm_min_wall_mm is not None:
        entry["dfm_min_wall_mm"] = dfm_min_wall_mm
    if interference_zones is not None:
        entry["interference_zones"] = interference_zones
    return entry


def _manifest(entries: list[dict]) -> dict:
    """Build a by_task/by_row manifest from a list of entries."""
    by_task: dict[str, dict] = {}
    by_row: dict[str, list] = {}
    for e in entries:
        tid = e["task_id"]
        if tid not in by_task:
            by_task[tid] = e
        rid = e.get("row_id", "")
        if rid:
            by_row.setdefault(rid, []).append(e)
    return {"by_task": by_task, "by_row": by_row}


_REGISTRY = {"task_families": [{"id": "vented_plate", "title": "Vented plate", "tracks": ["blind"]}]}


# ---------------------------------------------------------------------------
# 1. Schema / version / benchmark_version round-trip + _generated provenance
# ---------------------------------------------------------------------------

def test_envelope_schema_and_version():
    manifest = _manifest([_entry("enclosure_fastened")])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")

    assert result["schema"] == "makerbench-inspect-v1"
    assert result["version"] == 1
    assert result["benchmark_version"] == "0.1.0"


def test_benchmark_version_passes_through_verbatim():
    manifest = _manifest([_entry("vented_plate")])
    result = build_inspect(manifest, _REGISTRY, "2.3.4-rc1")
    assert result["benchmark_version"] == "2.3.4-rc1"


def test_inspect_page_seo_points_at_published_hwe_viewer():
    """The #107 Inspect-a-Run page must share the published hwe URL."""
    parser = _HeadMetadataParser()
    parser.feed((ROOT / "site" / "inspect.html").read_text(encoding="utf-8"))

    page_url = "https://tonykoop.github.io/makerbench-hwe/inspect.html"

    assert "MakerBench" in parser.title
    assert "Inspect a Run" in parser.title
    assert "submitted 3D artifacts" in parser.meta["description"]
    assert "Inspect a Run" in parser.meta["og:title"]
    assert parser.meta["og:type"] == "website"
    assert parser.meta["og:url"] == page_url
    assert parser.links["canonical"] == page_url
    assert "makerbench/inspect.html" not in parser.meta["og:url"]


def test_generated_mentions_submission_and_no_oracle():
    manifest = _manifest([_entry("vented_plate")])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    generated = result["_generated"].lower()
    # Must mention provenance (submission-only / no oracle)
    assert "submission" in generated or "submission-only" in generated
    assert "oracle" in generated or "no oracle" in generated
    # Must also reference the issue that created this feature
    assert "107" in result["_generated"] or "mb#107" in result["_generated"].lower()


# ---------------------------------------------------------------------------
# 2. Empty manifest → empty artifacts list + valid envelope
# ---------------------------------------------------------------------------

def test_empty_manifest_yields_empty_artifacts():
    empty = {"by_task": {}, "by_row": {}}
    result = build_inspect(empty, _REGISTRY, "0.1.0")

    assert result["artifacts"] == []
    # Envelope keys must still be present
    assert result["schema"] == "makerbench-inspect-v1"
    assert result["version"] == 1
    assert "_generated" in result


# ---------------------------------------------------------------------------
# 3. One synthetic entry → one artifact with all required keys / correct values
# ---------------------------------------------------------------------------

_REQUIRED_ARTIFACT_KEYS = {
    "id", "task_id", "label", "model_identifier", "track", "seed", "score",
    "mesh", "face_count", "dfm_min_wall_mm", "min_wall_mm", "interference_zones",
    "metrics", "source_sha256",
}


def test_single_entry_produces_one_artifact():
    entry = _entry(
        "enclosure_fastened",
        model_identifier="antigravity-gemini-3-flash",
        track="perception",
        seed=1,
        score=4,
        mesh="assets/meshes/enclosure_fastened.stl",
        face_count=2712,
        source_sha256="3ed4f0965abdc6e7cb",
    )
    manifest = _manifest([entry])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")

    assert len(result["artifacts"]) == 1
    art = result["artifacts"][0]
    assert _REQUIRED_ARTIFACT_KEYS.issubset(art.keys()), (
        f"Missing keys: {_REQUIRED_ARTIFACT_KEYS - set(art.keys())}"
    )


def test_artifact_task_id_and_label_humanization():
    """task_id passes through; label is "_" → " " title-cased."""
    entry = _entry("enclosure_fastened")
    manifest = _manifest([entry])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    art = result["artifacts"][0]

    assert art["task_id"] == "enclosure_fastened"
    assert art["label"] == "Enclosure Fastened"


def test_artifact_label_multi_word():
    entry = _entry("laser_tab_slot_panel")
    manifest = _manifest([entry])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    art = result["artifacts"][0]
    assert art["label"] == "Laser Tab Slot Panel"


def test_artifact_mesh_passthrough():
    mesh_path = "assets/meshes/enclosure_fastened.stl"
    entry = _entry("enclosure_fastened", mesh=mesh_path)
    manifest = _manifest([entry])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    art = result["artifacts"][0]
    assert art["mesh"] == mesh_path


def test_artifact_min_wall_mm_from_quality():
    entry = _entry("vented_plate", min_wall_mm=2.499)
    manifest = _manifest([entry])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    assert result["artifacts"][0]["min_wall_mm"] == 2.499


def test_artifact_min_wall_mm_none_when_absent():
    entry = _entry("vented_plate", min_wall_mm=None)
    manifest = _manifest([entry])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    assert result["artifacts"][0]["min_wall_mm"] is None


def test_artifact_model_identifier_and_track_and_seed_and_score():
    entry = _entry(
        "vented_plate",
        model_identifier="antigravity-gemini-3-flash",
        track="blind",
        seed=0,
        score=4,
    )
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]

    assert art["model_identifier"] == "antigravity-gemini-3-flash"
    assert art["track"] == "blind"
    assert art["seed"] == 0
    assert art["score"] == 4


def test_artifact_source_sha256_passthrough():
    sha = "3ed4f0965abdc6e7cb0e81928b981400585309f407859aa3e4e7f3db0572962e"
    entry = _entry("vented_plate", source_sha256=sha)
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    assert art["source_sha256"] == sha


# ---------------------------------------------------------------------------
# 4. metrics formatting
# ---------------------------------------------------------------------------

def test_metrics_four_rows_correct_format():
    """All four metric rows render in expected label/value format."""
    entry = _entry(
        "enclosure_fastened",
        mass_g=34.56,
        min_wall_mm=1.997,
        bbox_mm=54.0,
        face_count=2712,
    )
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    metrics = art["metrics"]

    assert len(metrics) == 4
    by_label = {m["label"]: m["value"] for m in metrics}

    assert by_label["Mass"] == "34.56 g"
    assert by_label["Min wall"] == "2.00 mm"
    assert by_label["Bounding box"] == "54.0 mm"
    assert by_label["Triangles"] == "2712"

    # Order: Mass, Min wall, Bounding box, Triangles
    assert [m["label"] for m in metrics] == ["Mass", "Min wall", "Bounding box", "Triangles"]


def test_metrics_each_row_has_label_and_value_keys():
    entry = _entry("vented_plate", mass_g=10.0, min_wall_mm=2.0, bbox_mm=30.0, face_count=500)
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    for row in art["metrics"]:
        assert "label" in row and "value" in row


def test_metrics_missing_bbox_omits_bounding_box_row():
    """bbox_mm=None must omit the Bounding box metric row entirely."""
    entry = _entry("vented_plate", mass_g=10.63, min_wall_mm=2.499, bbox_mm=None, face_count=1196)
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    labels = [m["label"] for m in art["metrics"]]
    assert "Bounding box" not in labels
    # The other three rows are still present
    assert "Mass" in labels
    assert "Min wall" in labels
    assert "Triangles" in labels


def test_metrics_missing_mass_omits_mass_row():
    entry = _entry("vented_plate", mass_g=None, min_wall_mm=2.0, bbox_mm=30.0, face_count=300)
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    labels = [m["label"] for m in art["metrics"]]
    assert "Mass" not in labels


def test_metrics_missing_min_wall_omits_min_wall_row():
    entry = _entry("vented_plate", mass_g=5.0, min_wall_mm=None, bbox_mm=20.0, face_count=100)
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    labels = [m["label"] for m in art["metrics"]]
    assert "Min wall" not in labels


def test_metrics_empty_when_all_quality_missing():
    entry = _entry("vented_plate", mass_g=None, min_wall_mm=None, bbox_mm=None, face_count=None)
    # Override face_count manually since _entry always sets it to int; set to non-int
    entry["face_count"] = None
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    assert art["metrics"] == []


# ---------------------------------------------------------------------------
# 5. Determinism: out-of-order insertion → sorted by task_id ascending
# ---------------------------------------------------------------------------

def test_artifacts_sorted_by_task_id():
    entries = [
        _entry("vented_plate"),
        _entry("enclosure_fastened"),
        _entry("laser_tab_slot_panel"),
    ]
    manifest = _manifest(entries)
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    task_ids = [a["task_id"] for a in result["artifacts"]]
    assert task_ids == sorted(task_ids)


def test_artifacts_sort_is_deterministic_regardless_of_dict_order():
    """Two calls with entries in different insertion order yield identical output."""
    e1 = _entry("zz_task")
    e2 = _entry("aa_task")

    manifest_a = {"by_task": {"zz_task": e1, "aa_task": e2}, "by_row": {}}
    manifest_b = {"by_task": {"aa_task": e2, "zz_task": e1}, "by_row": {}}

    result_a = build_inspect(manifest_a, _REGISTRY, "0.1.0")
    result_b = build_inspect(manifest_b, _REGISTRY, "0.1.0")

    assert [a["task_id"] for a in result_a["artifacts"]] == ["aa_task", "zz_task"]
    assert result_a["artifacts"] == result_b["artifacts"]


# ---------------------------------------------------------------------------
# 6. interference_zones defaults to [] when entry has none
# ---------------------------------------------------------------------------

def test_interference_zones_default_to_empty_list():
    entry = _entry("vented_plate")
    # Ensure interference_zones is not in the entry
    entry.pop("interference_zones", None)
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    assert art["interference_zones"] == []


def test_interference_zones_passthrough_when_present():
    zones = [{"center": [1.0, 2.0, 3.0], "radius": 0.5}]
    entry = _entry("vented_plate", interference_zones=zones)
    manifest = _manifest([entry])
    art = build_inspect(manifest, _REGISTRY, "0.1.0")["artifacts"][0]
    assert art["interference_zones"] == zones


# ---------------------------------------------------------------------------
# 7. PUBLIC-DATA guard: no oracle in artifact values; pure function (no FS reads)
# ---------------------------------------------------------------------------

def test_payload_json_contains_no_oracle_in_artifact_values():
    """No artifact field value should contain the string 'oracle'."""
    entry = _entry(
        "enclosure_fastened",
        mesh="assets/meshes/enclosure_fastened.stl",
        source_sha256="deadbeef",
    )
    manifest = _manifest([entry])
    result = build_inspect(manifest, _REGISTRY, "0.1.0")

    # No artifact value should mention 'oracle'
    for art in result["artifacts"]:
        for key, val in art.items():
            if isinstance(val, str):
                assert "oracle" not in val.lower(), (
                    f"Artifact field {key!r} contains 'oracle': {val!r}"
                )


def test_build_inspect_is_pure_no_filesystem_access(tmp_path):
    """build_inspect works from synthetic inputs with no real file present.

    The function is declared pure — it only uses its arguments and must never
    attempt to open the mesh path or any other file.  We pass a fake mesh path
    that definitely does not exist on disk; if the function tried to read it
    the test would blow up with FileNotFoundError.
    """
    fake_mesh = str(tmp_path / "nonexistent" / "fake_part.stl")
    entry = _entry("vented_plate", mesh=fake_mesh)
    manifest = _manifest([entry])

    # This must not raise even though fake_mesh does not exist
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0]["mesh"] == fake_mesh


def test_build_inspect_does_not_touch_private_oracles_dir():
    """A canary: the private/oracles dir must never be read by build_inspect."""
    manifest = _manifest([_entry("vented_plate", mesh="assets/meshes/vented_plate.stl")])
    # Call succeeds without accessing any private path
    result = build_inspect(manifest, _REGISTRY, "0.1.0")
    # Confirm the artifact mesh is the submitted path, never redirected
    assert "private" not in result["artifacts"][0]["mesh"]
    assert "oracle" not in result["artifacts"][0]["mesh"]
