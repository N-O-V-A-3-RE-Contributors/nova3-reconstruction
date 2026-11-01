#!/usr/bin/env python3
"""Entry point for the packaged (PyInstaller) NOVA3 worker launcher.

The packaged binary exists so a contributor with no Python can double-click a
file and get working. It is **not** a second implementation: it embeds this
same standard-library package and calls the same ``cli``. Release bundles also
ship the platform-independent source archive, which is the route used when the
embedded launcher cannot reach the network to bootstrap the worker.

``--self-test`` verifies that the frozen interpreter can import every module and
build the launcher text without touching the network; CI runs it on every
platform before an archive is published.
"""

from __future__ import annotations

import os
import sys


def _bootstrap_path() -> None:
    if hasattr(sys, "_MEIPASS"):
        sys.path.insert(0, sys._MEIPASS)  # type: ignore[attr-defined]
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    if root not in sys.path:
        sys.path.insert(0, root)


_bootstrap_path()

from tools.dsh_loop import cli, credentials, installer, launch, loop, packager, wizard  # noqa: E402


def self_test() -> int:
    problems = []
    if sys.version_info < (3, 10):
        problems.append("frozen interpreter is older than Python 3.10")
    try:
        state = installer.default_state()
        state["workspace"] = "/tmp/nova3"
        # Exercise the real code paths the launcher uses, without network or
        # credentials: state defaults, fingerprinting and launcher generation.
        import tempfile
        from pathlib import Path
        assert credentials.fingerprint("sk-test-1234").startswith("…")
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "dsh-loop.json"
            installer.save_state(state_path, state)
            rendered = launch.unix_launcher_text(state_path)
            assert "launcher_main" in rendered
            assert installer.load_state(state_path)["workspace"] == "/tmp/nova3"
        assert callable(loop.Loop)
        assert callable(packager.build_all)
        assert callable(wizard.run_wizard)
        assert callable(cli.launcher_main)
    except Exception as exc:  # noqa: BLE001 - self-test must report, not crash
        problems.append(str(exc))
    if problems:
        for problem in problems:
            print("self-test FAILED: " + problem, file=sys.stderr)
        return 1
    print("NOVA3 worker launcher self-test OK (Python %d.%d)" % sys.version_info[:2])
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    if "--version" in argv:
        print("nova3-dsh worker launcher")
        return 0
    return cli.main([a for a in argv if a != "--frozen"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
