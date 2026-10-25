#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git branch --show-current)" == main ]] || { echo 'ERROR: check out main before publishing'; exit 2; }
if [[ -n "$(git status --porcelain)" ]]; then
  echo 'WARNING: local uncommitted files exist; the release will contain only committed HEAD.'
fi
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
echo "Repository: $repo"
[[ -n "$repo" ]] || exit 2
read -r -p 'Create a launcher release tag and trigger four GitHub build jobs? [y/N] ' yes
[[ "$yes" == y || "$yes" == Y ]] || exit 0
stamp="$(date -u +%Y%m%d%H%M%S)"
tag="launcher-v${stamp}"
git tag "$tag"
git push origin "$tag"
printf '\nWhen builds finish, release downloads appear at:\nhttps://github.com/%s/releases\n' "$repo"
