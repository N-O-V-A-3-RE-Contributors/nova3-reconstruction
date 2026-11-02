# Documentation map

Everything human-readable lives here. If you only read one page, read the [project README](../README.md); if you are an AI worker, read [AGENTS.md](../AGENTS.md) instead.

## Start here

| Document | For | What it covers |
| --- | --- | --- |
| [README](../README.md) | everyone | What the project is, status, quick start, ground rules |
| [START_HERE](START_HERE.md) | new contributors | The 10-minute path from zero to a running worker |
| [WORKER_LOOP](WORKER_LOOP.md) | contributors | The DSH worker: install, run, pause, update, troubleshoot |

## Contribute

| Document | For | What it covers |
| --- | --- | --- |
| [CONTRIBUTING](../CONTRIBUTING.md) | contributors | Ways to help, setup, PR rules, review flow |
| [NOVA_TEAM](NOVA_TEAM.md) | contributors | Distributed worker guide, labels, leases, quarantine |
| [ONE_CLICK_CONTRIBUTORS](ONE_CLICK_CONTRIBUTORS.md) | legacy users | The older launcher generation (kept for reference) |
| [MAINTAINER_GITHUB_SETUP](MAINTAINER_GITHUB_SETUP.md) | owners | Repository, Actions and permission setup |

## Operate

| Document | For | What it covers |
| --- | --- | --- |
| [CONTROL_PLANE](CONTROL_PLANE.md) | owners/admins | Roles, commands, release routes, ETA, hold, `/nova` |
| [DSH_WORKER](DSH_WORKER.md) | owners/admins | Supervisor architecture, states, budgets, cutover |
| [LIVE_DASHBOARD](LIVE_DASHBOARD.md) | everyone | Terminal dashboard and what its numbers do (not) mean |
| [PROGRESS_AND_RECOVERY](PROGRESS_AND_RECOVERY.md) | owners | Checkpoints, resume, quarantine, recovery |

## Research and engineering

| Document | What it covers |
| --- | --- |
| [DECOMPILE](DECOMPILE.md) | The local analysis workbench: doctor, index, decompile |
| [OBJECT_MATCHING](OBJECT_MATCHING.md) | Compiler/ABI fingerprinting, object and byte matching |
| [DISCORD_IMPLEMENTATION](DISCORD_IMPLEMENTATION.md) | Donor-assisted matching protocol |
| [LEGACY_REVALIDATION](LEGACY_REVALIDATION.md) | How older reconstruction is re-checked |
| [FAST_TRACK](FAST_TRACK.md) | Hardware-aware analysis plan for a small laptop |
| [SCOPE_RECOVERY](SCOPE_RECOVERY.md) | What "complete" can and cannot mean here |
| [DEBUG_BUILD_COMPARISON](DEBUG_BUILD_COMPARISON.md) | Comparing debug and retail builds |
| [AUTOFEED](AUTOFEED.md) | Queue replenishment |
| [AUTONOMOUS_DISCOVERY](AUTONOMOUS_DISCOVERY.md) | Function discovery runs |
| [toolchain](toolchain.md) | Verified tools, versions and paths |

## Project state

| Document | What it covers |
| --- | --- |
| [STATUS](progress/STATUS.md) | Current verified facts and what is still missing |
| [NEXT](progress/NEXT.md) | Prioritised, executable next tasks |
| [HANDOFF](progress/HANDOFF.md) | Standalone resume instructions with no chat history |
| [SESSION_LOG](progress/SESSION_LOG.md) | Append-only record of real commands and results |
| [BLOCKERS](decisions/BLOCKERS.md) | Outstanding problems and attempted remedies |
| [ARCHITECTURE](decisions/ARCHITECTURE.md) | Substantial engineering decisions and trade-offs |
| [OWNER_APPROVAL_GATE](progress/OWNER_APPROVAL_GATE.md) | The approval/lease contract in detail |
| [PROJECT_VISION](PROJECT_VISION.md) | What the finished project is meant to be |

## Conventions for these docs

* Write for a person first: short paragraphs, tables, concrete commands.
* Separate **inventory**, **implemented**, **tested** and **verified** counts.
* Cite the command or report behind a number; never present a forecast as fact.
* Machine contracts stay in [AGENTS.md](../AGENTS.md) and [NOVA_TEAM](NOVA_TEAM.md), not in the human guide.
