# NOVA3: Progress and recovery

## Honest progress

The issue labelled `nova:dashboard` is **an automatically updated status report**. It records queued, claimed, submitted-for-review, blocked and integrated tasks, and links PRs. **Integrated does not mean byte-matched.** Function coverage and complete-game ETA remain unknown until independently measured against a complete inventory.

An active GitHub claim indicates ownership, **not** that the AI model is consuming tokens. The local worker state (`python -m nova3_team.reliability --repo OWNER/REPO`) records provider launches, exits and failure codes. The repo cannot observe subscription usage balances.

## Failure handling

- Successful claim/heartbeat/release/submission operations update GitHub issue metadata **without a second coordinator response comment**, reducing comment flood.
- Errors such as `Head sha can't be blank` cause an immediate **global block**, not continuous retries.
- Unclassified transient failures get one cooldown/retry; two identical failures quarantine that task. Other tasks can continue.
- Source edits and submitted commits remain in the existing cached checkout. Restart does **not** delete them.
- A blocked task requires a maintainer to fix the cause and replace `nova:blocked` with `nova:queued` in GitHub. Do this only after correcting the affected worker(s).
- Expired leases return to queue with `nova:attention` and one maintainer-tagged alert. A claimed AI_RUNNING task with no stage heartbeat for over 45 minutes gets `nova:attention` and one alert. The alert is cleared after a later AI_FINISHED heartbeat or a fresh claim.
- The GitHub status issue updates every 30 minutes and when a PR closes; trigger manually for an immediate refresh via `gh workflow run nova-status.yml`.

## Commands (Arch Linux)

```bash
cd ~/Projects/nova3_re
./START-LINUX.sh
# Or, in a second terminal:
python3 -m nova3_team.report show --repo N-O-V-A-3-RE-Contributors/nova3-reconstruction
python3 -m nova3_team.reliability --repo N-O-V-A-3-RE-Contributors/nova3-reconstruction
```

**Stop:** Ctrl+C; **resume:** start the same launcher again. Checkout source is preserved, but model reasoning that was never written to disk cannot be recovered.

## Release management

Contributor packages must be rebuilt after source changes using your existing release workflow. Workers must obtain a fixed worker version before resuming; old installed executables may continue running outdated coordinator logic until updated. Release assets are only updated when GitHub Actions has successfully published them.

**Maintainer:** Check `Actions` for the coordinator and status workflows. If workflow permissions prohibit label/issue mutations, use repository Settings → Actions → General → Workflow permissions to give GitHub Actions the needed issue-write capability through workflow-scoped permissions.
