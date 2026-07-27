#!/usr/bin/env bash
# Admin one-liner: enable GitHub Pages once the repo is public.
#
# Run AFTER flipping repo visibility to public in GitHub Settings.
# Requires: gh CLI authenticated as repo admin (tonykoop).
set -euo pipefail

REPO="tonykoop/makerbench-hwe"

echo "=== Enable GitHub Pages — $REPO ==="
echo ""

# --- Guard: confirm repo is public -------------------------------------------

VISIBILITY="$(gh api "repos/$REPO" --jq '.visibility' 2>/dev/null || echo "unknown")"
if [[ "$VISIBILITY" != "public" ]]; then
    echo "ERROR: repo '$REPO' is '$VISIBILITY', not public." >&2
    echo "Flip visibility first: GitHub → Settings → Change visibility → Make public" >&2
    exit 1
fi
echo "Repo is public. Proceeding."

# --- Enable Pages (GitHub Actions build type) --------------------------------

echo ""
echo "--- Enabling GitHub Pages ---"

HTTP_STATUS="$(
    gh api -X POST "repos/$REPO/pages" \
        -f build_type=workflow \
        --jq '.status' \
        2>&1
)" || {
    # Pages may already be enabled — check if it exists.
    EXISTING="$(gh api "repos/$REPO/pages" --jq '.status' 2>/dev/null || echo "")"
    if [[ -n "$EXISTING" ]]; then
        echo "Pages already enabled (status: $EXISTING)."
    else
        echo "ERROR: failed to enable Pages. Check admin permissions." >&2
        exit 1
    fi
}

if [[ -n "${HTTP_STATUS:-}" ]]; then
    echo "Pages created (status: $HTTP_STATUS)."
fi

# --- Poll until built --------------------------------------------------------

echo ""
echo "--- Waiting for Pages to build ---"
echo "(Trigger a push or workflow_dispatch on pages.yml to start the first deploy.)"
echo ""
echo "To trigger manually:"
echo "  gh workflow run pages.yml -R $REPO"
echo ""

ATTEMPTS=0
MAX=20
while [[ $ATTEMPTS -lt $MAX ]]; do
    STATUS="$(gh api "repos/$REPO/pages" --jq '.status' 2>/dev/null || echo "")"
    URL="$(gh api "repos/$REPO/pages" --jq '.html_url' 2>/dev/null || echo "")"
    echo "  [$ATTEMPTS/$MAX] status: ${STATUS:-pending}"
    if [[ "$STATUS" == "built" ]]; then
        echo ""
        echo "=== Pages live ==="
        echo "  URL: $URL"
        break
    fi
    ATTEMPTS=$((ATTEMPTS + 1))
    sleep 15
done

if [[ $ATTEMPTS -eq $MAX ]]; then
    echo ""
    echo "INFO: Pages not yet built after ${MAX} polls. Check workflow run:"
    echo "  gh run list -R $REPO --workflow pages.yml"
fi

echo ""
echo "Next steps:"
echo "  - Visit the Pages URL above to confirm the leaderboard is live."
echo "  - The deploy-pages workflow auto-triggers on any push to main"
echo "    touching site/, results/, or tasks/registry.json."
