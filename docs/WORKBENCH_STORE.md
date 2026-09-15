# Workbench revision store (#788 W2)

`makerbench/workbench_store.py` is where the Studio design workbench keeps
its work. It never compiles or runs anything; the job runner (W3) does that
and reports back into a draft.

## Where things live

Everything is under `runs/workbench/` in the repository, which is gitignored.
Nothing is written to an arena run directory, an instrument repo, or
`private/`. Exporting a revision into an instrument repo is a separate,
confirmed step (W7), and committing it stays a human decision.

```
runs/workbench/<design_id>/
  design.json          origin, instrument, backend, title   (written once)
  index.jsonl          append-only revision index, under file_lock
  curation.jsonl       append-only picks/titles/notes; the last value wins
  revisions/<rev_id>/  source.scad|source.py, revision.json, objective.json,
                       artifacts/, job.log                   (immutable)
  drafts/<draft_id>/   source.*, draft.json, job.log, objective.json,
                       artifacts/                            (mutable, expire)
```

## Rules

- **Revisions are append-only.** A revision directory is created exclusively;
  `revision.json` is written once and there is no update or delete method.
  Saving a draft whose revision already exists is a `Conflict` that changes
  no bytes.
- **Ids are content-addressed.** `rev_id = "r-" + sha256(design, parent,
  source hash, editor kind, prompt hash, changed values)[:16]`, so the same
  draft cannot be saved twice and a sibling from the same parent gets its own
  id. `seq` is assigned under the index lock and only orders the list.
- **Saves name their parent.** A second revision from the same parent lands
  as a sibling; lineage is a tree, never last-write-wins.
- **Provenance travels with the revision.** `editor` is `human` (voter),
  `parameters` (changed values) or `model` (model id, provider, confinement
  `verified|unconfined|restricted-tools`, prompt sha256, max turns, reference
  images). The origin revision also records where the design came from
  (`trial`, `master` or `blank`). Prompts are hashed, not stored, in
  `revision.json`.
- **Drafts are mutable and expire.** Their `job` block moves through
  `queued -> running -> succeeded|failed|cancelled|interrupted`; finished
  drafts older than 24 hours are removed by `expire_drafts`; running ones
  never are.
- **Containment.** Every id must match `[a-z0-9][a-z0-9_-]{0,63}`, every
  path is resolved and must stay under the root, so `..` and symlinks cannot
  reach outside. The store's dicts carry no host-absolute paths.
- **Size caps.** Source 256 KiB, titles and notes 2 KiB, prompts and job
  errors 8 KiB. Over the cap raises `TooLarge` and writes nothing.

## Relation to the blind vote

Workbench revisions are not arena trials. They never enter `run_log.json`,
`votes.*.jsonl`, vote queues or `vote_pages/`, and the store never reads or
writes an arena run. Run discovery keys on `run_log.json`, which workbench
directories never contain.
