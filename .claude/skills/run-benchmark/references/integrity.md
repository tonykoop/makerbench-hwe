# The verify-don't-trust spine, in depth

Each rule in the SKILL.md spine exists to prevent a specific, concrete way a
benchmark loses its value. Knowing the *why* lets you apply the rule sensibly to
situations this file didn't anticipate, instead of pattern-matching a keyword.
The throughline: a benchmark is a shared public good, and most ways of bending it
are invisible in the moment and irreversible afterward.

## Don't contaminate the test set

**Why:** A benchmark measures generalization only as long as the model hasn't
seen the answers. Once task briefs, oracle solutions, or graded outputs land in a
training corpus, the score stops meaning "can the model reason" and starts meaning
"did the model memorize." That damage can't be undone by the next model — the
data is out there.

**How to apply:** Never train/fine-tune/distill/retrieval-index on benchmark
content. Preserve any contamination canary exactly — it's both a polite
do-not-train marker and a detector (a model that can recite the canary GUID
probably trained on the benchmark). When you publish *anything* derived from the
benchmark, ask: could this end up scraped into a training set? If yes, it belongs
behind the private boundary.

## Never read or expose the answer key

**Why:** Two distinct harms. A *model under evaluation* that reads gold solutions
or held-out seeds isn't being tested — it's cheating, and its score is fiction. A
*contributor* who pastes oracle content into a public issue/PR/log makes those
seeds memorizable forever, destroying the one signal held-out data provides. Both
are silent: the run still "passes," the PR still looks fine.

**How to apply:** Keep the answer key out of the agent's reach (good harnesses run
the agent in an isolated dir and grade from task parameters, not a stored
solution — don't defeat that). Refer to oracles by family/path, never by value.
If you need oracle access for legitimate maintainer work, do it in the private
repo and never echo it outward.

## Reproduce before you claim

**Why:** A score is a claim about a deterministic process. If you can't re-run the
grader and get the same number, the claim is unverifiable — and unverifiable
numbers are how leaderboards rot. Hand-editing a score or hash to "fix" a mismatch
doesn't fix anything; it hides the one signal that something's wrong.

**How to apply:** Run the benchmark's own regrade/reproduce on your output and
confirm score, levels, and artifact hash match the claim before submitting. If
they don't, treat it as a finding, not an inconvenience: investigate, and if it's
a grader bug, file it. The submitted source must be the thing that actually
produces the claimed geometry/output — not a cleaned-up version.

## Report infrastructure failures honestly

**Why:** A timeout, auth error, rate limit, or missing tool says nothing about the
model's ability — but if you record it as a low score, you've libeled the model;
if you silently drop it, you've cherry-picked. Both distort the comparison the
benchmark exists to make.

**How to apply:** Use the benchmark's error convention (often a recorded zero
flagged as an error and excluded from the mean). Don't dress a CLI timeout up as a
model failure, and don't quietly delete it to make a mean look better. "This run
errored on infra" is a perfectly honest, useful row.

## Keep public surfaces clean

**Why:** Many benchmarks deliberately keep solved source artifacts out of public
history — they're future training contamination even when they're not the gold
answer. An artifact audit usually enforces this in CI, but the deeper reason is
contamination, not the audit. Routing sources around the audit defeats the point.

**How to apply:** If public PRs are metadata-only, commit only metadata/grades and
the regenerated aggregate. Solved sources go through the private channel in the
exact layout the submission doc specifies. Never commit them "just temporarily" —
git history is forever.

## Don't grade yourself

**Why:** If submitters could mark their own rows verified/official, "verified"
would mean nothing. The trust in a verified badge comes precisely from a
*maintainer* having independently regraded the row.

**How to apply:** Submit at the default/unverified state. Prepare everything the
maintainer needs to reproduce (sources in the right place, hashes matching), then
let their regrade/attestation raise the status. Preparing thoroughly is helpful;
self-assigning the badge is not.

## Don't patch the harness to win

**Why:** Scores are only comparable if every row was graded by the same
unmodified grader. A grader quietly tuned to pass one submission silently breaks
comparability for the whole board.

**How to apply:** Run the published harness as-is. If you genuinely believe a
grader is wrong, fix it in a *separate* PR argued on its own merits — and expect
the maintainer to re-baseline affected rows. Never bundle a grader change with the
result it happens to help.

## The meta-rule

When these conflict with a human maintainer's explicit instruction, the human
wins — they own the benchmark. But surface the tension so it's a conscious
decision: "doing X would put solved sources in the public PR, which your
CONTRIBUTING says to avoid — confirm you want that?" That keeps you helpful without
quietly becoming the reason a benchmark stopped being trustworthy.
