# N.O.V.A. 3 — independent source reconstruction

**Rebuilding a 2012 Android game from evidence, as source code we own.** No APK patching, no bundled binaries, no borrowed executable code — reconstructed C++/Java, tests, and the trail that proves each step.

[![NOVA verification](https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction/actions/workflows/nova-ci.yml/badge.svg)](https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction/actions/workflows/nova-ci.yml)

> **Read this first:** this is a long-running research project, not a playable game and not a finished port. It has working source-owned utility, package and geometry modules with honest evidence for a small slice of the engine. The rest is inventoried, scheduled, or still unknown.

## What is this?

| Area | What it means |
| --- | --- |
| **Reconstruction** | Recovered behaviour rewritten as independently compiled C++/Java, with tests and provenance. |
| **Evidence** | Ghidra/IDA exports, DWARF analysis, asset schemas and differential checks — kept private, cited, never published as game content. |
| **Automation** | A DSH-based worker fleet that claims scoped tasks, verifies them, and opens pull requests under an owner/admin gate. |
| **Tooling** | One-command setup for Linux, macOS and Windows, plus a `/nova` control command inside DeepSeek Harness. |

## Status at a glance

| Measure | Value |
| --- | --- |
| Functions recorded from the Premium ELF | **1,165 of 39,092** (3.0%; inventory not complete) |
| Source mappings implemented | **18** (7 ARM-matched, 8 behavioural, 3 utility modules) |
| End-to-end game | **Not yet** — no renderer, gameplay, audio, campaign or saves |
| Worker platform | **Ready** — DSH supervisor, owner gate, release `launcher-v7-20261102` |
| Decompilation queue | **On hold** pending the owner's pre-release source |

The dynamic ETA is deliberately blunt about the gap between those numbers. Run `python -m nova3_team.analysis` (or `/nova eta` inside DSH) for the current inventory, rate and scenarios, and read [docs/CONTROL_PLANE.md](docs/CONTROL_PLANE.md) for how to interpret them.

## Quick start

### I want to run a worker (contributor)

1. Ask an owner for **read** access to this repository.
2. Download the worker release for your OS from [Releases](https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction/releases) (Linux, macOS Intel/Apple silicon, Windows) or the source archive `nova3-source.tar.gz`.
3. Unpack and run `START.sh` / `START.command` / `START.cmd`. The installer checks your machine, installs the worker for your user account, and asks for **your own** DeepSeek/OpenRouter key. Nothing is billed to anyone else.
4. Leave it running. `nova3-dsh status` shows what is happening.

Full guide: [docs/WORKER_LOOP.md](docs/WORKER_LOOP.md) · one page: [docs/START_HERE.md](docs/START_HERE.md).

### I want to build or analyse locally

```bash
git clone https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction.git
cd nova3-reconstruction
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/host
ctest --test-dir build/host --output-on-failure
```

The analysis workbench (`./nova3 doctor`, `./nova3 index`, `./nova3 decompile`) is described in [docs/DECOMPILE.md](docs/DECOMPILE.md). Reference game files are **not** included; where an oracle needs them, they stay on your machine.

### I own or administer this project

```bash
python -m nova3_team.dsh_status              # worker, queue and open requests
python -m nova3_team.analysis                # inventory, coverage, ETA
python -m nova3_team.review --repo … --pr N  # compliance verdict for a PR
python -m tools.dsh_loop.toolchain --tier project --plan
```

Permissions, the release process, the hold, and the `/nova` command are in [docs/CONTROL_PLANE.md](docs/CONTROL_PLANE.md).

## How the automation fits together

```text
GitHub issue (scoped task)                    owner/admin only
        │                                            │
        ▼                                            ▼
  coordinator (Actions)  ── leases ──►  approve / queue / merge
        │                                            ▲
        ▼                                            │
  DSH supervisor (systemd) ── bounded session ── independent verify ── PR
        │
        └── /nova status | eta | review | fleet | toolchain
```

* **Workers** claim one scoped task at a time, edit only its `Allowed paths`, run their own AI, and open a pull request. They never approve or merge.
* **Owners/admins** approve tasks, read the machine compliance check, and merge. Authority is verified live against GitHub (repo ADMIN, org owner, or the owner-maintained DSH team allowlist).
* **DSH** ([DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)) is the execution engine; the optional `/nova` plugin puts the control plane in any chat. Docs: [deepseek-harness.github.io](https://deepseek-harness.github.io/deepseek-harness/).

## Project layout

| Path | Contents |
| --- | --- |
| `reconstruction/` | Source-owned engine, Android and test code (the product). |
| `nova3_team/` | Queue, coordinator, DSH worker/supervisor, analysis and review tools. |
| `tools/` | Setup, analysis, comparison and packaging scripts. |
| `docs/` | Human documentation — start at [docs/README.md](docs/README.md). |
| `research/`, `analysis/`, `generated/` | **Private, not tracked.** Reference analysis, decompiler output, inventories. |
| `originals/`, `Archive/` | **Private, not tracked.** Game files you own; never uploaded. |

## Documentation

| I want to… | Read |
| --- | --- |
| Find any document | [docs/README.md](docs/README.md) |
| Contribute with my own AI | [docs/WORKER_LOOP.md](docs/WORKER_LOOP.md), [docs/START_HERE.md](docs/START_HERE.md) |
| Understand the evidence rules | [AGENTS.md](AGENTS.md), [docs/NOVA_TEAM.md](docs/NOVA_TEAM.md) |
| Run or administer the automation | [docs/CONTROL_PLANE.md](docs/CONTROL_PLANE.md), [docs/DSH_WORKER.md](docs/DSH_WORKER.md) |
| Decompile and compare | [docs/DECOMPILE.md](docs/DECOMPILE.md), [docs/OBJECT_MATCHING.md](docs/OBJECT_MATCHING.md) |
| See where the project stands | [docs/progress/STATUS.md](docs/progress/STATUS.md), [docs/progress/NEXT.md](docs/progress/NEXT.md) |

## Ground rules

* **No original game binaries or assets in this repository.** Reference APKs, OBBs, native libraries and decompiler output stay on the machine that owns them and are never published, uploaded or shipped.
* **The final product builds from source.** Platform libraries and audited, reproducibly built third-party code are allowed; Gameloft executables are not.
* **Evidence outranks enthusiasm.** A function is not "done" because it compiles; the states are separate: INVENTORIED → DISASSEMBLED → DECOMPILED → UNDERSTOOD → IMPLEMENTED → TESTED → VERIFIED.
* **Owner/admin control is not optional.** Contributors can tune their own local AI; they cannot approve, merge or change policy.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). If a security issue involves credentials or access, use a private advisory rather than a public issue. This is an independent reconstruction for interoperability and preservation; it is not affiliated with or endorsed by Gameloft.
