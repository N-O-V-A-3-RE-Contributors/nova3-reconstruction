#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
printf '\nNOVA 3 GitHub publish: private repository, no original game binaries\n'
command -v gh >/dev/null || { echo 'Missing gh. Run: sudo pacman -S github-cli'; exit 2; }
command -v git >/dev/null || { echo 'Missing git. Run: sudo pacman -S git'; exit 2; }
python3 -m unittest discover -s nova3_team/tests -q
if ! gh auth status >/dev/null 2>&1; then
    echo 'Authenticate GitHub (browser auth; never share your token):'
    gh auth login
fi
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git init -b main
fi
# Existing Git history and remotes are preserved; never reset.
if ! git config user.name >/dev/null || ! git config user.email >/dev/null; then
    echo 'Git identity missing. Set it with: git config --global user.name "..." and user.email "..."'
    exit 3
fi
git add -A
python3 -m nova3_team.guard --repo .
if ! git diff --cached --quiet; then
    git commit -m 'Establish distributed NOVA reconstruction collaboration'
fi
if git remote get-url origin >/dev/null 2>&1; then
    echo 'An origin remote already exists; preserving it. Inspect before pushing:'
    git remote -v
    echo 'To publish to the existing origin: git push -u origin HEAD'
    exit 4
fi
if [ "$(git branch --show-current)" != main ]; then
    echo 'Existing branch is not main; preserving it. Manually choose publish branch before retrying.'
    exit 5
fi
name="${NOVA3_REPO_NAME:-nova3-reconstruction}"
owner="${NOVA3_OWNER:-}"
visibility="${NOVA3_VISIBILITY:-private}"
if [[ "$visibility" != private && "$visibility" != public ]]; then
    echo 'NOVA3_VISIBILITY must be private or public';exit 6
fi
full="$name"
if [[ -n "$owner" ]];then full="$owner/$name";fi
echo "Creating $visibility GitHub repository '$full'."
gh repo create "$full" "--$visibility" --source=. --remote=origin --push
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
echo "GitHub repository: https://github.com/$repo"
python3 -m nova3_team.seed --repo "$repo" --create
printf '\nREADY: repository and first task issues published privately.\n'
printf 'Start your worker: python3 -m nova3_team.worker run --repo %s --provider codex\n' "$repo"
