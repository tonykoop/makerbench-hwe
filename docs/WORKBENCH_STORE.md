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

- **Revisions are append-only.** A revision is staged completely in a
  contained scratch directory (`revisions/.staging-*`, a name no id can take)
  and published with one `rename`; `revision.json` is written once and there
  is no update or delete method. Saving a draft whose revision already exists
  is a `Conflict` that changes no bytes, and a failure before publication
  removes the staging directory, so a retry is a normal save, never a
  spurious conflict.
- **Provenance comes from the bytes saved.** The source hash in
  `revision.json` is computed from the draft's source file under the index
  lock, and the staged copy is hashed again before publication. A draft whose
  source changed after it was created is refused (`Conflict`); a draft whose
  `editor` block no longer validates is refused too.
- **Nothing is written before validation.** `create_design` and
  `create_draft` validate every input (ids, backend, title, origin, source,
  editor, parent) before creating a directory, so a rejected call leaves no
  orphan. Design ids are `d-<instrument>-<6 hex>`; a long instrument id is
  shortened to fit the 64-character rule, the random suffix never is.
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
  path is resolved and must stay under the root, and any symlink in the
  chain is refused outright, even one pointing elsewhere inside the root.
  Listings (`list_designs`, `list_drafts`, `expire_drafts`, artifact names)
  walk directory entries through the same rule and never follow a link. The
  store's dicts carry no host-absolute paths.
- **Size caps.** Source 256 KiB, titles and notes 2 KiB, prompts and job
  errors 8 KiB. Over the cap raises `TooLarge` and writes nothing.

## Relation to the blind vote

Workbench revisions are not arena trials. They never enter `run_log.json`,
`votes.*.jsonl`, vote queues or `vote_pages/`, and the store never reads or
writes an arena run. Run discovery keys on `run_log.json`, which workbench
directories never contain.
