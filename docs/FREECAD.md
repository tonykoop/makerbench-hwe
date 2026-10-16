# Headless FreeCAD runner

The #1036 runner uses the official Linux x86_64 Python 3.11 AppImage for
FreeCAD **1.1.4**, SHA-256
`f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434`.
The distribution bundles OCCT **7.8.1**. Source:
[official release](https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.4).
There is no system FreeCAD installation, FUSE mount, display server, GUI import,
or system-package installation. Linux x86_64 and WSL2 are supported.
Execution uses the extracted `AppRun python` launcher to import `FreeCAD`, `Part`
and `MeshPart` with their bundled Python ABI; it never launches the GUI executable.

From the repository root, with the harness Python environment:

```bash
export PYTHONPATH="$PWD"
python scripts/install_freecad.py /tmp/makerbench-freecad-1.1.4
export MAKERBENCH_FREECAD_APPDIR=/tmp/makerbench-freecad-1.1.4/squashfs-root
MAKERBENCH_FREECAD_INTEGRATION=1 python -m pytest tests/test_freecad_integration.py -q
```

The installer downloads about 821 MB once, verifies the pinned SHA-256 **before
execution**, invokes `--appimage-extract`, and records the digest beside the
extracted runtime. It refuses incomplete or mismatched extractions; use a fresh
destination to recover. The digest marker identifies a trusted local extraction;
it is not an integrity check against subsequent local modification of its files.

CI runs a separate `freecad-integration` job with a checksum-keyed cache of the
extracted AppImage, without fallback cache keys. The three unit-test matrix jobs
do not install/download FreeCAD. Integration tests skip unless explicitly enabled;
when enabled, missing/broken runtime is a failure rather than a skip.

## Runner API

```python
from pathlib import Path
from makerbench.freecad_backend import run_freecad

artifacts = run_freecad(Path("entrant.py"), Path("new-output-dir"), timeout_s=180)
print(artifacts.step_path, artifacts.stl_path, artifacts.provenance_path)
```

An entrant receives `doc`, a fresh FreeCAD document. Set `result` to a valid
positive-volume `Part.Shape` or a document object with `.Shape`. Alternatively,
leave exactly one shape object in `doc`. Multiple objects require an explicit
result (e.g. a compound). Each invocation has a fresh process, document, HOME,
configuration/cache directories, and an allowlisted environment with no inherited
display or credential variables. The runner times out the process group and
publishes artifacts only after successful export. Output directories must be
absent or empty to prevent stale artifacts being mistaken for a new result.

This is a **trusted-input primitive**, not a security sandbox. It is not registered
in Arena or any grading path. Filesystem/network confinement and Arena wiring
are #1037; do not execute untrusted entrant code through this API until that
layer exists. Tests generate simple geometry; no oracle or held-out data is used.

## Export policy and determinism

STEP uses AP214IS and millimetres. STL is binary, with absolute linear deflection
**0.1 mm**, angular deflection **0.1 radians**, and `Relative=False`. The source
B-rep is copied into a fixed-name export object. Provenance records the measured
FreeCAD version/build, OCCT kernel version, pinned distribution URL/checksum,
and all export settings. STEP and STL are retained unchanged.

Determinism tests export a generated box and cylinder twice in separate processes
and scratch paths, with a delay so timestamps differ. **STL bytes must match
exactly**, including the binary header. For STEP comparison only, the first two
quoted values of the HEADER section's `FILE_NAME` record are replaced with empty
strings: field 1 (`name`) and field 2 (`time_stamp`). No other fields are stripped:
author, organization, preprocessor_version, originating_system, authorization,
FILE_DESCRIPTION, FILE_SCHEMA, every DATA byte, whitespace and line endings all
remain part of the byte-for-byte comparison. Negative controls change other
header/data fields to prove those differences remain visible.
