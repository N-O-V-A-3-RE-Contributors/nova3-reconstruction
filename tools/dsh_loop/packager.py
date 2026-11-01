"""Source-only distribution archives for contributors without Git access.

The installation cannot rely on ``git`` access to a *private* repository, so
every release also ships a source archive: the tracked Git tree (which never
contains proprietary binaries, assets or decompiler output) plus a small
``GETTING-STARTED`` file. Extracting it and running ``install.sh`` /
``START.cmd`` produces the same installation the Git route produces.

Extraction model (important for correctness):

    <dist>/                      this archive
        nova3 source tree...      tracked files at the tag
        GETTING-STARTED.txt
        .nova3-dsh-loop           provenance: repo, commit, installer version
        tools/dsh_loop/...        the installer itself

``install.sh --workspace <this folder>`` recognises the marker, records it as
the workspace *without* touching Git, and installs everything else under the
user's per-user install root.

The archive is built with ``git archive`` so untracked files (local analysis,
references, caches) can never leak into it.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
import zipfile
from pathlib import Path

DIST_FILES = ("GETTING-STARTED.txt", "install.sh", "install.cmd", "START.cmd",
              "START.command", "START.sh")


def tracked_files(repo: Path) -> list[str]:
    cp = subprocess.run(["git", "ls-files", "-z"], cwd=str(repo), capture_output=True, check=False)
    if cp.returncode != 0:
        raise RuntimeError("git ls-files failed; the packager needs a Git checkout")
    return [p for p in cp.stdout.decode("utf-8").split("\0") if p]


def provenance(repo: Path, *, repository: str) -> dict:
    def git(*args: str) -> str:
        cp = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=False)
        return (cp.stdout or "").strip()
    return {
        "repo": repository,
        "commit": git("rev-parse", "HEAD"),
        "describe": git("describe", "--tags", "--always", "--dirty"),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "installer_version": 1,
        "packaged_by": "tools/dsh_loop/packager.py",
    }


def getting_started(prov: dict) -> str:
    return f"""N.O.V.A. 3 reconstruction -- worker distribution
{'=' * 52}

Source commit : {prov.get('commit', 'unknown')[:12]}
Repository    : {prov.get('repo', 'unknown')}
Built         : {prov.get('built_at', '')}

This archive contains ONLY source code of the reconstruction project (tracked
files of the repository). It contains no game binaries, no game assets and no
API keys.

HOW TO START
------------
Linux :   ./install.sh          (or: sh install.sh)
macOS :   double-click START.command
Windows:  double-click START.cmd

The launcher installs, for your user only:
  * a private Node.js runtime if you do not have a suitable one,
  * a pinned DeepSeek Harness runtime and worker profile,
  * a "nova3-dsh" command in your PATH.

FIRST RUN asks for one thing: your own model provider API key. The key is
never written by the installer into any file; see "provider" below. You need
your own DeepSeek (or OpenRouter) account; this project does not ship keys.

WHERE THINGS GO
---------------
  Linux   ~/.local/share/nova3/dsh      runtime, node, DSH profile
          ~/.config/nova3/dsh-loop.json settings (no secrets)
          ~/.local/bin/nova3-dsh         launcher
  macOS   ~/Library/Application Support/Nova3/dsh
          ~/.config/nova3/dsh-loop.json, ~/.local/bin/nova3-dsh
  Windows %LOCALAPPDATA%\\Nova3           runtime, launcher
          %APPDATA%\\Nova3\\dsh-loop.json

USEFUL COMMANDS
---------------
  nova3-dsh            start working (or finish setup on first run)
  nova3-dsh status     what is configured; API-key fingerprint; recent passes
  nova3-dsh doctor     explain anything missing
  nova3-dsh update     update the source (git route only)
  nova3-dsh stop       stop after the current pass
  nova3-dsh help       everything else

