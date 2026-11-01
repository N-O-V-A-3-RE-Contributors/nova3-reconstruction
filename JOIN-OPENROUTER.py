#!/usr/bin/env python3
"""Start the existing NOVA3 GitHub-leased Hermes worker (no Paperclip)."""
from __future__ import annotations
import argparse
import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REPO = "N-O-V-A-3-RE-Contributors/nova3-reconstruction"
MODEL = "deepseek/deepseek-v4.1-flash-1017"


def run(argv, **kw):
    return subprocess.run(argv, check=True, **kw)


def yes(prompt):
    return input(prompt + " [Y/n] ").strip().lower() not in ("n", "no")


def hermes_path():
    options = [shutil.which("hermes")]
    if os.name != "nt":
        options += [str(Path.home() / ".local/bin/hermes"),
                    str(Path.home() / ".hermes/hermes-agent/.hermes/bin/hermes")]
    else:
        options += [str(Path.home() / "AppData/Local/hermes/bin/hermes.exe")]
    return next((p for p in options if p and Path(p).is_file()), None)


def install_hermes():
    found = hermes_path()
    if found:
        return found
    if not yes("Install Hermes CLI using the official Nous Research installer?"):
        raise RuntimeError("Hermes required. See https://hermes-agent.nousresearch.com/docs/getting-started/installation")
    url = ("https://hermes-agent.nousresearch.com/install.ps1" if os.name == "nt"
           else "https://hermes-agent.nousresearch.com/install.sh")
    with tempfile.TemporaryDirectory(prefix="nova3-install-") as tmp:
        installer = Path(tmp) / ("install.ps1" if os.name == "nt" else "install.sh")
        with urllib.request.urlopen(url, timeout=40) as response:
            data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise RuntimeError("Unexpectedly large installer; refusing")
        installer.write_bytes(data)
        if os.name == "nt":
            run(["powershell", "-NoProfile", "-File", str(installer)])
        else:
            run(["bash", str(installer)])
    found = hermes_path()
    if not found:
        raise RuntimeError("Hermes installed but not in PATH. Reopen terminal and rerun")
    return found


def configure_openrouter():
    if os.environ.get("OPENROUTER_API_KEY"):
        print("Using OPENROUTER_API_KEY from current environment")
        return
    target = Path.home() / ".hermes" / ".env"
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    if any(s.strip().startswith("OPENROUTER_API_KEY=") and
           s.split("=", 1)[1].strip() for s in text.splitlines()):
        print("Using existing Hermes OpenRouter key (not displayed)")
        return
    print("Enter a dedicated, spending-limited OpenRouter key, NOT your GitHub token.")
    secret = getpass.getpass("OpenRouter API key (hidden): ").strip()
    if not secret or any(c.isspace() for c in secret) or "\x00" in secret:
        raise RuntimeError("Empty or invalid API key; nothing saved")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        target.parent.chmod(0o700)
    filtered = [s for s in text.splitlines() if not s.startswith("OPENROUTER_API_KEY=")]
    filtered.append("OPENROUTER_API_KEY=" + secret)
    fd, temp = tempfile.mkstemp(prefix=".nova3-key-", dir=target.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(filtered) + "\n")
        os.replace(temp, target)
        if os.name != "nt":
            target.chmod(0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print("OpenRouter key saved privately to ~/.hermes/.env; nothing entered Git")


def main():
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10+ required")
    p = argparse.ArgumentParser(description="NOVA3 two-computer OpenRouter worker")
    p.add_argument("--check", action="store_true", help="Report queue status without claiming tasks")
    p.add_argument("--once", action="store_true", help="Attempt at most one task")
    p.add_argument("--model", default=MODEL, help="Your OpenRouter model")
    args = p.parse_args()
    if not args.model or any(c.isspace() for c in args.model):
        raise RuntimeError("Invalid model")
    root = Path(__file__).resolve().parent
    if not (root / "nova3_team/worker.py").is_file():
        raise RuntimeError("Run from the full GitHub repo checkout, not an isolated file")
    os.chdir(root)
    # Existing repo bootstrap: installs missing tools only with the user's consent.
    from nova3_easy import ensure_dependencies, github_login, local_identity
    ensure_dependencies()
    gh = github_login()
    view = subprocess.run([gh, "repo", "view", REPO, "--json", "nameWithOwner",
                           "--jq", ".nameWithOwner"], capture_output=True, text=True)
    if view.returncode or view.stdout.strip().lower() != REPO.lower():
        raise RuntimeError("Your GitHub account cannot access the NOVA3 repo")
    local_identity(gh, root)
    hermes = install_hermes()
    configure_openrouter()
    env = dict(os.environ)
    env["PATH"] = str(Path(hermes).parent) + os.pathsep + env.get("PATH", "")
    env.update(NOVA3_HERMES_EXECUTABLE=hermes, NOVA3_HERMES_PROVIDER="openrouter",
               NOVA3_HERMES_MODEL=args.model, NOVA3_HERMES_MAX_TURNS="96",
               NOVA3_AI_SESSION_TIMEOUT="1800")
    cmd = [sys.executable, "-m", "nova3_team.worker", "status" if args.check else "run",
           "--repo", REPO]
    if not args.check:
        cmd += ["--provider", "hermes", "--rounds", "3"]
        if args.once:
            cmd += ["--once"]
    print("Shared task queue:", REPO)
    print("Only owner/admin-approved tasks may be claimed. PRs are never auto-merged.")
    print("One local Hermes worker. Ctrl+C stops; rerun to resume.")
    run(cmd, cwd=root, env=env)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print("SETUP STOPPED:", str(error), file=sys.stderr)
        sys.exit(2)
