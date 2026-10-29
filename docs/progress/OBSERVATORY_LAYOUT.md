# Control Center responsive layout

The Control Center keeps framed cards, status words, colours and proportional task
bars while adapting to the live terminal size. Resizing does not select a page,
type into search or confirm an action. The observer does not start a worker.

## Window sizes

Both dimensions must meet a breakpoint. A wide but short window stays compact.

| Minimum columns × rows | Density | Presentation |
| --- | --- | --- |
| 72 × 18 | Compact, including 80 × 24 and 100 × 30 | Abbreviated sibling pages; focused card/detail views |
| 120 × 40 | Medium | Task/PR/Actions list beside selected details; paired worker cards |
| 160 × 45 | Spacious | Larger balanced cards and more visible records |
| 192 × 54 | Panoramic | Same hierarchy with additional room for card contents |

Below 72 × 18, a friendly resize message replaces the dashboard. A pending
action confirmation is never hidden by that message: the exact action and its
`Y/N` keys stay on the first row, because `Y` still executes while resized.
The PULSE
summary retains four framed tiles at supported sizes. PgUp/PgDn opens each
summary tile in a full card; Up/Down scrolls its wrapped details. OWNER OPS
pages through one card at 80 × 24, two stacked cards at 100 × 30, and four cards
at medium and larger sizes. The ACTIVITY privacy panel fits from 100 columns;
at narrower widths the sanitized round cards get the available width.

## Navigation

The selected group and full current page label stay visible. Angle markers
indicate more group/page labels outside the visible navigation window.

| Group | Pages and unchanged shortcuts |
| --- | --- |
| Overview | 1 PULSE, 6 FORECAST, 9 ROADMAP |
| Pipeline | 2 TASKS, 3 PULL REQUESTS, 4 ACTIONS, 5 SERVICE LOGS |
| Research | 7 SOURCE MAP, 8 APK ATLAS, H TOOLCHAIN |
| Team | W WORKERS, A ACTIVITY |
| Administration | 0 OWNER OPS, ? HELP |

Tab/Shift-Tab or Left/Right traverses visible pages (14 for contributors, 15 for owners) in the established order.
`{` / `}` selects the first page of the previous/next group. Direct shortcuts
remain available even when their labels are outside the navigation window.
On PULL REQUESTS, A retains its existing “mark all worker PRs” meaning; Tab or
group navigation reaches ACTIVITY from there. The 0 shortcut requires the
authenticated organization owner or repository ADMIN; other navigation can show the locked owner-page explanation.

On TASKS, PULL REQUESTS and ACTIONS:

- Up/Down selects a record; O opens its GitHub page.
- `/` edits a case-insensitive filter. Enter keeps it; Esc restores the prior
  filter. Backspace clears characters. An empty filter shows all records.
- D toggles a full-width selected-record card. Medium and larger layouts show
  both the list and selected details by default.
- PgUp/PgDn scrolls details, or pages through a compact list. Selection and
  scroll positions clamp after filtering, refreshed data and resizing.

HELP, WORKERS and ACTIVITY scroll with Up/Down. HELP lists every page shortcut
and existing controls. OWNER OPS retains J/j for its no-CI setting; use arrow
keys to scroll its prose. Owner commands remain role-gated and retain their
existing confirmations and backend permission checks. No account/login flow is
added.

## Accessibility and installation

Status words such as LOCKED, STALE, MISSING and UNKNOWN accompany colour.
Monochrome and limited-colour terminals use the existing palette fallback.
Set `NOVA3_ASCII=1` when launching to replace decorative Unicode characters.
The bottom keybar keeps help and quit visible; notices occupy the status row.
Search keeps the insertion marker and Enter/Esc instructions visible even for
long queries. Q exits the observer while the worker continues independently.

Task bars represent shares of sampled tasks, never game completion. Whole-game
forecasts remain evidence-gated UNKNOWN. ACTIVITY reads sanitized counters and
does not display extra fields, private paths, prompts or raw AI logs.

To update and reinstall from a checkout, run this yourself:

```bash
git pull --ff-only && bash tools/observatory/install.sh
```

This task does not publish a release or perform that installation.

## Offline verification

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tools/observatory/tests -p 'test_observer.py'
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tools/observatory/tests -p 'test_ui_refresh.py'
```

The strict fake surface checks all 14 pages at the documented sizes, owner,
contributor, guest and demo roles, ASCII fallback, randomized resizes, grouped
navigation, card geometry, selection, search, notices and confirmation overlays.
Existing render and isolated-bundle tests also cover real curses through a
local pseudo-terminal; no GitHub or real terminal session is needed for the
fake-surface tests. Physical Konsole visual inspection remains an operator
check. Discovery-unit tests invoking `systemd-analyze` are separate from screen
tests and can fail in sandboxes that prohibit its socket operations.
