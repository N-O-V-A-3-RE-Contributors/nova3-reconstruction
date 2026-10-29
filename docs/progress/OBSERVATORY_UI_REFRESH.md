# Observatory UI refresh — task #113

The previous PULSE renderer put roughly twelve text rows inside one screen-sized
border. It lost the four-panel hierarchy, coloured bars and useful distribution
of information. This change restores four real cards and adds a consistent theme
without changing the worker, forecast model, source validation or owner checks.

## Theme and layout

Standard-library curses only. On 256-colour terminals the surface is charcoal
(index 234), with light grey text, muted slate labels, cyan accents, and
text-labelled green/amber/red severity. Rounded Unicode borders, one-cell gutters,
badges and task-share meters provide hierarchy. Eight-colour terminals use the
terminal background; limited pair counts and monochrome fall back safely.
`NOVA3_ASCII=1`, a non-Unicode output encoding, or `TERM=dumb` selects ASCII.
Clipping accounts for wide and combining characters and removes control characters.
Curses refreshes its virtual screen; no ANSI sequences or forced terminal clears.
The idle input/redraw interval is one second; collection intervals are unchanged.

| Terminal | PULSE | FORECAST / ACTIVITY |
| --- | --- | --- |
| >=140x35 (also 180x48, 220x55) | 2x2 cards, task bars, readiness/source strip, recent round traffic when present | Fixed evidence gate, side-by-side queue/setup, scrollable blockers; stacked round cards with feed/contract sidebar |
| 120x30 / 100x30 | Dense 2x2 cards with bars | Queue/setup plus short scrolling details; round console and feed sidebar |
| 80x24 | Two columns; compact task counts, worker observations and UNKNOWN gate | Stacked console; queue/setup retain separate frames |
| 72x18 | Four compact cards: health, task counts, UNKNOWN gate, activity | Summary panels; footer retains UNKNOWN gate; resize for full detail |
| 40x12 and smaller | Bounded resize notice, activity summary, UNKNOWN | Same safe resize notice on all tabs |

The header uses the same 14 labels and key registry for navigation. Two aligned
rows show all labels when they fit; otherwise the visible rows follow selection.
The other eleven pages retain their existing renderers and controls, with shared
palette, clipping, header and footer improvements.

## Controls and evidence

TAB / Shift-TAB cycle; 1–9 select the original pages; 0 opens owner operations
only after the existing role gate; W workers, H toolchain, A activity, ? help,
Q quit. A retains its mark-all meaning on the PR page. Up/Down scroll forecast
reasons or activity rounds. Existing service/merge confirmations remain intact.

PULSE and FORECAST consume `forecast_lines` and `forecast_model.forecast` through
the existing adapter. Whole-game ETA remains UNKNOWN. Queue ranges/charts require
qualifying stage history; counts, configured slots, PR merges and session exits
never create a throughput or game estimate. Pipeline bars explicitly divide by
sampled **tasks**, including separately labelled other/unclassified states.
Original matches are an evidenced count or UNKNOWN, never inferred from source
presence or host tests. Missing setup evidence stays UNKNOWN.

ACTIVITY and worker cards use `activity_records` / `activity_line`. Badges age
out after 90 seconds, including during a stalled collector. Past/superseded task
rounds cannot become active. Counters mean observed starts/completions, not passed
tests; partial counts remain lower bounds. Extra JSON fields and raw log strings
are never rendered there. SERVICE LOGS remains a separate existing page. The
worker card and footer show actual systemd state; an active idle service is IDLE.

