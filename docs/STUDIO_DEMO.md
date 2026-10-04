# Read-only Studio showcase

```bash
makerbench studio --demo
```

The bundled examples are the public model/backend matchups, the anonymous
strings gallery and the kora study. The browser displays their already-published
objective metadata and PNG previews. Scoreline aggregates remain labelled by
their recorded trial counts; previews are individual examples. The gallery's
failed design remains unmeasured. This view supplies no new benchmark attestation.

Demo mode creates a separate app that has no workspace discovery, vote queue,
launcher, editor, preference report or writer services. All POST/PUT/PATCH/DELETE
requests return 403; GET endpoints that prepare voting assets or preferences are
unavailable. Local run directories, votes and source geometry are never bundled.
`--run-dir`, `--registry` and `--allow-live` cannot be combined with `--demo`.

The snapshot comes only from named committed `docs/showcase/` metadata. Check it
with `python3 scripts/build_studio_demo_data.py --check`; rebuild it without any
model call by omitting `--check`. Package metadata explicitly includes the 28
public presentation PNGs referenced by the snapshot. The hosted Space bundle is
a separate build; deployment is a maintainer action.
