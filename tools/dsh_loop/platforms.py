"""Platform facts and per-OS command construction for the NOVA3 DSH loop.

Everything in this module is a pure function or a thin ``subprocess`` wrapper.
Nothing here reads project state; ``installer.py`` owns that.

Supported platforms: Linux (x86_64/aarch64), macOS (Apple silicon and Intel),
Windows (x86_64). Each fact the installer needs -- Node archive names, GitHub
CLI package ids, DSH home location -- is derived here so the rest of the code
never string-matches ``sys.platform``.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

PYTHON_MIN = (3, 10)


class PlatformError(RuntimeError):
    """A platform assumption did not hold; the user must act."""


@dataclass(frozen=True)
class Facts:
    os_name: str          # "linux" | "macos" | "windows"
    arch: str             # normalised: x86_64 | aarch64
    machine: str          # platform.machine() verbatim, for reports

    @property
    def is_windows(self) -> bool:
        return self.os_name == "windows"

    @property
    def is_macos(self) -> bool:
        return self.os_name == "macos"


def facts() -> Facts:
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64",
            "aarch64": "aarch64"}.get(machine, machine)
    if WINDOWS:
        return Facts("windows", arch, machine)
    if MACOS:
        return Facts("macos", arch, machine)
    if LINUX:
        return Facts("linux", arch, machine)
    raise PlatformError(
        f"unsupported operating system '{sys.platform}'. "
        "The NOVA3 DSH loop supports Linux, macOS and Windows.")


def check_python() -> tuple[bool, str]:
    if sys.version_info < PYTHON_MIN:
        return False, ("Python {}.{} or newer is required; this interpreter is {}.{}".format(
            PYTHON_MIN[0], PYTHON_MIN[1], sys.version_info[0], sys.version_info[1]))
    return True, "Python {}.{}.{}".format(*sys.version_info[:3])


def node_archive_name(version: str, f: Facts | None = None) -> str:
    """Return the official nodejs.org release archive name for this platform."""
    f = f or facts()
    if f.is_windows:
        if f.arch != "x86_64":
            raise PlatformError(f"Node.js prebuilt builds for Windows {f.arch} are not published")
        return f"node-{version}-win-x64.zip"
    if f.is_macos:
        if f.arch == "aarch64":
            return f"node-{version}-darwin-arm64.tar.gz"
        if f.arch == "x86_64":
            return f"node-{version}-darwin-x64.tar.gz"
        raise PlatformError(f"unsupported macOS architecture {f.arch}")
    if f.arch == "x86_64":
        return f"node-{version}-linux-x64.tar.xz"
    if f.arch == "aarch64":
        return f"node-{version}-linux-arm64.tar.xz"
    raise PlatformError(f"no Node.js build is published for Linux {f.arch}")


def node_bin_name() -> str:
    return "node.exe" if WINDOWS else "node"


def npm_bin_name() -> str:
    return "npm.cmd" if WINDOWS else "npm"


def npm_command(npm: Path | str) -> list[str]:
    """argv prefix that runs npm without a shell on every OS."""
    if WINDOWS and str(npm).lower().endswith(".cmd"):
        comspec = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
        return [comspec, "/d", "/s", "/c", str(npm)]
    return [str(npm)]


def gh_package_ids(f: Facts) -> list[str]:
    """Best-effort GitHub CLI install via the platform package manager."""
    if f.is_windows:
        return ["winget", "install", "--id", "GitHub.cli", "-e",
                "--accept-package-agreements", "--accept-source-agreements"]
    if f.is_macos:
        return ["brew", "install", "gh"]
    if shutil.which("apt-get"):
        return ["sudo", "apt-get", "install", "-y", "gh"]
    if shutil.which("dnf"):
        return ["sudo", "dnf", "install", "-y", "gh"]
    if shutil.which("pacman"):
        return ["sudo", "pacman", "-S", "--needed", "--noconfirm", "github-cli"]
    return []


def node_package_ids(f: Facts) -> list[str]:
    if f.is_windows:
        return ["winget", "install", "--id", "OpenJS.NodeJS.LTS", "-e",
                "--accept-package-agreements", "--accept-source-agreements"]
    if f.is_macos:
        return ["brew", "install", "node"]
    if shutil.which("apt-get"):
        return ["sudo", "apt-get", "install", "-y", "nodejs", "npm"]
    if shutil.which("dnf"):
        return ["sudo", "dnf", "install", "-y", "nodejs", "npm"]
    if shutil.which("pacman"):
        return ["sudo", "pacman", "-S", "--needed", "--noconfirm", "nodejs", "npm"]
    return []


def git_package_ids(f: Facts) -> list[str]:
    if f.is_windows:
        return ["winget", "install", "--id", "Git.Git", "-e",
                "--accept-package-agreements", "--accept-source-agreements"]
    if f.is_macos:
        return ["brew", "install", "git"]
    if shutil.which("apt-get"):
        return ["sudo", "apt-get", "install", "-y", "git"]
    if shutil.which("dnf"):
        return ["sudo", "dnf", "install", "-y", "git"]
    if shutil.which("pacman"):
        return ["sudo", "pacman", "-S", "--needed", "--noconfirm", "git"]
    return []


def find_executable(name: str, extra_dirs: Iterable[Path] = ()) -> str | None:
    """PATH lookup that also considers directories created by this installer."""
    for directory in extra_dirs:
        candidate = Path(directory) / (name + (".exe" if WINDOWS else ""))
        if candidate.is_file():
            return str(candidate)
        if WINDOWS:
            candidate = Path(directory) / (name + ".cmd")
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def default_install_root() -> Path:
    """Where the installer puts the DSH runtime, Node and generated launchers."""
    override = os.environ.get("NOVA3_DSH_INSTALL")
    if override:
        return Path(override).expanduser().resolve()
    if WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "Nova3" / "dsh"
    if MACOS:
        return Path.home() / "Library" / "Application Support" / "Nova3" / "dsh"
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "nova3" / "dsh"


def default_config_path() -> Path:
    override = os.environ.get("NOVA3_DSH_CONFIG")
    if override:
        return Path(override).expanduser()
    if WINDOWS:
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Nova3" / "dsh-loop.json"
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "nova3" / "dsh-loop.json"


def user_scripts_dir() -> Path:
    """Where console launchers are placed (on PATH for a default install)."""
    if WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "Nova3" / "bin"
    return Path.home() / ".local" / "bin"


def make_executable(path: Path) -> None:
    if WINDOWS:
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json_private(path: Path, data: dict) -> None:
    """Atomically write owner-only JSON (never contains secrets by design)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not WINDOWS:
        tmp.chmod(0o600)
    os.replace(tmp, path)


