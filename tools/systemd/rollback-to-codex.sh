#!/usr/bin/env bash
# Roll back from the DSH worker to the Codex worker WITHOUT losing anything.
#
#   tools/systemd/rollback-to-codex.sh          stop DSH unit, start Codex unit
#   tools/systemd/rollback-to-codex.sh --check  print what would change
#
# Nothing is deleted: every DSH checkpoint, spend ledger, private session log and
# task checkout stays in the slot directory, and every Codex checkpoint in
# ~/.cache/nova3-team/<repo>/.nova-state is untouched (the DSH supervisor uses
# its own slot, `dsh`, precisely so the two never share state). A task that was
# mid-claim when the rollback happens keeps its GitHub lease, which expires on
# its own within 90 minutes and is then requeued by the coordinator sweep.
set -euo pipefail
mode="${1:-}"

if [[ "$mode" == "--check" ]]; then
  systemctl --user is-active nova3-dsh-worker.service || true
  systemctl --user is-enabled nova3-worker.service || true
  exit 0
fi

# Stop the DSH supervisor first: SIGTERM makes it finish the current step and
# kill its own process group, so no DSH session outlives the handover.
systemctl --user stop nova3-dsh-worker.service || true
systemctl --user disable nova3-dsh-worker.service || true
systemctl --user start nova3-worker.service
systemctl --user status nova3-worker.service --no-pager | head -20
echo
echo "Rolled back to the Codex worker. DSH state is preserved at:"
echo "  ~/.cache/nova3-team/N-O-V-A-3-RE-Contributors_nova3-reconstruction/.nova-slots/dsh/"
echo "Re-enable the DSH worker with: systemctl --user enable --now nova3-dsh-worker.service"
