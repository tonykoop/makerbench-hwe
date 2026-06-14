#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WT="${WT_ROOT:-/home/tony/hwe-wt}"
mkdir -p "$HERE/handoffs"
PRE="$(cat "$HERE/assignment-preamble.txt")"
MAN="$HERE/persona-launch.generated.tsv"
printf '# persona\truntime\twork_dir\tteam\tmodel\teffort\tprompt_file\n' > "$MAN"
tail -n +2 "$HERE/persona-map.tsv" | grep -v '^#' | while IFS=$'\t' read -r persona slug remote localdir issues title; do
  [ -n "$persona" ] || continue
  for side in A B; do
    sl=$(echo "$side" | tr A-Z a-z); wt="$WT/${persona}-${sl}"; br="${persona}/${sl}-r1-${slug}"
    f="$HERE/handoffs/sprint-${persona}-${side}.md"
    if [ "$side" = "A" ]; then rt=Claude; model=Opus; pair="codex gpt-5.5"; prn="You may open your own PR via gh."
    else rt=Codex; model=gpt-5.5; pair="Claude Opus"; prn="NOTE: you cannot reach api.github.com — commit + push your branch; the MANAGER opens your PR."; fi
    { printf '%s\n\n' "$PRE"
      echo "# ${persona} (side ${side}) — Round R1 — ${title}"; echo
      echo "**Persona:** ${persona} — ${title}  ·  **Side:** ${side}  ·  **Runtime:** ${rt}  ·  **Model:** ${model}  ·  **Partner runtime:** ${pair}"
      echo "**Repo:** ${remote}  ·  **Worktree:** ${wt}  ·  **Branch:** ${br}  ·  **Issues:** ${issues}"
      echo "**Output folder (peek artifacts):** ${wt}  ·  **PR:** ${prn}"; echo
      cat "$HERE/pack.md"; echo; cat "$HERE/bodies/${persona}.md"; echo
      echo "---"; echo "Begin: cd ${wt} first, run qmd Step-0, post your plan, WAIT for manager approval, then implement and COMMIT+PUSH early."
    } > "$f"
    if [ "$side" = "A" ]; then printf '%s\tclaude\t%s\tsprint-hwe\topus\thigh\t%s\n' "$persona" "$wt" "$f" >> "$MAN"
    else printf '%s\tcodex\t%s\tsprint-hwe\tgpt-5.5\thigh\t%s\n' "$persona" "$wt" "$f" >> "$MAN"; fi
  done
done
echo "generated $(ls "$HERE/handoffs" | wc -l) handoffs + manifest"