def subprocess_env_with(path_dirs: Sequence[Path]) -> dict:
    """Environment with extra directories prepended to PATH."""
    env = dict(os.environ)
    parts = [str(p) for p in path_dirs if str(p)]
    if parts:
        env["PATH"] = os.pathsep.join(parts + [env.get("PATH", "")])
    return env


def run(argv: Sequence[str], *, cwd: Path | None = None, env: dict | None = None,
        timeout: float | None = 600, input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run a command without a shell and capture text output."""
    return subprocess.run(list(argv), cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, timeout=timeout,
                          input=input_text, check=False)


def current_platform_tag() -> str:
    """A stable tag used in release bundles, e.g. ``linux-x64``."""
    f = facts()
    arch = {"x86_64": "x64", "aarch64": "arm64"}.get(f.arch, f.arch)
    return f"{f.os_name}-{arch}"


def python_platform_tag() -> str:
    """Tag of the interpreter running this code (for PyInstaller artefacts)."""
    if WINDOWS:
        return "windows-x64" if sys.maxsize > 2**32 else "windows-x86"
    if MACOS:
        return "macos-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "macos-x64"
    if LINUX:
        return "linux-arm64" if platform.machine().lower() in ("aarch64", "arm64") else "linux-x64"
    return sysconfig.get_platform()
