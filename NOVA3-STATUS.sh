#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
REPO="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
python3 -m nova3_team.report show --repo "$REPO"
printf '\nLOCAL MODEL EXECUTION RECORDS\n'
python3 -m nova3_team.reliability --repo "$REPO"
