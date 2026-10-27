# NOVA3 autonomous discovery and task replenishment

This is the **local reference-discovery sidecar**, not the AI code-writing worker.
It uses headless Ghidra on locally and lawfully held ELF `.so` libraries in
`analysis/` (or `NOVA3_REFERENCE_ROOT` where configured).

## Behaviour

1. Each contributor runs the normal launcher. If that contributor has valid
   ELF references, setup locates or installs Ghidra and registers local
   discovery. If no original files are present, the worker can still
   contribute to tasks that do not require matching against originals.
2. Linux: a `systemd --user` timer starts every 30 minutes, independently of
   the Codex/Claude/Gemini coding worker. Non-systemd environments use a
   single-instance detached background loop.
3. Discovery hashes each local ELF and runs headless analysis once per hash.
   It caches the private Ghidra output under the user's home directory.
4. The exported GitHub ledger includes **only** SHA-256, library basename,
   tool-identified function image-base-relative address and size. No original
   library, game asset, disassembly, source body, code strings or credentials
   are uploaded by this tool.
5. New records are append-only; existing source-matching statuses and
   evidence are never overwritten. A private temporary Git clone publishes
   the metadata to `coordination/function_ledger.json`, normally directly to
   `main` if authorised. Protected branches use a reviewable PR and GitHub's
   standard auto-merge when available. No force-pushes or bypasses.
6. The pre-existing `nova-autofeed.yml` is triggered by the resulting main
   branch update. Its adjusted queue generator checks *historical GitHub
   issue address coverage* before scheduling further clusters of four.
7. The previous model of creating vague, AI-consuming evidence-expansion tasks
   is suppressed when the local-discovery sidecar is installed. A missing
   local ELF is reported as a blocker, never interpreted as game completion.

## Initial maintainer setup

From the unpacked upgrade archive:

```bash
bash ~/Downloads/nova3_discovery_autopilot/install.sh
```

The script fast-forwards the clean local `main`, updates only infrastructure,
backs up prior coordinator files, runs unit tests, commits and pushes, then
registers the separate discovery timer. It does not restart or modify the
active AI worker or delete saved checkouts.

On Arch Linux, first-time tool setup can invoke signed `pacman` packages for
Ghidra and prompt for sudo once. On Windows/macOS, if an installed Ghidra isn't
found, it can download an official release after verifying its published
SHA-256. Ghidra 12.2 requires JDK 25; supported setup paths attempt to install
it automatically, but platform restrictions can still require a local admin.

## Status and control

```bash
systemctl --user list-timers nova3-discovery.timer
journalctl --user -u nova3-discovery.service -n 60 --no-pager
cd ~/Projects/nova3_re
python3 -m nova3_team.discovery status
python3 -m nova3_team.discovery preview --project .
```

`preview` runs Ghidra if necessary and counts discovered functions without
publishing them. It is optional; normal operation is fully scheduled.

Local cache: `~/.cache/nova3-team/discovery/`.

## Important limits

- Ghidra discovers **candidate** function boundaries. This is not C++ source,
  not a verified function match, and not a complete game inventory. Real
  matching/reconstruction still takes coding and verification effort.
- Native `.so` discovery does not reconstruct Java DEX, scripts, assets or
  the game's entire runtime. These require separate evidence and tasks.
- A headless Ghidra run can take hours and substantial RAM/disk. It is
  isolated and uses a separate low-priority service with a two-hour timeout.
- A contributor without original binaries cannot conjure accurate function
  boundaries. They can still work on existing repository tasks. They are not
  asked to acquire or upload copyrighted commercial game content.
- GitHub branch protections, disabled private forks, offline machines and
  declined first-time installations can prevent fully unattended publication.
  Errors appear in `nova3-discovery.service` journal and local status JSON.
- Automated CI/test passing is **not proof of binary equivalence**.
- Most code in this upgrade is tested with simulated Ghidra and GitHub calls,
  but a real headless Ghidra installation is required for live verification.