The worker logs go to <this folder>/.local/dsh-loop/ . Progress reports live in
docs/progress/. Read docs/START_HERE.md and AGENTS.md to contribute.
"""


INSTALL_SH = r"""#!/bin/sh
# NOVA3 worker setup -- generated. Safe to delete.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required. Install it, then run this again." >&2
  exit 2
fi
exec "$PY" "$HERE/tools/dsh_loop/install.py" --workspace "$HERE" "$@"
"""


INSTALL_CMD = r"""@echo off
rem NOVA3 worker setup -- generated. Safe to delete.
set HERE=%~dp0
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% "%HERE%tools\dsh_loop\install.py" --workspace "%HERE%" %*
if errorlevel 1 pause
"""


START_SH = r"""#!/bin/sh
# NOVA3 worker -- generated. Installs on first run, then starts working.
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/install.sh" --then-run "$@"
"""


START_COMMAND = r"""#!/bin/bash
cd "$(dirname "$0")" || exit 1
./install.sh --then-run "$@"
status=$?
echo
read -r -p 'Press Enter to close...'
exit $status
"""


START_CMD = r"""@echo off
rem NOVA3 worker -- generated. Installs on first run, then starts working.
call "%~dp0install.cmd" --then-run %*
"""



def _mode(name: str) -> int:
    return 0o755 if name in ("install.sh", "START.sh", "START.command") else 0o644


def build_tar(repo: Path, out: Path, *, prov: dict) -> Path:
    files = tracked_files(repo)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        extra = {
            "GETTING-STARTED.txt": getting_started(prov).encode(),
            "install.sh": INSTALL_SH.encode(),
            "install.cmd": INSTALL_CMD.encode(),
            "START.sh": START_SH.encode(),
            "START.command": START_COMMAND.encode(),
            "START.cmd": START_CMD.encode(),
            ".nova3-dsh-loop": (json.dumps(prov, indent=2) + "\n").encode(),
        }
        for name in files:
            source = repo / name
            if not source.is_file():
                continue
            info = tar.gettarinfo(str(source), arcname=name)
            with source.open("rb") as handle:
                tar.addfile(info, handle)
        for name, payload in extra.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = _mode(name)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(payload))
    return out


def build_zip(repo: Path, out: Path, *, prov: dict) -> Path:
    files = tracked_files(repo)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in files:
            source = repo / name
            if source.is_file():
                bundle.write(source, name)
        extra = {
            "GETTING-STARTED.txt": getting_started(prov),
            "install.sh": INSTALL_SH,
            "install.cmd": INSTALL_CMD,
            "START.sh": START_SH,
            "START.command": START_COMMAND,
            "START.cmd": START_CMD,
        }
        for name, text in extra.items():
            info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
            info.external_attr = (_mode(name) | 0o100000) << 16
            bundle.writestr(info, text)
        bundle.writestr(".nova3-dsh-loop", json.dumps(prov, indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Build the source-only NOVA3 worker distribution")
    parser.add_argument("--repo", default=".", help="git checkout to package (tracked files only)")
    parser.add_argument("--out", default="dist/source", help="output directory")
    parser.add_argument("--repository", required=True, help="OWNER/REPO recorded in provenance")
    args = parser.parse_args(argv)
    manifest = build_all(Path(args.repo).resolve(), Path(args.out).resolve(),
                         repository=args.repository)
    print(json.dumps(manifest, indent=2))
    return 0


def build_all(repo: Path, outdir: Path, *, repository: str) -> dict:
    prov = provenance(repo, repository=repository)
    outdir.mkdir(parents=True, exist_ok=True)
    tar_path = build_tar(repo, outdir / "nova3-source.tar.gz", prov=prov)
    zip_path = build_zip(repo, outdir / "nova3-source.zip", prov=prov)
    manifest = {
        "provenance": prov,
        "files": {
            p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
            for p in (tar_path, zip_path)
        },
    }
    (outdir / "nova3-source.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                       encoding="utf-8")
    return manifest


if __name__ == "__main__":
    raise SystemExit(main())
