# NOVA3 live worker dashboard

The launcher shows a terminal dashboard after account checks and sign-in.

- **Stage and timer:** derived from actual local worker messages. A timer or animation only means the process is still running, not that an AI has made progress.
- **Task and session:** extracted from the worker's actual task claim/session messages.
- **GitHub queue:** counts actual **open** issues with `nova:queued`, `nova:claimed`, and `nova:review` labels. An issue without these labels is counted as *other*.
- **Pull requests:** open PR count read from GitHub and the number of submissions observed in the current worker process.
- **Queue bar:** relative issue-state distribution, **not** the percent of original game reconstructed.
- **Local source inventory:** counts C/C++ files in `reconstruction/engine` and `reconstruction/tests`, if present. Counts are not proof of correctness.
- **Game matching progress:** explicitly marked *not established*. Binary matching requires verified function inventory and a reproducible matching workflow.
- **Worker events:** a bounded tail of exact sanitised worker status messages. The AI CLI's normal detailed output is captured by the worker and recorded in its task handoff files, not streamed to this dashboard.

Stats refresh approximately once per minute. If GitHub is unavailable the worker continues; counts can be stale and the display says so.

Run on Linux: `bash START-LINUX.sh` from the checkout, or use the release `START.sh`. Ctrl+C stops; launch again to resume saved local checkouts when their issue leases remain available. After an unexpected power loss, saved source changes survive if the local storage survives, but the current in-memory AI reasoning and unsaved writes cannot be recovered. An expired task claim may be reassigned.

If Codex reports that `~/.codex/config.toml` is unreadable, it may have root-owned files left by an earlier `sudo codex`. From an ordinary Linux terminal, run:

```bash
sudo find "$HOME/.codex" -xdev -user root -exec chown -h "$(id -u):$(id -g)" {} +
codex login status
```

Never launch the project or its AI worker with sudo. Never share the `.codex` directory or credentials.

Set `NOVA3_PLAIN_OUTPUT=1` to disable the dashboard and see chronological worker lines directly, e.g. `NOVA3_PLAIN_OUTPUT=1 bash START-LINUX.sh`. Noninteractive terminals fall back to plain output.
