# Control Center local activity and evidence-gated forecast (#111)

This source-only integration reads sanitized worker observations; it does not
show internal thoughts. No matching claim is made: **NOT MATCHED**. No game
executable, asset, raw provider output, or private analysis is an input.

## Operation

After the maintainer merges and updates their checkout, reinstall with:

```sh
git pull --ff-only && bash tools/observatory/install.sh
```

That command is for the owner after merge, and was not run against GitHub by
this worker. Control Center stays separate from the worker. Run the continuous
queue worker in its own terminal with `bash START-LINUX.sh` (without `--once`).
Opening or quitting Control Center does not start/stop the worker.

PULSE shows the newest locally observed active session, preferring it over a
more recent completed task. ACTIVITY is a focused scrollable page (A outside
the PR page, or TAB from any page). The PR page retains its A mark-all control.
Existing numbered pages, W workers, H toolchain, owner locks and ? help remain.
FORECAST supports up/down scrolling. Small terminals show compact status and
UNKNOWN whole-game ETA even below the normal 72x18 layout threshold.

No GitHub login is necessary for local guidance/observations:

```sh
~/.local/bin/nova3-observer --offline
~/.local/bin/nova3-observer --offline --once
~/.local/bin/nova3-observer --toolchain-setup
```

`--offline` reads local summaries and any existing cached metadata, performs
no subprocess collection, and does not start the GitHub poller. Normal live
mode retains authenticated read-only GitHub refresh and existing explicitly
confirmed owner operations. This change adds no worker lifecycle operation,
permission, scheduler, lease, fail-stop change, or automatic merge.

## Private summary boundary

The selected `--repo OWNER/REPO` selects exactly the worker directory
`~/.cache/nova3-team/OWNER_REPO/.nova-private/<issue-checkout>/round-N.activity.json`.
This matches `nova3_team.worker` and the schema-v1 producer in
`nova3_team/activity_feed.py`. There is no home-wide glob, recursive traversal,
raw-log fallback, provider invocation, or import of the worker package.

On each existing local poller iteration (approximately four seconds), the
reader visits at most 64 checkout directories, 1,024 total directory entries,
and 128 candidate summaries. Each summary is at most 8,192 bytes; reads are
also capped if a file grows. At most 16 newest observations are retained.
Files whose mtime or observation is older than seven days are ignored. An
observation is fresh only for 90 seconds, re-evaluated at render time. Future
timestamps display UNKNOWN. Fresh QUIET observations indicate no recent
categorized event; they do not establish progress or a live process probe.

Directory components and files are opened using descriptor-relative
`O_NOFOLLOW` traversal. Symlinked ancestors/checkouts/files, nonregular files,
hardlinked summaries, malformed JSON, unsupported schemas and invalid values
are rejected without printing exception text or private paths. Nonblocking
opens prevent a FIFO from hanging refresh. Platforms lacking these primitives
fail closed as UNAVAILABLE. Scan limits are shown explicitly: the selected
observation is newest within the bounded scan, not a claim that all cached
state has been enumerated. No cached state is deleted or modified.

Only fixed status/category enums, bounded counters, normalized UTC timestamps,
issue/round numbers and boolean flags leave the reader. Unknown fields are
dropped even if they contain text, paths or credentials. Categories are
lifecycle, command, test, file_change, message, error or unknown. A message
category reveals no message text. Test completion counters do not mean tests
passed. Counts marked incomplete are lower bounds. File-change counts can be
UNKNOWN. Unsupported/older providers remain UNKNOWN. The TUI neither reads
nor displays raw `round-N.log`, commands, arguments, prompts or reasoning.

Fresh JSON activity is independent of systemd status: a foreground worker can
be observed while its service is stopped. Completed/integrated/verified tasks,
stale observations, superseded rounds and conflicting GitHub stages are not
presented as currently active tasks. Missing GitHub task data is labeled TASK
UNKNOWN. Activity stays in memory and is excluded from the GitHub snapshot
cache and all publication paths.

## Forecast evidence and limits

All time labels in PULSE, FORECAST, ROADMAP and `--once` now use the bundled
pure `forecast_model.forecast`. Legacy assumed minutes/task, linear worker
multipliers, function batching, invented inventory coverage fractions and
roadmap labour extrapolations are removed from display paths. Verified source
matches=0 produces UNKNOWN; missing verified denominator and representative
throughput also produce UNKNOWN. Whole-game integration/gameplay remains
uncalibrated even if a future matching-scope forecast becomes measurable.

The adapter normalizes actual issue workflow stages: queued, claimed/active,
review, blocked, and explicitly integrated/verified workflow completion. A
closed issue without accepted workflow labels remains unresolved. Integrated
issues/merged PRs never become matching evidence. At most 500 tasks and their
linked PR/CI observations are supplied (within the model record bound).
Explicit closing references are used for PR linkage, with the existing
`#N:` title convention only when references are absent. Multiple open PRs
remain ambiguous rather than selecting a guessed current PR.

