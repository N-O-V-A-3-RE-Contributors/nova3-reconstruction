#!/usr/bin/env python3
"""Bootstrap entry point shipped inside the source distribution archive.

The archive cannot assume it was cloned with Git, cannot assume the project is
on ``sys.path``, and must work under a bare ``python3`` on Linux, macOS and
Windows. This script is therefore dependency-free and adds its owning tree to
``sys.path`` itself before handing over to ``tools.dsh_loop.cli``.

Usage (as called by ``install.sh`` / ``install.cmd`` / ``START.*``)::

    install.py --workspace <extracted folder> [--then-run]

``--then-run`` chains into the worker loop after a successful install, which is
what a contributor expects when double-clicking ``START.cmd``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main(argv: list[str]) -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required (this interpreter is {}.{}).".format(
            *sys.version_info[:2]), file=sys.stderr)
        return 2
    if any(flag in argv for flag in ("-h", "--help")):
        # Help must work without an install and without network setup.
        cli_help = __doc__
        print(cli_help or "")
        print("Options: --yes  --provider openrouter|deepseek  --model ID  --base-url URL\n"
              "         --repo OWNER/REPO  --install-root DIR  --dsh-version V  --offline\n"
              "         --workspace DIR  --then-run (start working after setup)")
        return 0
    try:
        from tools.dsh_loop import cli
    except Exception as exc:  # noqa: BLE001 - explain, never traceback at a double-click
        print("NOVA3 installer: cannot load tools/dsh_loop from:\n  " + str(REPO_ROOT), file=sys.stderr)
        print("  " + str(exc), file=sys.stderr)
        return 2

    args = list(argv)
    then_run = "--then-run" in args
    workspace = str(REPO_ROOT)
    passthrough: list[str] = []
    iterator = iter([a for a in args if a != "--then-run"])
    for token in iterator:
        if token == "--workspace":
            workspace = next(iterator, None) or workspace
            continue
        passthrough.append(token)
    setup_args = ["setup", "--workspace", workspace, *passthrough]
    code = cli.main(setup_args)
    if code != 0 or not then_run:
        return code
    print("")
    print("Starting the reconstruction worker. Close this window to stop after the current pass.")
    return cli.main(["loop", "--workspace", workspace])


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nsetup cancelled")
        sys.exit(130)
