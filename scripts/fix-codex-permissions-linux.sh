#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != Linux ]]; then
  echo 'This helper is for Linux only.'; exit 2
fi
ROOT="$HOME/.codex"
[[ -d "$ROOT" ]] || { echo "No Codex configuration directory exists at $ROOT"; exit 2; }
echo "Checking Codex permissions for user $(id -un)"
find "$ROOT" -xdev -user root -print
read -r -p 'Change only root-owned files under ~/.codex to your user? [y/N] ' answer
[[ "$answer" == y || "$answer" == Y ]] || exit 0
sudo find "$ROOT" -xdev -user root -exec chown -h "$(id -u):$(id -g)" {} +
chmod u+rwx "$ROOT"
if [[ -f "$ROOT/config.toml" ]]; then chmod u+rw "$ROOT/config.toml"; fi
codex login status
