# Contributing to the N.O.V.A. 3 reconstruction

Thanks for helping. This project rebuilds a 2012 Android game as source we own, using the reference files only as private evidence. You do not need to be a reverse engineer to be useful: running a worker, writing a test, or checking a claim all move the project forward.

## Three ways to help

| You want to… | Start with |
| --- | --- |
| **Donate compute** — run the AI worker on queued tasks | [docs/START_HERE.md](docs/START_HERE.md) · [docs/WORKER_LOOP.md](docs/WORKER_LOOP.md) |
| **Write code or research** — take a specific task | This page, then the task's issue |
| **Review** — check a claim, a PR or evidence | [docs/NOVA_TEAM.md](docs/NOVA_TEAM.md), [docs/DECOMPILE.md](docs/DECOMPILE.md) |

## Run the worker (donate compute)

1. Ask an owner for **read** access to this repository.
2. Download the worker release for your platform from [Releases](https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction/releases), unpack it, and run `START.sh` / `START.command` / `START.cmd`.
3. Supply **your own** DeepSeek/OpenRouter key when asked. The installer never stores or sends it; usage is billed to your account.
4. Leave it running. It claims one scoped task at a time and opens a pull request when done. `nova3-dsh status` shows the current state; `Ctrl+C` stops it.

You can change your provider, model and limits at any time — that is your local choice. What you cannot do is approve or merge work.

## Work on a task directly

1. Pick an open issue labelled `nova:queued`, or ask an owner to scope one. Every task declares `Allowed paths:` — change nothing outside them.
2. Edit, build and test locally.
3. Open a pull request that says `Closes #<issue>`.
4. A machine check compares your diff with the task scope and flags protected trees, binaries and credentials. An owner/admin then reviews and merges.

If the task needs a reference binary you do not have, say so and stop; an honest "not verified here" is a valid outcome.

## The rules that matter

* **Never commit game content.** No APKs, OBBs, native libraries, extracted assets, decompiler dumps or credentials — not in commits, issues, PRs or logs. Reference files stay on your machine.
* **Stay inside the task scope.** The issue's `Allowed paths:` is the contract; the review gate enforces it.
* **Evidence, not vibes.** Use the states honestly: INVENTORIED, DISASSEMBLED, DECOMPILED, UNDERSTOOD, IMPLEMENTED, TESTED, VERIFIED. A successful build is not a byte match.
* **No self-approval.** Workers open PRs; owners/admins merge. Do not merge your own work or edit the approval/lease state.
* **Keep the queue honest.** One claimed task at a time per worker; release a task you cannot finish rather than sitting on the lease.

## Pull request checklist

- [ ] The PR says `Closes #<issue>` and only touches the task's allowed paths.
- [ ] Tests or checks were actually run; paste the commands and exit codes.
- [ ] Matching claims are labelled with their scope, not overstated.
- [ ] No secrets, game files or large generated artefacts.
- [ ] The change is understandable without reading a chat log.

## Where to ask

Open an issue for bugs or design questions. For anything involving credentials, permissions or a suspected vulnerability, use a private security advisory instead of a public issue (see [SECURITY.md](SECURITY.md)).

This is an independent interoperability and preservation project. It is not affiliated with or endorsed by Gameloft, and it does not distribute the game.