`install.sh` includes the sibling helper in both manifests and performs an
isolated `--demo --once` import smoke after copying. No download or dependency was
added. Installation into the user's directory was not performed in this session;
tests copy the exact installer manifest into a temporary standalone directory.
The existing PowerShell installer is outside the task allowlist and was untouched
(it already omits the #112 forecast helper).

## Verification (Linux, Python 3.14.8; offline synthetic fixtures)

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tools/observatory/tests -p 'test_ui_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tools/observatory/tests -q
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s nova3_team/tests -q
PYTHONDONTWRITEBYTECODE=1 python tools/observatory/observer.py --demo --once
PYTHONDONTWRITEBYTECODE=1 python -m nova3_team.guard --repo . --all
bash -n tools/observatory/install.sh
git diff --check
```

- New UI tests: 13 passed. FakeScreen covers all 14 tabs at 40x12, 72x18,
  80x24, 100x30, 140x38, 180x48 and 220x55 in Unicode and ASCII. Assertions
  check disjoint frames, right-column use, four actual PULSE cards, state badges,
  honest zero-evidence labels, counters, stale/past tasks, scrolling, no leaked
  extra fields, unclassified/blocked tasks and locked owner controls. The original
  single-box renderer fails the multiple-frame contracts.
- Standalone manifest smoke: 98 renders without repository imports (includes
  120x30), plus isolated `--demo --once`, passed.
- Real curses PTY smoke: 56 renders each under `xterm-256color`, `vt100` and
  `dumb` passed. This checks capabilities, not Konsole visual fidelity.
- Full Observatory: 150 run, 148 passed; one failure and one error in existing
  `test_discovery_fix` real-systemd checks. This sandbox denies SO_PASSRIGHTS /
  SO_PASSCRED socket operations before systemd can validate units. No unit
  verification or installer preflight was weakened to conceal the environment
  failure. Those same checks prevent the full installer preflight here.
- Root project unit suite: 429 passed. Demo report, source guard, shell syntax
  and whitespace checks passed. No packages, network, GitHub changes or original
  game data were needed. No gameplay/source-equivalence claim is made.

Remaining review: inspect typography/contrast in the owner's actual Konsole at
1920x1080; pixel dimensions depend on font settings. Resize behaviour and terminal
capabilities are tested, but no owner-machine screenshot or live worker was used.
Rerun the two systemd integration checks on an unrestricted normal user session.

## Suggested PR body

Before: PULSE was a mostly empty single bordered rectangle with plain rows;
FORECAST and ACTIVITY were flat dumps that obscured hierarchy and freshness.
After: four responsive PULSE cards, coloured task bars and readiness strip;
fixed UNKNOWN forecast gate with queue/setup sections and scrolling blockers;
worker console round cards with freshness badges, counters and a privacy sidebar.
Shared curses primitives retain all 14 tabs and owner restrictions. Linux
standalone installation bundles the new helper. Validation and the two sandbox
systemd limitations are recorded above.

## Synthetic text preview (100x30)

Generated directly from the deterministic zero-match/missing-worker fixture in
`test_ui_refresh.py`; no live worker, private paths or original game data.

```text
  ◈ NOVA3 // OBSERVATORY                                                Thu 29 Oct 2026  09:00:00
  fixture/synthetic • LIVE READ-ONLY • GitHub role UNKNOWN
───────────────────────────────────────────────────────────────────────────────────────────────────
  [1:PULSE]  2:TASKS   3:PULL REQUESTS   4:ACTIONS   5:SERVICE LOGS   6:FORECAST   7:SOURCE MAP
   8:APK ATLAS   9:ROADMAP   0:OWNER OPS   W:WORKERS   H:TOOLCHAIN   A:ACTIVITY   ?:HELP
───────────────────────────────────────────────────────────────────────────────────────────────────
 ╭─ WORKER TELEMETRY ──────────────────[RUNNING]─╮ ╭─ TASK PIPELINE ──────────────────────[TASKS]─╮
 │ systemd active / running [RUNNING]            │ │ Denominator: 4 sampled tasks                 │
 │ PID 123  |  restarts 0                        │ │ Queued         1 ██████░░░░░░░░░░░░░░░░░░░   │
 │ Active issue: UNKNOWN (no live task observat… │ │ Claimed        1 ██████░░░░░░░░░░░░░░░░░░░   │
 │ Feed heartbeat: UNKNOWN                       │ │ Review         0 ░░░░░░░░░░░░░░░░░░░░░░░░░   │
 │ AI category: UNKNOWN                          │ │ Blocked        1 ██████░░░░░░░░░░░░░░░░░░░   │
 │ Observation: MISSING                          │ │ Integrated     0 ░░░░░░░░░░░░░░░░░░░░░░░░░   │
 │ AI UNKNOWN: local feed MISSING (older/missin… │ │ Other / verified / unclassified: 1           │
 │                                               │ │ Shares of task sample; never game completio… │
 ╰───────────────────────────────────────────────╯ ╰──────────────────────────────────────────────╯

 ╭─ EVIDENCE / FORECAST ────────────[UNVERIFIED]─╮ ╭─ LIVE ACTIVITY ────────────────────[MISSING]─╮
 │ Whole-game ETA: UNKNOWN                       │ │ [MISSING] AI UNKNOWN                         │
 │ Setup readiness: UNKNOWN                      │ │ No structured worker observations.           │
 │ Queue stage work: UNKNOWN                     │ │ Commands UNKNOWN | tests UNKNOWN             │
 │ Queue samples: 0 | confidence: none           │ │ Waiting for a compatible worker feed.        │
 │ Original matches: 0 (evidenced count)         │ │ No raw AI logs or internal thoughts.         │
 │ Setup reasons: no_setup_milestones            │ │                                              │
 │ 6: forecast details | A: worker console       │ │                                              │
 │                                               │ │                                              │
 ╰───────────────────────────────────────────────╯ ╰──────────────────────────────────────────────╯
───────────────────────────────────────────────────────────────────────────────────────────────────
  GitHub LIVE · 0s ago  │  systemd active [RUNNING]
  TAB pages  A activity  6 forecast  ? help  Q quit | LOCKED
```
