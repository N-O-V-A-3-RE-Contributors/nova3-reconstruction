"""Command line for the NOVA3 DeepSeek Harness loop.

Subcommands
-----------
``setup``    install/reconfigure this machine (the installer)
``run``      one bounded worker pass, in the foreground
``loop``     unattended passes until the budget or a stop condition
``status``   what the installation thinks, including a one-line live view
``logs``     tail the pass log and the live session pointer
``stop``     ask a running loop to stop after the current pass
``pause`` / ``resume``   arm or clear the STOP file between passes
``update``   fast-forward the source checkout
``doctor``   check every prerequisite and print exactly what is missing
``env``      launch a login shell with the recorded environment (secrets
             never leave the user's shell)

``nova3-dsh`` with no arguments behaves like ``setup`` the first time and
``loop`` afterwards, so a double-click starts doing work.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import credentials, installer, launch, loop, platforms as plat, resource
from .workspace import Repo


def _config(args: argparse.Namespace | None = None) -> Path:
    """Explicit --config, else this checkout's own config, else the machine one."""
    explicit = getattr(args, "config", None) if args is not None else None
    if explicit:
        return Path(explicit).expanduser()
    return launch.config_for_checkout()


def _install_state(config_path: Path) -> dict:
    return installer.load_state(config_path)


