#!/usr/bin/env bash
# Install the NOVA3 DSH user units. Never uses sudo; never touches other units.
#
#   tools/systemd/install-units.sh            install + enable (start on next login)
#   tools/systemd/install-units.sh --now      install + enable + start now
#   tools/systemd/install-units.sh --check    verify only, change nothing
#
# The units are copied (not symlinked) so a Git checkout change cannot switch a
# running supervisor's unit file without a deliberate `systemctl --user
# daemon-reload`, and so the installed configuration survives a checkout move.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mode="${1:-}"

missing=0
for unit in nova3-dsh-worker.service nova3-dsh-status.service nova3-dsh-status.timer \
            nova3-autoadmit.service nova3-autoadmit.timer; do
  if [[ ! -f "$here/$unit" ]]; then echo "missing unit source: $unit" >&2; missing=1; fi
done
[[ $missing -eq 0 ]] || exit 1

if [[ "$mode" == "--check" ]]; then
  systemctl --user cat nova3-dsh-worker.service >/dev/null 2>&1 \
    && echo "installed: nova3-dsh-worker.service" || { echo "not installed: nova3-dsh-worker.service"; exit 3; }
  systemctl --user is-enabled nova3-dsh-worker.service
  systemctl --user is-enabled nova3-dsh-status.timer 2>/dev/null || true
  exit 0
fi

mkdir -p "$unit_dir"
for unit in nova3-dsh-worker.service nova3-dsh-status.service nova3-dsh-status.timer \
            nova3-autoadmit.service nova3-autoadmit.timer; do
  install -m 0644 "$here/$unit" "$unit_dir/$unit"
  echo "installed $unit_dir/$unit"
done
systemctl --user daemon-reload
systemctl --user enable nova3-dsh-worker.service nova3-dsh-status.timer nova3-autoadmit.timer
if [[ "$mode" == "--now" ]]; then
  systemctl --user start nova3-dsh-status.timer
  systemctl --user start nova3-autoadmit.timer
fi
echo
echo "enabled. Status:   systemctl --user status nova3-dsh-worker.service"
echo "                  python3 -m nova3_team.dsh_status --slot dsh"
echo "Stop cleanly:     systemctl --user stop nova3-dsh-worker.service"
echo "Pause (keep run): touch ~/.cache/nova3-team/N-O-V-A-3-RE-Contributors_nova3-reconstruction/.nova-slots/dsh/state/control/STOP"
