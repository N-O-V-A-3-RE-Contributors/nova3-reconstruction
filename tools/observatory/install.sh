#!/usr/bin/env bash
# Contributor-safe, no-sudo installation of the shared NOVA3 Observatory.
set -euo pipefail
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo 'Install as your own user, never with sudo.' >&2
  exit 1
fi
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SETUP_TIMER=0
case "${1:-}" in
  '') ;;
  --setup-timer) SETUP_TIMER=1 ;;
  *) echo 'Usage: install.sh [--setup-timer]' >&2; exit 2 ;;
esac
for file in observer.py operator_ops.py presence_view.py progress.py project_roadmap.json; do
  [[ -f "$SOURCE_DIR/$file" ]] || { echo "Missing $file" >&2; exit 1; }
done
python3 -c 'import curses,sys; assert sys.version_info >= (3,10)'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$SOURCE_DIR/tests" -q
TARGET="${XDG_DATA_HOME:-$HOME/.local/share}/nova3-observatory"
BINDIR="$HOME/.local/bin"
install -d -m 700 "$TARGET" "$BINDIR"
for file in observer.py operator_ops.py presence_view.py progress.py project_roadmap.json; do
  install -m 600 "$SOURCE_DIR/$file" "$TARGET/$file"
done
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'exec python3 "${XDG_DATA_HOME:-$HOME/.local/share}/nova3-observatory/observer.py" "$@"' \
  > "$BINDIR/nova3-observer"
chmod 700 "$BINDIR/nova3-observer"
echo "Observatory installed: $BINDIR/nova3-observer"
echo 'Read-only contributor pages are available to anyone with repository access.'
echo 'ADMIN/MAINTAIN GitHub permission is checked before every owner operation.'
echo 'Opening the dashboard will not restart your running AI worker.'
if [[ "$SETUP_TIMER" -eq 1 ]]; then
  echo 'Optional owner-only Linux discovery timer setup requested.'
  if [[ "$(uname -s)" != Linux ]]; then
    echo 'Discovery timer requires Linux/systemd.' >&2
    exit 2
  fi
  printf 'Type SETUP to configure the current user discovery timer: '
  read -r confirmation
  if [[ "$confirmation" != SETUP ]]; then
    echo 'Skipped discovery timer setup.'
    exit 0
  fi
  python3 - "$TARGET/operator_ops.py" <<'PY'
import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('nova_owner_ops', sys.argv[1])
ops = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ops
spec.loader.exec_module(ops)
ops.require_owner_access(ops.REPO_DEFAULT)
root = Path.home() / 'Projects/nova3_re'
ops.timer_repair(root, ops.REPO_DEFAULT, emit=print)
PY
fi