def _resolve_workspace(state: dict, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if state.get("workspace"):
        return Path(state["workspace"])
    here = Path.cwd()
    if (here / "AGENTS.md").is_file():
        return here
    raise SystemExit("no source checkout configured; run 'nova3-dsh setup' or pass --workspace")


def cmd_setup(args: argparse.Namespace) -> int:
    from . import wizard
    config_path = _config(args)
    settings = wizard.run_wizard(
        echo=print, assume_yes=args.yes, provider=args.provider, model=args.model,
        base_url=args.base_url, previous=installer.load_state(config_path))
    result = installer.Installer(
        state_path=config_path,
        install_root=Path(args.install_root).expanduser() if args.install_root else None,
        assume_yes=args.yes or not (sys.stdin and sys.stdin.isatty()),
        provider=settings["provider"], model=settings["model"], base_url=args.base_url,
        workspace=Path(args.workspace).expanduser() if args.workspace else None,
        github=args.repo, dsh_version=args.dsh_version,
        allow_network=not args.offline, api_key=settings.get("api_key"),
    ).run_all()
    print("")
    for step in result.steps:
        mark = "ok  " if step.ok else "FAIL"
        change = " (changed)" if step.changed else ""
        print(f"  [{mark}] {step.name:18s} {step.detail}{change}")
    print("")
    if result.failures():
        print("Setup did not complete. Fix the FAIL lines above and run setup again.")
        return 1
    if settings.get("api_key") and not credentials.key_status(settings["provider"]).present:
        wizard.offer_persistence(credentials.PROVIDERS[settings["provider"]]["env"])
        print("")
    print("Setup complete. Start the worker with:  nova3-dsh")
    print("Status, logs and guidance:               nova3-dsh status | logs | help")
    return 0


def _loop_config(args: argparse.Namespace, state: dict) -> loop.LoopConfig:
    workspace = _resolve_workspace(state, args.workspace)
    config = loop.LoopConfig(
        workspace=workspace,
        config_path=_config(args),
        max_passes=args.max_passes,
        max_minutes=args.max_minutes,
        pass_timeout=args.pass_timeout,
        max_failures=args.max_failures,
        max_stuck=args.max_stuck,
        auto_commit=not args.no_commit,
        github=state.get("github_repo") or state.get("repo"),
        seed_goal=args.goal or state.get("goal") or _default_goal(),
    )
    return config


def _default_goal() -> str:
    return (
        "Recover N.O.V.A. 3 as independently compilable, tested source code that reproduces the "
        "original game behaviour (see AGENTS.md for the full mission, non-negotiable end state and "
        "evidence rules). Work autonomously, in verifiable increments, until the game runs from "
        "this project's own source on the listed platforms."
    )


def cmd_run(args: argparse.Namespace) -> int:
    config_path = _config(args)
    state = _install_state(config_path)
    cfg = _loop_config(args, state)
    cfg.max_passes = 1
    return loop.Loop(cfg, install_state=state).run()


def cmd_loop(args: argparse.Namespace) -> int:
    config_path = _config(args)
    state = _install_state(config_path)
    cfg = _loop_config(args, state)
    return loop.Loop(cfg, install_state=state).run()


def cmd_status(args: argparse.Namespace) -> int:
    config_path = _config(args)
    state = _install_state(config_path)
    print(launch.status_text(state, config_path=config_path))
    workspace = state.get("workspace")
    if workspace and Path(workspace).is_dir():
        from .workspace import LoopState
        ws = LoopState(Path(workspace))
        data = ws.data
        live = ws.dir / "live" / "current.json"
        if live.exists():
            try:
                info = json.loads(live.read_text(encoding="utf-8"))
                print(f"  running:   pass {info.get('pass')} since {info.get('started')} ({info.get('phase')})")
            except ValueError:
                pass
        print(f"  loop:      {data.get('passes', 0)} pass(es) run; "
              f"stop reason: {data.get('stopped_reason') or '(none)'}")
        for record in ws.recent_passes(3):
            print(f"    #{record.get('pass'):>3} {'ok ' if record.get('ok') else 'ERR'} "
                  f"{(record.get('summary') or '')[:110]}")
    if args.json:
        print(json.dumps({"install": state}, indent=2, sort_keys=True))
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    config_path = _config(args)
    state = _install_state(config_path)
    workspace = state.get("workspace")
    if not workspace:
        print("no checkout configured")
        return 1
    logdir = Path(workspace) / ".local" / "dsh-loop" / "logs"
    files = sorted(logdir.glob("passes-*.log")) if logdir.is_dir() else []
    if not files:
        print(f"no pass logs yet in {logdir}")
        return 0
    text = files[-1].read_text(encoding="utf-8", errors="replace")
    print(f"--- {files[-1]} (last {args.lines} lines) ---")
    print("\n".join(text.splitlines()[-args.lines:]))
    return 0


def _loop_dir(state: dict) -> Path | None:
    workspace = state.get("workspace")
    return Path(workspace) / ".local" / "dsh-loop" if workspace else None


def cmd_stop(args: argparse.Namespace) -> int:
    config_path = _config(args)
    d = _loop_dir(_install_state(config_path))
    if not d:
        return 1
    d.mkdir(parents=True, exist_ok=True)
    (d / "STOP").write_text("stop requested from the command line\n", encoding="utf-8")
    print(f"stop requested; the running loop stops after its current pass ({d / 'STOP'})")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    return cmd_stop(args)


def cmd_resume(args: argparse.Namespace) -> int:
    config_path = _config(args)
    d = _loop_dir(_install_state(config_path))
    if d:
        try:
            (d / "STOP").unlink()
        except OSError:
            pass
        print("STOP cleared; relaunch 'nova3-dsh' to continue")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    config_path = _config(args)
    state = _install_state(config_path)
    workspace = state.get("workspace")
    if not workspace:
        print("no checkout configured")
        return 1
    repo = Repo(Path(workspace))
    if not repo.is_git:
        print("the configured workspace is not a git checkout; download a new source archive")
        return 1
    dirty = repo.dirty_paths()
    if dirty and not args.force:
        print(f"refusing to update: {len(dirty)} uncommitted path(s) (use --force to stash-free pull anyway)")
        return 1
    cp = subprocess.run(["git", "pull", "--ff-only", "origin", repo.branch()], cwd=workspace,
                        capture_output=True, text=True, check=False)
    print((cp.stdout or cp.stderr or "").strip()[-2000:])
    return cp.returncode


def cmd_env(args: argparse.Namespace) -> int:
    """Open a shell whose KEY names are announced, never their values."""
    config_path = _config(args)
    state = _install_state(config_path)
    provider = state.get("provider")
    if provider in credentials.PROVIDERS:
        st = credentials.key_status(provider)
        print(f"{st.env_name}: " + ("set " + st.fingerprint if st.present else "NOT SET"))
    shell = os.environ.get("SHELL") or ("powershell" if plat.WINDOWS else "/bin/sh")
    return subprocess.call([shell])


def cmd_doctor(args: argparse.Namespace) -> int:
    config_path = _config(args)
    state = _install_state(config_path)
    print("NOVA3 DSH loop doctor")
    print("  host: " + resource.describe())
    ok = plat.check_python()
    print(f"  python: {'ok' if ok[0] else 'FAIL'} -- {ok[1]}")
    problems = loop.check_state(state, Path(state.get("workspace") or "/nonexistent"), config_path)
    for line in problems:
        print("  problem: " + line)
    if not problems:
        print("  installation: ok")
    return 0 if (ok[0] and not problems) else 1


def cmd_help(args: argparse.Namespace) -> int:
    print(HELP)
    return 0


HELP = """\
NOVA3 reconstruction worker -- DeepSeek Harness loop

  nova3-dsh                 start (set up on first run, then work until done)
  nova3-dsh setup           install or repair this machine's worker setup
  nova3-dsh status          setup status, API-key fingerprint, recent passes
  nova3-dsh loop [opts]     unattended passes in this terminal
  nova3-dsh run             exactly one pass (diagnostic)
  nova3-dsh logs           tail the current day's pass log
  nova3-dsh stop|pause      stop after the current pass
  nova3-dsh resume          clear a stop request
  nova3-dsh update          fast-forward the checkout to the shared branch
  nova3-dsh doctor          explain every missing prerequisite
  nova3-dsh env             show which key variable is set and open a shell

Useful options:  --max-minutes N   --max-passes N   --pass-timeout SECONDS
                 --conservative    (light tools only)   --config PATH
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nova3-dsh", add_help=False)
    parser.add_argument("-h", "--help", action="store_true", dest="want_help")
    parser.add_argument("--config", default=None, help="path to the installer's JSON config")
    sub = parser.add_subparsers(dest="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", default=None, help="source checkout to work in")
        p.add_argument("--config", default=None, help="path to the installer's JSON config")

    p = sub.add_parser("setup")
    common(p)
    p.add_argument("--yes", action="store_true", help="accept every prompt (unattended)")
    p.add_argument("--provider", choices=tuple(credentials.PROVIDERS))
    p.add_argument("--model", default=None, help="model id served by your provider")
    p.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    p.add_argument("--repo", default=None, help="OWNER/REPO of the shared project")
    p.add_argument("--install-root", default=None)
    p.add_argument("--dsh-version", default=None, help="pin a different DeepSeek Harness version")
    p.add_argument("--offline", action="store_true", help="never download anything")

    def loop_args(p: argparse.ArgumentParser) -> None:
        common(p)
        p.add_argument("--max-passes", type=int, default=0)
        p.add_argument("--max-minutes", type=int, default=0)
        p.add_argument("--pass-timeout", type=int, default=90 * 60)
        p.add_argument("--max-failures", type=int, default=3)
        p.add_argument("--max-stuck", type=int, default=3)
        p.add_argument("--no-commit", action="store_true", help="never commit from the loop")
        p.add_argument("--goal", default=None, help="standing objective for the worker")

    p = sub.add_parser("run")
    loop_args(p)
    p = sub.add_parser("loop")
    loop_args(p)

    p = sub.add_parser("status")
    common(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("logs")
    common(p)
    p.add_argument("--lines", type=int, default=120)
    for name in ("stop", "pause", "resume", "update", "env"):
        p = sub.add_parser(name)
        common(p)
        if name == "update":
            p.add_argument("--force", action="store_true")
    p = sub.add_parser("doctor")
    common(p)
    sub.add_parser("help")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.want_help or args.command == "help":
        return cmd_help(args)
    handlers = {
        "setup": cmd_setup, "run": cmd_run, "loop": cmd_loop, "status": cmd_status,
        "logs": cmd_logs, "stop": cmd_stop, "pause": cmd_pause, "resume": cmd_resume,
        "update": cmd_update, "env": cmd_env, "doctor": cmd_doctor,
    }
    if not args.command:
        parser.print_help()
        return 2
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except installer.InstallError as exc:
        print("\nSETUP ERROR: " + str(exc), file=sys.stderr)
        return 1


def _spec(spec: list | tuple) -> tuple[str | None, Path]:
    checkout = spec[0] if len(spec) > 0 else None
    config = Path(spec[1]) if len(spec) > 1 and spec[1] else plat.default_config_path()
    return checkout, config


def launcher_main(spec: list | tuple, argv: list[str]) -> int:
    """Entry point called by the generated ``nova3-dsh`` launcher.

    No arguments: set up on first run, otherwise continue working. The launcher
    passes the checkout it was built for, so this also works from any directory.
    """
    checkout, config = _spec(spec)
    if checkout and Path(checkout).is_dir():
        try:
            os.chdir(checkout)
        except OSError:
            pass
    if argv and argv[0] not in ("",):
        return main(argv)
    state = installer.load_state(config)
    if not state.get("marker"):
        return cmd_setup(argparse.Namespace(
            config=str(config), workspace=checkout, install_root=None, yes=False,
            provider=None, model=None, base_url=None, repo=None, dsh_version=None,
            offline=False))
    status = credentials.key_status(state.get("provider") or "openrouter")
    if not status.present:
        print(f"{status.env_name} is not set in this terminal.")
        print("Run 'nova3-dsh setup' to configure the key, or export it yourself and retry.")
        return 2
    if not sys.stdin or not sys.stdin.isatty():
        # Double-clicked without a terminal: keep the window open on exit.
        try:
            return main(["loop", "--config", str(config)])
        finally:
            input("Press Enter to close this window...")
    return main(["loop", "--config", str(config)])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
