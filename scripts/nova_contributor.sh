#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
    echo 'Usage: bash nova_contributor.sh OWNER/REPO [codex|claude|gemini]'
    exit 2
fi
repo="$1";provider="${2:-codex}"
command -v git >/dev/null || { echo 'Git required';exit 2; }
command -v gh >/dev/null || { echo 'GitHub CLI required';exit 2; }
command -v python3 >/dev/null || { echo 'Python 3.10+ required';exit 2; }
if ! gh auth status >/dev/null 2>&1;then gh auth login;fi
if ! command -v "$provider" >/dev/null;then
    echo "Install and authenticate $provider CLI before continuing."
    exit 2
fi
base="$HOME/nova3-team"
if [[ ! -d "$base/.git" ]];then
    gh repo clone "$repo" "$base"
fi
cd "$base"
python3 -m nova3_team.worker run --repo "$repo" --provider "$provider"
