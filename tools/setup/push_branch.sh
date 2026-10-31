#!/usr/bin/env bash
# Publish the current reconstruction branch and prove it landed.
#
# Why this exists: `git push` from an automated session was repeatedly reported
# as "no GitHub credentials" with no way to tell a missing grant from a bad
# remote. The Paperclip runtime wraps `git` (see the wrapper at
# $PATH/paperclip-github-runtime/*/git): it strips GH_TOKEN/GITHUB_TOKEN/
# SSH_AUTH_SOCK from the environment, disables interactive prompts, and asks the
# control plane for managed credentials only when PAPERCLIP_GITHUB_BROKER_TOKEN
# is present. When that token is absent the wrapper prints
# "GitHub capability_missing" and every push fails at authentication.
#
# So there are exactly two supported paths, and this script reports which one
# applies instead of guessing:
#   1. managed  - run inside a Paperclip session that holds GitHub capability;
#                 the wrapper brokers the credential for this invocation.
#   2. manual   - run from the maintainer's own terminal (not through the
#                 wrapper) with GH_TOKEN exported, or with `gh auth login` done.
#
# It never fabricates a credential and never rewrites history: only a plain
# fast-forward push of the current branch, then a remote-SHA read-back.
set -uo pipefail
cd "$(dirname "$0")/../.."

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
REMOTE="${NOVA3_PUSH_REMOTE:-origin}"

if [ "$BRANCH" = "HEAD" ] || [ "$BRANCH" = "main" ]; then
    echo "push_branch: refusing to push detached HEAD or main (branch=$BRANCH)" >&2
    exit 2
fi

local_sha=$(git rev-parse "$BRANCH") || exit 2
dirty=$(git status --porcelain | wc -l)

echo "branch:  $BRANCH"
echo "local:   $local_sha"
echo "remote:  $(git remote get-url "$REMOTE" 2>/dev/null || echo '<missing>')"
echo "dirty:   $dirty path(s)"

if ! git ls-remote "$REMOTE" HEAD >/dev/null 2>&1; then
    echo "push_branch: cannot reach $REMOTE (network, remote URL, or repository visibility)" >&2
    exit 4
fi

if [ -z "${PAPERCLIP_GITHUB_BROKER_TOKEN:-}${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]; then
    echo "push_branch: no GitHub credential source in this environment." >&2
    echo "  PAPERCLIP_GITHUB_BROKER_TOKEN, GH_TOKEN and GITHUB_TOKEN are all unset." >&2
    echo "  Remediation (maintainer, one of):" >&2
    echo "    * grant the GitHub connection to this agent/run in Paperclip so the runtime" >&2
    echo "      wrapper can broker credentials, then re-run: bash tools/setup/push_branch.sh" >&2
    echo "    * or run this script from your own terminal (outside the Paperclip wrapper)" >&2
    echo "      with GH_TOKEN=<token> exported, or after 'gh auth login'." >&2
    exit 3
fi

echo "pushing $BRANCH -> $REMOTE ..."
if ! git push --set-upstream "$REMOTE" "$BRANCH" 2>&1 | tee /tmp/nova3-push.log; then
    if grep -qiE 'could not read Username|Authentication failed|terminal prompts disabled|403' /tmp/nova3-push.log; then
        echo "push_branch: remote rejected the credential (see /tmp/nova3-push.log)" >&2
        exit 5
    fi
    exit 6
fi

remote_sha=$(git ls-remote "$REMOTE" "refs/heads/$BRANCH" | cut -f1)
if [ "$remote_sha" != "$local_sha" ]; then
    echo "push_branch: PUSH NOT VERIFIED - remote $remote_sha != local $local_sha" >&2
    exit 7
fi
echo "verified: $REMOTE/$BRANCH == $local_sha"
echo "pr:       $(git remote get-url "$REMOTE" | sed -E 's#(github\.com[:/])(.*)\.git#https://\1\2#')/compare/$BRANCH?expand=1"
