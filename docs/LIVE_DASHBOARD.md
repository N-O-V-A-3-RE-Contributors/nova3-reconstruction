# NOVA3 worker dashboard (v2)

The dashboard reads live worker output and GitHub issue/PR counts. **It does not know the percentage of N.O.V.A. 3 reconstructed.** The current set of GitHub issues primarily covers infrastructure and investigation, not every game function.

## Running

From the already configured project: `./START-LINUX.sh`. Normal mode on Linux/macOS uses a terminal-managed screen (Python's built-in curses), not repeated ANSI screen-clears. It redraws the same screen and should not print duplicate header lines into the normal terminal scrollback.

* `Ctrl+C`: stop; saved worker checkouts remain available for resumption, provided GitHub task ownership allows it.
* `NOVA3_COMPACT=1 ./START-LINUX.sh`: simple append-only status view (no cursor-control sequences), useful on terminals that behave unusually.
* `NOVA3_PLAIN_OUTPUT=1 ./START-LINUX.sh`: raw worker output, useful for debugging.

Windows defaults to the append-only view. Linux/macOS terminals that cannot initialise curses fall back to append-only mode. A terminal smaller than 45 columns by 15 rows shows a minimal size hint rather than overflowing.

## Time estimates

* **Session timeout ceiling:** each AI session is currently capped at **30 minutes** by `nova3_team/worker.py`. The countdown is an upper bound on the current AI execution slice, **not a prediction of completion**. Three sessions can be attempted for a single task, followed by validation and PR work.
* **Queue ETA:** appears only after observing at least four NOVA-labelled GitHub issue closures spanning more than 30 minutes, including a closure within 72 hours. It is an approximate, historical-rate forecast for the currently queued/claimed GitHub issues. It may change significantly and does not include uncreated tasks.
* **Overall decompilation ETA:** unavailable until a verified full function inventory and sustained measured throughput exist. No credible estimate is derivable from 12 infrastructure issues.

## Colours and progress bars

The issue bar shows the distribution of GitHub issues by state (queued, claimed, review). It is **not a completeness meter**. PR counts and issue counts update approximately once per minute. Local engine/test source-file counts are an inventory, not verification.

## If fullscreen still renders badly

Run `NOVA3_COMPACT=1 ./START-LINUX.sh` and report the terminal application and `$TERM`; this mode is guaranteed not to emit ANSI redraws from the NOVA3 dashboard. Avoid running multiple worker instances with the same GitHub identity and saved checkout.
