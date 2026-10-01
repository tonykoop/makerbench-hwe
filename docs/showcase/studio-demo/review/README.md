# Studio demo: S10 visual review

Eight full-page Chromium captures of the read-only demo, at desktop width
1366 px (900 px viewport height) and phone width 390 px (844 px viewport
height), using light mode and a device scale factor of one. Tony approves the
look before publishing. These files are a local review pack, not a deployment.

| View | Desktop | Phone |
| --- | --- | --- |
| Comparing AI models | [1366 px](s10/post3-models-1366.png) | [390 px](s10/post3-models-390.png) |
| Comparing CAD tools and scoring correction | [1366 px](s10/post3-backends-1366.png) | [390 px](s10/post3-backends-390.png) |
| String-instrument gallery | [1366 px](s10/strings-gallery-1366.png) | [390 px](s10/strings-gallery-390.png) |
| Kora: text brief and reference photo | [1366 px](s10/kora-1366.png) | [390 px](s10/kora-390.png) |

The [manifest](s10/manifest.json) records screenshot hashes, browser versions,
viewport sizes, the capture's base commit and hashes of every served demo
input. The captured runtime is the clean #953 implementation. The manifest's
`source_tree_dirty` flag is true because the new capture script had not yet
been committed; screenshot/tool additions do not change those runtime inputs.
The final PR must retain the same input hashes. This preserves the distinction
between the commit used for capture and the later commit containing its images.

To reproduce from a clone with Python dependencies installed:

```sh
pip install -e ".[studio]"
python -m pip install playwright
python -m playwright install chromium
PYTHONPATH=. python scripts/capture_studio_demo_review.py --out /tmp/studio-demo-review-new
```

Use a fresh output directory. The script launches only the separate read-only
demo on loopback, checks every view's heading and card count, waits for the
server version, fonts and images, refuses overflow, browser errors or external
requests, and stops its own server afterward. It does not discover private
workspaces, run CAD, call a model, vote or upload anything. If a capture fails,
its output is incomplete and must not be presented as a finished review pack.
