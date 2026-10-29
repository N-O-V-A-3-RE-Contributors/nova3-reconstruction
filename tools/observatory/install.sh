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
for file in observer.py operator_ops.py presence_view.py progress.py forecast_model.py project_roadmap.json ui_components.py; do
  [[ -f "$SOURCE_DIR/$file" ]] || { echo "Missing $file" >&2; exit 1; }
done
PROVIDER_RUNTIME="$SOURCE_DIR/../../nova3_team/provider_runtime.py"
[[ -f "$SOURCE_DIR/provider_accounts.py" && -f "$PROVIDER_RUNTIME" ]] || {
  echo 'Missing Accounts/provider runtime module' >&2; exit 1; }
HISTORICAL_HELPER="$SOURCE_DIR/../../nova3_team/historical_toolchain.py"
[[ -f "$HISTORICAL_HELPER" ]] || { echo 'Missing historical toolchain onboarding helper' >&2; exit 1; }
HISTORICAL_GUIDE="$SOURCE_DIR/../../docs/progress/HISTORICAL_TOOLCHAIN.md"
[[ -f "$HISTORICAL_GUIDE" ]] || { echo 'Missing historical toolchain guide' >&2; exit 1; }
LIVE_GUIDE="$SOURCE_DIR/../../docs/progress/OBSERVATORY_LIVE_INTEGRATION.md"
FORECAST_GUIDE="$SOURCE_DIR/../../docs/progress/FORECAST_POLICY.md"
[[ -f "$LIVE_GUIDE" && -f "$FORECAST_GUIDE" ]] || { echo 'Missing Observatory integration/forecast guides' >&2; exit 1; }
python3 -c 'import curses,sys; assert sys.version_info >= (3,10)'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$SOURCE_DIR/tests" -q
TARGET="${XDG_DATA_HOME:-$HOME/.local/share}/nova3-observatory"
BINDIR="$HOME/.local/bin"
install -d -m 700 "$TARGET" "$BINDIR"
for file in observer.py operator_ops.py presence_view.py progress.py forecast_model.py project_roadmap.json ui_components.py; do
  install -m 600 "$SOURCE_DIR/$file" "$TARGET/$file"
done
install -m 600 "$SOURCE_DIR/provider_accounts.py" "$TARGET/provider_accounts.py"
install -m 600 "$PROVIDER_RUNTIME" "$TARGET/provider_runtime.py"
install -m 600 "$HISTORICAL_HELPER" "$TARGET/historical_toolchain.py"
install -m 600 "$HISTORICAL_GUIDE" "$TARGET/HISTORICAL_TOOLCHAIN.md"
install -m 600 "$LIVE_GUIDE" "$TARGET/OBSERVATORY_LIVE_INTEGRATION.md"
install -m 600 "$FORECAST_GUIDE" "$TARGET/FORECAST_POLICY.md"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'exec python3 "${XDG_DATA_HOME:-$HOME/.local/share}/nova3-observatory/observer.py" "$@"' \
  > "$BINDIR/nova3-observer"
chmod 700 "$BINDIR/nova3-observer"
# Exercise the installed sibling imports, offline and outside the source tree.
PYTHONDONTWRITEBYTECODE=1 python3 -I "$TARGET/observer.py" --demo --once >/dev/null
echo "Observatory installed: $BINDIR/nova3-observer"
echo 'Read-only contributor pages are available to anyone with repository access.'
echo 'ADMIN/MAINTAIN GitHub permission is checked before every owner operation.'
echo 'Opening the dashboard will not restart your running AI worker.'
echo 'A: local sanitized activity summaries. Whole-game ETA stays UNKNOWN without evidence.'
echo 'Optional historical ARM compiler: press H in the TUI, or run nova3-observer --toolchain-setup.'
echo 'Read-only observers and ordinary host-only workers need no historical NDK.'
echo 'No compiler is downloaded, installed or executed. Source matching remains NOT PROVEN.'
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
