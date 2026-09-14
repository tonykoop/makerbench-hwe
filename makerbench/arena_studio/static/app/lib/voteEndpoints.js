// Where one blind voting session reads and writes. The same vote stage serves
// a run's queue and a nightly job's morning bundle.

const enc = encodeURIComponent;

export function runVoteEndpoints(runId) {
  const base = `/api/runs/${enc(runId)}`;
  return {
    queue: (voter, skip) => `${base}/queue?voter=${enc(voter)}&skip=${skip}`,
    vote: `${base}/vote`,
    undo: `${base}/undo-vote`,
    reveal: (pairId, voter) => `${base}/judge-panel?pair_id=${enc(pairId)}&voter=${enc(voter)}`,
    doneTitle: "You've voted on every pair in this run",
    doneNote: "Votes are saved in the run's votes.blind.jsonl.",
    doneHref: `#/runs/${enc(runId)}`,
    doneLink: "Back to the run",
  };
}

// Morning bundles have no undo route: a morning vote is final once saved.
export function morningVoteEndpoints(jobId) {
  const base = `/api/morning/${enc(jobId)}`;
  return {
    queue: (voter, skip) => `${base}/pair?voter=${enc(voter)}&skip=${skip}`,
    vote: `${base}/vote`,
    undo: null,
    reveal: (pairId, voter) => `${base}/judge-panel?pair_id=${enc(pairId)}&voter=${enc(voter)}`,
    doneTitle: "You've voted on every pair in this bundle",
    doneNote: "Votes are saved in the nightly run's votes.blind.jsonl.",
    doneHref: "#/morning",
    doneLink: "Back to morning review",
  };
}
