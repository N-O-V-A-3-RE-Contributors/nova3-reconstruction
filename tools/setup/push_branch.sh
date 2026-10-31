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
# So there are exactly three supported paths, and this script reports which one
# applies instead of guessing:
#   1. managed  - run inside a Paperclip session that holds GitHub capability;
#                 the wrapper brokers the credential for this invocation.
#   2. manual   - run from the maintainer's own terminal (not through the
#                 wrapper) with GH_TOKEN exported, or with `gh auth login` done.
#   3. ssh      - the machine already holds a GitHub SSH identity. That key is
#                 not a managed credential, so this path is opt-in and never
#                 taken silently: set NOVA3_PUSH_VIA_SSH=1. The configured
#                 remote is left untouched; the push goes to the ssh URL derived
#                 from it. Verify the identity first with `ssh -T git@github.com`.
#                 Measured 2026-11-01: the local key authenticates as
#                 'UmbraSecurity' but the remote denies it write access to this
#                 repository, so this route is documented and not a fix as-is.
#
# Set NOVA3_PUSH_DRY_RUN=1 to print the push command and run `git push --dry-run`
# instead of publishing: that proves reachability, identity and ref advertisement
# without creating a branch. It is the correct check for an unattended session.
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

REMOTE_URL=$(git remote get-url "$REMOTE" 2>/dev/null)
SSH_URL=$(printf '%s' "$REMOTE_URL" | sed -E 's#^https://github\.com/(.+)/?\.git$#git@github.com:\1.git#; s#/$##')
PUSH_TARGET="$REMOTE"
DRY_RUN=()

if [ "${NOVA3_PUSH_VIA_SSH:-0}" = "1" ]; then
    case "$SSH_URL" in
        git@github.com:*) PUSH_TARGET="$SSH_URL" ;;
        *) echo "push_branch: NOVA3_PUSH_VIA_SSH=1 but $REMOTE_URL is not an https://github.com/... URL" >&2
           exit 2 ;;
    esac
    echo "auth:    ssh identity ($PUSH_TARGET)"
    echo "         preflight the key with: ssh -T git@github.com"
elif [ -z "${PAPERCLIP_GITHUB_BROKER_TOKEN:-}${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]; then
    echo "push_branch: no GitHub credential source in this environment." >&2
    echo "  PAPERCLIP_GITHUB_BROKER_TOKEN, GH_TOKEN and GITHUB_TOKEN are all unset." >&2
    echo "  Remediation (maintainer, one of):" >&2
    echo "    * grant the GitHub connection to this agent/run in Paperclip so the runtime" >&2
    echo "      wrapper can broker credentials, then re-run: bash tools/setup/push_branch.sh" >&2
    echo "    * or run this script from your own terminal (outside the Paperclip wrapper)" >&2
    echo "      with GH_TOKEN=<token> exported, or after 'gh auth login'." >&2
    echo "    * or publish with this machine's existing GitHub SSH key, if that key has" >&2
    echo "      push access (the local key authenticates as 'UmbraSecurity', which the" >&2
    echo "      remote answered with 'Permission ... denied' on 2026-11-01):" >&2
    echo "      NOVA3_PUSH_VIA_SSH=1 bash tools/setup/push_branch.sh" >&2
    echo "      (verify first: ssh -T git@github.com; add NOVA3_PUSH_DRY_RUN=1 to only rehearse)" >&2
    echo "  Measured 2026-11-01: no route from this run can publish. The branch stays" >&2
    echo "  local until a credential with write access is supplied." >&2
    exit 3
fi

if [ "${NOVA3_PUSH_DRY_RUN:-0}" = "1" ]; then
    DRY_RUN=(--dry-run)
    echo "mode:    dry run (nothing is published)"
fi

echo "pushing $BRANCH -> $PUSH_TARGET ${DRY_RUN[*]:-}..."
if ! git push "${DRY_RUN[@]}" --set-upstream "$PUSH_TARGET" "$BRANCH" 2>&1 | tee /tmp/nova3-push.log; then
    if grep -qiE 'could not read Username|Authentication failed|terminal prompts disabled|403|Permission denied \(publickey\)' /tmp/nova3-push.log; then
        echo "push_branch: remote rejected the credential (see /tmp/nova3-push.log)" >&2
        exit 5
    fi
    exit 6
fi

if [ "${NOVA3_PUSH_DRY_RUN:-0}" = "1" ]; then
    echo "dry run: authentication and ref advertisement succeeded; nothing published."
    exit 0
fi

remote_sha=$(git ls-remote "$PUSH_TARGET" "refs/heads/$BRANCH" | cut -f1)
if [ "$remote_sha" != "$local_sha" ]; then
    echo "push_branch: PUSH NOT VERIFIED - remote $remote_sha != local $local_sha" >&2
    exit 7
fi
echo "verified: $PUSH_TARGET/$BRANCH == $local_sha"
echo "pr:       https://github.com/$(printf '%s' "$REMOTE_URL" | sed -E 's#^.*github\.com[:/]##; s#\.git$##')/compare/$BRANCH?expand=1"
