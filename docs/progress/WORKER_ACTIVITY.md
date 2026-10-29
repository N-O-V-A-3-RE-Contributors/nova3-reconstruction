# Private worker activity feed (task #106)

The worker writes a small observational summary beside each private provider
round log. It does not publish this summary, send it to GitHub/presence reporting,
or contact an Control Center endpoint. Tool activity is not game progress, source
proof, a test-pass claim, or machine-code matching evidence. No game matching is
claimed by this task (NOT MATCHED).

For a checkout `<worker-cache>/<issue-checkout>`, the files are:

```text
<worker-cache>/.nova-private/<issue-checkout>/round-N.log
<worker-cache>/.nova-private/<issue-checkout>/round-N.activity.json
```

The existing log contains sensitive provider output and must stay private.
Consumers should read **only** the activity JSON. On POSIX the containing round
directory is mode `0700`; each summary is created as `0600` using a temporary file
in that directory and atomic replacement. A failed replacement retains the old
summary and removes the temporary file. Other platforms use their existing
account/directory access controls; POSIX mode guarantees do not imply Windows ACL
enforcement. These files are outside the task checkout and its Git/PR flow.

## JSON contract, schema version 1

Example (synthetic):

```json
{
  "schema_version": 1,
  "timestamp_utc": "2026-10-29T00:00:00Z",
  "status": "TESTING",
  "event_category": "test",
  "last_activity_age_seconds": 3,
  "commands_started": 2,
  "commands_completed": 1,
  "tests_started": 1,
  "tests_completed": 0,
  "file_changed_count": null,
  "counts_complete": true,
  "structured_events": true
}
```

| Field | Meaning |
| --- | --- |
| `timestamp_utc` | Worker wall-clock UTC time of this snapshot, RFC 3339 seconds. |
| `status` | One of the fixed states below. |
| `event_category` | `unknown`, `lifecycle`, `command`, `test`, `file_change`, `message`, or `error`; no event text. |
| `last_activity_age_seconds` | Nonnegative monotonic seconds since the worker last parsed a recognized event, or `null`. This is observation time, not an upstream event timestamp. |
| `commands_started`, `commands_completed` | Counts of observed command execution events for this round. Completions include failed commands. |
| `tests_started`, `tests_completed` | Subset with recognized explicit test runner invocations. These count commands, not test cases or passing tests. |
| `file_changed_count` | `null` during the provider session. After successful independent `validate_scope`, count of currently changed, validated checkout files, including pre-existing edits. Event paths and model claims do not supply this count. |
| `counts_complete` | False after malformed/unknown input, lost bytes, resource caps, file replacement/truncation, or unavailable structured input. Counts then cover only observations retained and must not be treated as totals. This is not a fidelity attestation. |
| `structured_events` | Whether structured parsing was enabled, not a claim that valid events have arrived. Other providers retain their existing text format. |

| Status | Observation |
| --- | --- |
| `RUNNING` | Recognized activity, without a currently observed test or edit item. |
| `TESTING` | An observed test command item has started without a matching completion. |
| `EDITING` | An observed file-change item has started without a matching completion; active tests take priority. |
| `QUIET` | No recognized event for at least 60 seconds. This does not indicate a hang or AI thinking. |
| `UNKNOWN` | No recognized activity, unavailable/malformed input, unknown schema, or text-only provider. |
| `FINISHED` | Provider process exited zero. This is not validation or PR completion. |
| `FAILED` | Provider process exited nonzero, timed out, failed to launch, or was interrupted. |

The process exit state takes precedence, including for text-only providers.
Structured `error`/`turn.failed` events set the error category; the worker does
not infer a final process exit from their text. A terminal provider summary stops
updating; the separate worker validation/PR flow may still be running.

## Compatibility, bounds and privacy

Before its first Codex round, the worker checks local `codex exec --help` once
(cached, three-second timeout, no model session). It adds `--json` only when help
advertises that exact option. Missing/failed/timed-out help retains the existing
command and live `UNKNOWN` status. Claude and Gemini commands are unchanged.
Local verification used `codex-cli 0.163.0`, whose help advertises `--json` as
JSONL output. No nested AI session or network documentation lookup was run.
The event fixtures are representative synthetic noninteractive Codex
`item.started`/`item.completed` events, not a captured real-provider transcript.

Parsing runs in the existing wait loop every ten seconds; no new thread or
watcher process is created. The existing 60-second journal heartbeat, absolute
session deadline, process-group termination, timeout quarantine, checkout/resume,
scope guard, and PR control flow remain in place. Initial/final summaries and the
post-scope file count bypass the throttle. Summary I/O/parser failures cannot
change provider exit codes or fail the worker task.

Each poll reads at most 256 KiB, accepts lines up to 64 KiB, and retains at most
1,024 active item IDs of at most 256 characters. Partial lines are buffered within
that cap; oversized lines are discarded until the next newline. A large backlog
is skipped to a bounded tail and `counts_complete` becomes false. Truncated final
lines are incomplete input. Replacement/truncation clears active state and marks
counts incomplete. On POSIX the reader refuses symlinks and non-regular files
and uses nonblocking opens so a FIFO cannot stall worker timeouts.

Only a fixed set of labels, numbers, booleans and worker timestamps are
serialized. No prompts, reasoning, command arguments, output, file contents,
event IDs, filenames, local paths, tokens, upstream exception messages or raw
executable logs enter the summary. Reasoning item content is ignored and never
used to infer activity. Test recognition handles direct `ctest`, `pytest`,
`pytest-3`, `python[3] -m unittest/pytest` and a single shell wrapper. Compound
shell commands intentionally remain generic command activity.

## Verification and handoff

Run offline:

```bash
python -m unittest nova3_team.tests.test_activity_feed nova3_team.tests.test_worker_timeout nova3_team.tests.test_worker_resume -v
python -m unittest discover -s nova3_team/tests -q
python -m nova3_team.guard --repo . --all
```

The focused suite passed 23 tests; the full worker suite passed 398 tests on
Python 3.14.8. Coverage includes split/multiline text, invalid UTF-8/JSON,
truncation/replacement, secret-bearing fields, unknown events, oversized input,
active-item bounds, no text leakage, atomic replacement/cleanup and POSIX modes,
FIFO/symlink refusal, optional CLI support, provider fallback, local emitter
integration, failure isolation, and simulated 60/120-second heartbeats with an
exact 125-second timeout deadline. No live provider, GitHub or service was started.

The broader Control Center suite ran 74 tests: 72 passed and two existing
`test_discovery_fix` tests failed because sandboxed `systemd-analyze verify`
cannot configure user-lookup/handoff socket options (`Operation not permitted`).
Those tests exercise real systemd verification outside this task's allowlist;
full CI success is pending an unrestricted CI runner. Source hygiene and diff
whitespace checks passed. A clean host Debug build and all 13 CTests passed
using CMake/Ninja under a temporary build directory. The launcher self-test,
shell syntax checks and Control Center offline demo passed. The legacy audit's
`/tmp` report attempt was rejected by its `.cache`-only policy; its read-only
no-output mode then passed without writing outside the task allowlist.

A later Control Center reader PR should discover summaries locally, bound reads
(e.g. 8 KiB), validate schema/enums/types, display snapshot age separately from
activity age, and treat missing/malformed/stale files as unavailable. It should
read no raw round logs, write no summaries, launch no workers, and send no feed
data over the network. Reader integration and TUI changes are deliberately left
for that separate PR; `tools/observatory/` is unchanged here.