Current-head PR rollups expose observed failed/pending signals. Green/empty
rollups and historic workflow runs cannot establish complete required-check
coverage; CI stays unknown in those cases. Issue `updatedAt` is not a stage
start. Fresh local activity supplies worker assignment observations, not
session start times, timeout budgets or proven task completion. Existing
journal exit codes and cumulative PR counters are not comparable stage
history and are not used as training samples.

Task size/provider/toolchain cohorts, complete retries and complete queue
pagination are not in the present collector contract. They remain explicitly
unknown. The model accepts an `unknown` task kind and gates it as a blocker;
unknown work is never silently classified as source reconstruction. The
queue is explicitly an incomplete bounded GitHub sample. Setup milestones
are absent (UNKNOWN), rather than invented from tool installation or issue
closures. Ledger-reported zero matching counts can gate the model; positive
status labels do not supply reviewed matching evidence.

The forecast view reports scope, sample size, confidence, stale/missing
observations and blocker reasons separately from setup readiness and source
matching. Synthetic tests demonstrate low confidence at five independent
comparable samples and medium at ten; these are test fixtures, not project
measurements. No live numeric queue ETA is justified by the current adapter.

## Packaging and follow-up boundaries

The Bash installer includes `forecast_model.py`, the integration guide and
FORECAST_POLICY.md alongside existing helpers. Observer loads each sibling
module by its installed file location; it does not need repository Python
import paths. The standalone test launches Python with `-I` outside the
checkout and displays an offline report.

Release ZIP packaging in `.github/workflows/nova-launchers.yml` and the
Windows installer have explicit file lists outside this task's allowlist.
They need a separate follow-up to bundle the model and guides, plus the
pre-existing historical helper/guides expected by the Bash installer. This
slice targets installation from the merged repository using the owner command
above; it does not silently edit CI or other installers.

## Verification / handoff

Results and exact commands are recorded below after execution. All tests use
source-owned synthetic metadata. No network, installation of packages, GitHub
mutation, child AI session or proprietary binary inspection is needed.

Executed with Python 3.14.8 on 2026-10-29:

| Check | Actual result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/observatory/tests -p test_live_integration.py -q` | 24 passed, exit 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s nova3_team/tests -q` | 429 passed, exit 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/observatory/tests -q` | 137 executed: 135 passed, one failure and one error, exit 1 |
| Control Center discovery excluding exactly the two known systemd-dependent tests below | 135 passed, exit 0 |
| Bash installer under a disposable temporary HOME/XDG configuration | Exit 1 at the same two systemd test failures; no launcher created, no real installation modified |
| Standalone copied bundle, Python `-I`, `--offline --once`, outside repository import paths | Passed in integration suite; whole-game ETA UNKNOWN |
| `PYTHONDONTWRITEBYTECODE=1 python3 tools/observatory/observer.py --demo --once` | Exit 0; queue/setup/whole-game UNKNOWN with sample/blocker explanations |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m nova3_team.guard --repo . --all` | Guard passed, 404 files, exit 0 |
| `bash -n tools/observatory/install.sh` and `git diff --check` | Exit 0 |
| `ast.parse(..., feature_version=(3, 10))` on observer, model and all Control Center test modules | 12 files passed; Python 3.10 runtime not available/tested |

Known environmental failures reproduced before and after the integration:

* `test_discovery_fix.Tests.test_known_bug_working_directory_quoted`
* `test_discovery_fix.Tests.test_owner_repairs_exact_installer_output`

Both invoke `systemd-analyze verify`, which the sandbox rejects with
`SO_PASSRIGHTS` / `SO_PASSCRED`: Operation not permitted. The first consequently
misses its expected unit-path diagnostic; the second raises CommandFailure.
No systemd configuration, test bypass in the installer, or privilege change
was attempted. Private diagnostic logs for this disposable session are in
`/tmp/nova111-{project,observatory,install}-tests.txt`; these are not committed.

The filtered suite was run with this explicit exclusion, retaining all other
tests (including role locks, new integration and standalone tests):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import sys
import unittest
suite = unittest.defaultTestLoader.discover('tools/observatory/tests')
excluded = {
    'test_discovery_fix.Tests.test_known_bug_working_directory_quoted',
    'test_discovery_fix.Tests.test_owner_repairs_exact_installer_output',
}
def selected(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from selected(item)
        elif item.id() not in excluded:
            yield item
result = unittest.TextTestRunner().run(unittest.TestSuite(selected(suite)))
sys.exit(not result.wasSuccessful())
PY
```

Resume by running the focused tests and full suite, reviewing the diff within
the task allowlist, and merging through the maintainer's normal review. Then
run the owner reinstall command on an unrestricted normal-user machine and
check a foreground continuous worker in a separate terminal. Real curses
terminal interaction, live GitHub collection and the owner's actual installed
launcher were not exercised in this offline worker session; fake screens
covered 40x12, 72x18, 80x24, 120x35 and 400x45 plus existing render sizes.
Release/Windows packaging and evidence adapters for complete current-head
required checks, comparable histories, setup checks and reviewed matching
ledger evidence remain bounded follow-up tasks. No native implementation or
machine-code matching result is claimed by this dashboard work.
