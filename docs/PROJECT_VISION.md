# NOVA3: one long-running project, one AI issue queue

Goal: reconstruct auditable, maintainable C++ source using independently obtainable
reference evidence. Keep reconstruction, native ports and optional expansions in
one architecture, without treating successful compilation as proof of original
binary/behaviour equivalence.

- **Reconstruction**: the existing `nova-autofeed.yml` continues publishing
  specific address tasks sourced from a grounded ELF/Ghidra function ledger.
  This stream continues throughout other phases until original coverage can
  honestly be demonstrated; it does **not** stop at a fixed issue count.
- **Foundation**: portable architecture, readable APIs, dependency boundaries,
  cross-platform build groundwork and tests start alongside reconstruction.
- **Native ports**: Linux and Windows x86_64; Android arm64-v8a/x86_64; iOS
  arm64 devices and macOS-hosted simulator. These are scaffolds until gameplay,
  assets and runtime pass smoke tests; iOS requires Apple tooling.
- **Game runtime**: shared C++ subsystems and independently verifiable smoke
  tests; running an executable is not equivalent to recreating the game.
- **Expansion**: configurable opt-in feature interfaces and modern-graphics
  modules. Actual additional gameplay features require a concrete requirement;
  never invent features or mark them delivered without specification/tests.

The `coordination/project_roadmap.json` file is the persistent machine-readable
vision. GitHub Action `nova-roadmap.yml` checks it twice an hour and on plan
changes. It creates a bounded number of GitHub issues with `nova:queued` and
`Allowed paths:` exactly like function tasks, so the existing Codex, Claude,
and Gemini worker pool can claim them without new accounts or prompts.

A phase gate requiring `merged:key,...` opens when the corresponding roadmap PR
is integrated. Reopening old work or a failed PR must not advance a gate.
The roadmap gates represent *engineering dependency order*, not proof that the
original game is already reverse-engineered or playable. Function reconstruction
and byte/behaviour matching retain separate evidence requirements.

To change a future requirement, edit `project_roadmap.json` once and review the
manifest change; the feeder will take care of subsequent finite tasks. Keep
all original APK/OBB/SO and large decompiler exports out of GitHub. Do not
publish private game content or credentials.
