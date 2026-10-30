#!/usr/bin/env bash
# Contributor-safe, no-sudo installation of the NOVA3 Control Center.
# The legacy name "NOVA3 Observatory" and its executable alias are retained.
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
for file in observer.py operator_ops.py presence_view.py progress.py forecast_model.py project_roadmap.json ui_components.py live_status.py journal_inspector.py onboarding.py; do
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
[[ -f "$LIVE_GUIDE" && -f "$FORECAST_GUIDE" ]] || { echo 'Missing Control Center integration/forecast guides' >&2; exit 1; }
python3 -c 'import curses,sys; assert sys.version_info >= (3,10)'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$SOURCE_DIR/tests" -q
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
OLD_TARGET="$DATA_HOME/nova3-observatory"
TARGET="$DATA_HOME/nova3-control-center"
BINDIR="$HOME/.local/bin"
install -d -m 700 "$DATA_HOME" "$BINDIR"
# Stage and health-check before replacing anything. The legacy directory is
# preserved so an interrupted upgrade can always fall back to it.
STAGING="$(mktemp -d "$DATA_HOME/.nova3-control-center.stage.XXXXXXXX")"
cleanup_staging() {
  # Only unlink regular files in our newly created private staging directory.
  # Unexpected directories or symlinks are preserved for inspection.
  for staged_file in "$STAGING"/*; do
    [[ -f "$staged_file" && ! -L "$staged_file" ]] || continue
    unlink -- "$staged_file"
  done
  for backend_file in access_policy.py planning_bridge.py planning_memory.py; do
    staged_file="$STAGING/planning_backend/$backend_file"
    [[ -f "$staged_file" && ! -L "$staged_file" ]] || continue
    unlink -- "$staged_file"
  done
  rmdir -- "$STAGING/planning_backend" 2>/dev/null || true
  rmdir -- "$STAGING" 2>/dev/null || true
}
trap cleanup_staging EXIT
for file in observer.py operator_ops.py presence_view.py progress.py forecast_model.py project_roadmap.json ui_components.py live_status.py journal_inspector.py onboarding.py; do
  install -m 600 "$SOURCE_DIR/$file" "$STAGING/$file"
done
install -m 600 "$SOURCE_DIR/provider_accounts.py" "$STAGING/provider_accounts.py"
install -m 600 "$PROVIDER_RUNTIME" "$STAGING/provider_runtime.py"
install -m 600 "$SOURCE_DIR/planning_chat.py" "$STAGING/planning_chat.py"
install -d -m 700 "$STAGING/planning_backend"
for backend_file in access_policy.py planning_bridge.py planning_memory.py; do
  install -m 600 "$SOURCE_DIR/../../nova3_team/$backend_file" "$STAGING/planning_backend/$backend_file"
done
# provider_runtime imports this sibling when the optional Hermes backend is
# registered; stage it when present so the standalone install keeps loading.
HERMES_RUNTIME="$SOURCE_DIR/../../nova3_team/hermes_runtime.py"
[[ -f "$HERMES_RUNTIME" ]] && install -m 600 "$HERMES_RUNTIME" "$STAGING/hermes_runtime.py"
install -m 600 "$HISTORICAL_HELPER" "$STAGING/historical_toolchain.py"
install -m 600 "$HISTORICAL_GUIDE" "$STAGING/HISTORICAL_TOOLCHAIN.md"
install -m 600 "$LIVE_GUIDE" "$STAGING/OBSERVATORY_LIVE_INTEGRATION.md"
install -m 600 "$FORECAST_GUIDE" "$STAGING/FORECAST_POLICY.md"
# Offline synthetic health check, outside the source tree and with -I (no PYTHONPATH).
python3 -B -I "$STAGING/observer.py" --demo --once >/dev/null
# Fail closed if the provider registry / Accounts page could not load; the
# observer otherwise degrades silently to "Accounts module unavailable".
python3 -B -I -c "import sys; sys.path.insert(0, sys.argv[1]); import provider_accounts; assert provider_accounts.provider_runtime.PROVIDERS" "$STAGING" >/dev/null
python3 -B -I -c "import sys; sys.path.insert(0, sys.argv[1]); import planning_chat; assert planning_chat.planning_memory" "$STAGING" >/dev/null
install -d -m 700 "$TARGET"
for file in observer.py operator_ops.py presence_view.py progress.py forecast_model.py project_roadmap.json ui_components.py live_status.py journal_inspector.py onboarding.py provider_accounts.py provider_runtime.py historical_toolchain.py HISTORICAL_TOOLCHAIN.md OBSERVATORY_LIVE_INTEGRATION.md FORECAST_POLICY.md; do
  install -m 600 "$STAGING/$file" "$TARGET/$file"
done
[[ -f "$STAGING/hermes_runtime.py" ]] && install -m 600 "$STAGING/hermes_runtime.py" "$TARGET/hermes_runtime.py"
install -m 600 "$STAGING/planning_chat.py" "$TARGET/planning_chat.py"
install -d -m 700 "$TARGET/planning_backend"
for backend_file in access_policy.py planning_bridge.py planning_memory.py; do
  install -m 600 "$STAGING/planning_backend/$backend_file" "$TARGET/planning_backend/$backend_file"
done
# Shared state (accounts.json, snapshot.json) intentionally stays in the old
# XDG state path; install never deletes ~/.local/share/nova3-observatory.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'exec python3 "${XDG_DATA_HOME:-$HOME/.local/share}/nova3-control-center/observer.py" "$@"' \
  > "$BINDIR/nova3-control"
chmod 700 "$BINDIR/nova3-control"
# Compatibility alias: existing shortcuts/systemd drop-ins keep working.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'exec python3 "${XDG_DATA_HOME:-$HOME/.local/share}/nova3-control-center/observer.py" "$@"' \
  > "$BINDIR/nova3-observer"
chmod 700 "$BINDIR/nova3-observer"
echo "NOVA3 Control Center installed: $BINDIR/nova3-control (alias: nova3-observer)"
echo "Previous Observatory files remain at: $OLD_TARGET (state is not deleted)."
echo 'Read-only contributor pages are available to anyone with repository access.'
echo 'Organization-owner or repository ADMIN permission is rechecked before every administrative action.'
echo 'Opening the dashboard will not restart your running AI worker.'
echo 'A: separate process/lease/provider-log/structured-feed status plus owner local journal.'
echo 'A 60-second structured-event gap is shown as RUNNING with a structured-event-quiet sub-badge.'
echo 'Optional historical ARM compiler: press H in the TUI, or run nova3-control --toolchain-setup.'
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
