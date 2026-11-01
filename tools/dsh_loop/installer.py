"""Idempotent per-user installer for the NOVA3 DeepSeek Harness loop.

What installation means on a contributor machine:

1. ``git`` present (required to obtain the source; not installed silently on
   Windows because the official GitHub releases are already available).
2. ``node`` + ``npm`` at a pinned major version. A system Node is reused when
   it satisfies the pin; otherwise ``nodejs.org`` GitHub releases are fetched
   into the install root and used without touching the system installation.
3. A pinned ``@deepseek-ai/dsh`` runtime in ``<install>/runtime`` (npm,
   registry integrity checked by npm itself).
4. A DSH profile ``nova3-worker`` under ``<install>/dsh-home`` containing only
   ``dsh-base`` + ``dsh-headless`` and a generated user patch layer that routes
   the model through the contributor's own key (name only -- the value stays in
   their environment).
5. Console launchers ``nova3-dsh`` / ``nova3-dsh.cmd`` in ``~/.local/bin``
   (or ``%LOCALAPPDATA%\\Nova3\\bin`` on Windows) and Start-menu/desktop
   shortcuts on Windows/macOS.

Every step is a check-then-act: re-running the installer repairs a missing or
damaged piece and leaves a healthy one untouched. The installer never reads or
writes an existing credential file, never overwrites a profile or config that
contains user edits it did not generate, and never requires root.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from . import platforms as plat
from . import credentials, resource

DSH_PACKAGE = "@deepseek-ai/dsh"
DSH_PINNED_VERSION = "0.2.1-rc.5"
NODE_PINNED_MAJOR = 22
PROFILE_NAME = "nova3-worker"
PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "profile"

MARKER = "# NOVA3 DSH loop state"
REPO_MARKER_FILE = ".nova3-dsh-loop"  # written into THIS checkout only

Echo = Callable[[str], None]


class InstallError(RuntimeError):
    pass


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    changed: bool = False


@dataclass
class InstallResult:
    steps: list[Step] = field(default_factory=list)
    config_path: Path | None = None
    install_root: Path | None = None
    ready: bool = False

    def add(self, name: str, ok: bool, detail: str, changed: bool = False) -> Step:
        step = Step(name, ok, detail, changed)
        self.steps.append(step)
        return step

    def failures(self) -> list[Step]:
        return [s for s in self.steps if not s.ok]

    def to_json(self) -> dict:
        return {
            "ready": self.ready,
            "config_path": str(self.config_path) if self.config_path else None,
            "install_root": str(self.install_root) if self.install_root else None,
            "steps": [s.__dict__ for s in self.steps],
        }


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def default_state() -> dict:
    return {
        "marker": MARKER,
        "schema": 1,
        "provider": None,             # "openrouter" | "deepseek"
        "api_key_env": None,          # variable NAME only; value never stored
        "base_url": None,
        "model": None,
        "model_name": None,
        "context_window": None,
        "max_tokens": None,
        "install_root": None,
        "dsh_home": None,
        "dsh_profile": PROFILE_NAME,
        "dsh_version": None,
        "node_home": None,
        "workspace": None,
        "workspace_kind": None,       # "checkout" | "clone" | "absent"
        "github_login": None,
        "launcher_paths": [],
        "launcher_mode": None,        # "source" | "frozen"
        "archive_sha256": None,       # local provenance of the distribution archive
        "python": None,
        "platform": None,
    }


def load_state(path: Path) -> dict:
    data = plat.read_json(path)
    if data.get("marker") != MARKER:
        return default_state()
    base = default_state()
    base.update({k: v for k, v in data.items() if k in base})
    return base


def save_state(path: Path, state: dict) -> None:
    plat.write_json_private(path, state)


# ---------------------------------------------------------------------------
# network
# ---------------------------------------------------------------------------

def http_get(url: str, *, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "nova3-dsh-loop-installer"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URLs
        return response.read()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract_tar(archive: Path, dest: Path) -> None:
    """Extract a tar archive, refusing absolute paths and traversal."""
    dest = dest.resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest) + os.sep) and target != dest:
                raise InstallError(f"archive member escapes destination: {member.name}")
            if member.issym() or member.islnk():
                link = (target.parent / member.linkname).resolve()
                if not str(link).startswith(str(dest)):
                    raise InstallError(f"archive link escapes destination: {member.name}")
        tar.extractall(dest)  # noqa: S202 - members validated above


def safe_extract_zip(archive: Path, dest: Path) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest) + os.sep) and target != dest:
                raise InstallError(f"archive member escapes destination: {info.filename}")
        bundle.extractall(dest)  # noqa: S202 - members validated above


# ---------------------------------------------------------------------------
# installer
# ---------------------------------------------------------------------------

class Installer:
    def __init__(self, *, state_path: Path | None = None, install_root: Path | None = None,
                 echo: Echo = print, assume_yes: bool = False,
                 provider: str | None = None, model: str | None = None,
                 base_url: str | None = None, workspace: Path | None = None,
                 github: str | None = None, dsh_version: str | None = None,
                 allow_network: bool = True, api_key: str | None = None) -> None:
        self.state_path = (state_path or plat.default_config_path()).expanduser()
        self.state = load_state(self.state_path)
        if install_root:
            self.state["install_root"] = str(install_root.expanduser().resolve())
        self.install_root = Path(self.state["install_root"] or plat.default_install_root())
        self.state["install_root"] = str(self.install_root)
        self.echo = echo
        self.assume_yes = assume_yes
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.workspace_override = workspace
        self.github = github
        self.dsh_version = dsh_version or self.state.get("dsh_version") or DSH_PINNED_VERSION
        self.allow_network = allow_network
        # Used for one in-memory verification only; never persisted or logged.
        self.api_key = api_key
        self.result = InstallResult(config_path=self.state_path, install_root=self.install_root)
        self.facts = plat.facts()

    # -- helpers -------------------------------------------------------------
    @property
    def runtime_dir(self) -> Path:
        return self.install_root / "runtime"

    @property
    def dsh_home(self) -> Path:
        return Path(self.state.get("dsh_home") or (self.install_root / "dsh-home"))

    @property
    def profile_dir(self) -> Path:
        return self.dsh_home / "profiles" / PROFILE_NAME

    @property
    def node_dir(self) -> Path:
        return Path(self.state.get("node_home") or (self.install_root / "node"))

    def dsh_entry(self) -> Path:
        return self.runtime_dir / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"

    def _confirm(self, question: str, default: bool = True) -> bool:
        if self.assume_yes:
            return True
        if not sys.stdin or not sys.stdin.isatty():
            return default
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            answer = input(question + suffix).strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        return answer in ("y", "yes")

    def _run(self, argv: Sequence[str], *, cwd: Path | None = None, env: dict | None = None,
             timeout: float = 900) -> plat.subprocess.CompletedProcess:
        return plat.run(argv, cwd=cwd, env=env, timeout=timeout)

    # -- steps ---------------------------------------------------------------
    def check_python(self) -> None:
        ok, detail = plat.check_python()
        self.result.add("python", ok, detail)
        if not ok:
            raise InstallError(detail)

    def check_root(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise InstallError(
                "do not run the installer as root/with sudo: everything is installed "
                "for the current user into " + str(self.install_root))

    def check_resources(self) -> None:
        reason = resource.disk_pressure(resource.disk(self.install_root.parent if self.install_root.parent.exists() else Path.home()), 1.5)
        mem = resource.memory()
        mem_reason = resource.pressure(mem, 300)
        detail = resource.describe()
        if reason or mem_reason:
            # Not fatal: installation is light-weight compared with analysis,
            # but the user deserves to know before a long download.
            self.echo(f"  note: {reason or mem_reason}")
        self.result.add("resources", True, detail)

    def check_git(self) -> None:
        git = plat.find_executable("git")
        if git:
            cp = self._run([git, "--version"], timeout=30)
            self.result.add("git", cp.returncode == 0, (cp.stdout or "").strip(), False)
            return
        install = plat.git_package_ids(self.facts)
        if not install or not self._confirm(
                "git is not installed. Install it with your system package manager? "
                f"({' '.join(install)})"):
            self.result.add("git", False, "git is required to obtain the source checkout")
            raise InstallError("install git, then run the installer again")
        cp = self._run(install)
        found = plat.find_executable("git", [Path("/usr/bin"), Path("/usr/local/bin")])
        self.result.add("git", bool(found) and cp.returncode == 0,
                        found or (cp.stderr or cp.stdout or "").strip(), True)
        if not found:
            raise InstallError("git installation did not produce a git executable; "
                               "open a new terminal and retry")

    def _node_candidates(self) -> list[Path]:
        if plat.WINDOWS:
            return [self.node_dir / "node.exe"]
        return [self.node_dir / "bin" / "node"]

    def _node_version(self, node: Path) -> tuple[int, int, int] | None:
        cp = self._run([str(node), "--version"], timeout=30)
        if cp.returncode != 0:
            return None
        match = re.match(r"v(\d+)\.(\d+)\.(\d+)", (cp.stdout or "").strip())
        return tuple(int(x) for x in match.groups()) if match else None

    def check_node(self) -> None:
        """Reuse a suitable system Node, else install a private pinned one."""
        for candidate in self._node_candidates():
            if candidate.is_file():
                version = self._node_version(candidate)
                if version and version[0] >= NODE_PINNED_MAJOR - 2:
                    self.state["node_home"] = str(self.node_dir)
                    self.result.add("node", True,
                                    f"private Node {'.'.join(map(str, version))} ({candidate})", False)
                    return
        system = plat.find_executable("node")
        if system:
            version = self._node_version(Path(system))
            if version and version[0] >= NODE_PINNED_MAJOR:
                self.state["node_home"] = None
                self.result.add("node", True,
                                f"system Node {'.'.join(map(str, version))} ({system})")
                return
            if version and version[0] < 18:
                detail = (f"system Node is too old ({'.'.join(map(str, version))}); "
                          "a private Node is installed instead")
            else:
                detail = ""
        else:
            detail = ""
        self._install_private_node(detail)

    def _node_download_url(self, version: str) -> tuple[str, str]:
        name = plat.node_archive_name(version, self.facts)
        return f"https://nodejs.org/dist/{version}/{name}", name

    def _latest_node_version(self) -> str:
        index = http_get("https://nodejs.org/dist/index.json", timeout=60).decode("utf-8")
        data = json.loads(index)
        majors = [entry for entry in data if int(str(entry["version"]).lstrip("v").split(".")[0]) == NODE_PINNED_MAJOR]
        if not majors:
            raise InstallError(f"no Node.js {NODE_PINNED_MAJOR}.x release is listed on nodejs.org")
        candidate = majors[0]["version"]
        name = plat.node_archive_name(candidate, self.facts)
        try:
            http_get(f"https://nodejs.org/dist/{candidate}/{name}.sha256sum", timeout=30)
        except OSError:
            raise InstallError(f"{name} has no published checksum; refusing to install it") from None
        return candidate

    def _install_private_node(self, why: str) -> None:
        if not self.allow_network:
            raise InstallError("Node.js is missing and network access was disabled")
        if not self._confirm("Download a private, pinned Node.js runtime for the worker loop?"):
            raise InstallError("Node.js is required; installation declined")
        version = self._latest_node_version()
        url, name = self._node_download_url(version)
        self.echo(f"  downloading {url}")
        payload = http_get(url, timeout=300)
        expected = http_get(f"https://nodejs.org/dist/{version}/{name}.sha256sum", timeout=30).decode("ascii").split()[0]
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise InstallError(f"Node.js archive checksum mismatch for {name}")
        self.node_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(self.install_root)) as tmp:
            archive = Path(tmp) / name
            archive.write_bytes(payload)
            staging = Path(tmp) / "unpack"
            staging.mkdir()
            if name.endswith(".zip"):
                safe_extract_zip(archive, staging)
            else:
                safe_extract_tar(archive, staging)
            inner = [p for p in staging.iterdir() if p.is_dir()]
            if len(inner) != 1:
                raise InstallError("unexpected Node.js archive layout")
            if self.node_dir.exists():
                shutil.rmtree(self.node_dir)
            shutil.move(str(inner[0]), str(self.node_dir))
        node = self._node_candidates()[0]
        version_tuple = self._node_version(node)
        if not version_tuple:
            raise InstallError("private Node.js runtime did not execute")
        self.state["node_home"] = str(self.node_dir)
        self.result.add("node", True,
                        (why + "; " if why else "") + f"private Node {version} ({node})", True)

    def node_paths(self) -> list[Path]:
        if self.state.get("node_home"):
            if plat.WINDOWS:
                return [self.node_dir]
            return [self.node_dir / "bin"]
        return []

    def check_npm(self) -> Path:
        env = plat.subprocess_env_with(self.node_paths())
        npm = plat.find_executable("npm", self.node_paths())
        if not npm:
            raise InstallError("npm was not found next to node; reinstall Node.js")
        cp = plat.run(plat.npm_command(npm) + ["--version"], env=env, timeout=120)
        ok = cp.returncode == 0
        self.result.add("npm", ok, (cp.stdout or cp.stderr or "").strip().splitlines()[-1] if (cp.stdout or cp.stderr) else "")
        if not ok:
            raise InstallError("npm does not run; reinstall Node.js")
        return Path(npm)

    def check_runtime(self, npm: Path) -> None:
        entry = self.dsh_entry()
        env = plat.subprocess_env_with(self.node_paths())
        if entry.is_file():
            cp = plat.run([str(self._node_exe()), str(entry), "--version"], env=env, timeout=120)
            have = (cp.stdout or "").strip()
            if cp.returncode == 0 and have == self.dsh_version:
                self.result.add("dsh-runtime", True, f"{DSH_PACKAGE} {have} at {self.runtime_dir}")
                return
            if cp.returncode == 0 and have:
                self.echo(f"  runtime has {have}, pin wants {self.dsh_version}; reinstalling")
        if not self.allow_network:
            raise InstallError("the pinned DeepSeek Harness runtime is missing and network access is disabled")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.runtime_dir / "package.json"
        manifest.write_text(json.dumps({
            "name": "nova3-dsh-runtime",
            "private": True,
            "description": "Pinned DeepSeek Harness runtime for the NOVA3 worker loop.",
            "dependencies": {DSH_PACKAGE: self.dsh_version},
        }, indent=2) + "\n", encoding="utf-8")
        # The harness dependency tree is large; slow links need generous
        # fetch timeouts or npm aborts mid-unpack with ETIMEDOUT.
        cp = plat.run(plat.npm_command(npm) + [
            "install", "--no-audit", "--no-fund", "--loglevel=warn",
            "--fetch-timeout=600000", "--fetch-retries=8", "--maxsockets=4"],
                      cwd=self.runtime_dir, env=env, timeout=3600)
        if cp.returncode != 0:
            raise InstallError("npm install of the DeepSeek Harness runtime failed:\n"
                               + credentials.mask_embedded_secrets((cp.stderr or cp.stdout or "")[-2000:]))
        cp = plat.run([str(self._node_exe()), str(self.dsh_entry()), "--version"], env=env, timeout=120)
        have = (cp.stdout or "").strip()
        if cp.returncode != 0 or have != self.dsh_version:
            raise InstallError(f"runtime installed but reports '{have or cp.stderr}'; expected {self.dsh_version}")
        self.result.add("dsh-runtime", True, f"{DSH_PACKAGE} {have} installed at {self.runtime_dir}", True)

    def _node_exe(self) -> Path:
        for candidate in self._node_candidates():
            if candidate.is_file():
                return candidate
        system = plat.find_executable("node")
        if not system:
            raise InstallError("node disappeared after installation")
        return Path(system)

    def resolve_provider(self) -> None:
        """Pick the model route: flags > existing state > interactive default."""
        if not self.provider:
            self.provider = self.state.get("provider")
        if self.provider and self.provider not in credentials.PROVIDERS:
            raise InstallError(f"unknown provider '{self.provider}'; choose openrouter or deepseek")
        if not self.provider:
            if self.assume_yes or not (sys.stdin and sys.stdin.isatty()):
                self.provider = "openrouter"
            else:
                self.echo("")
                self.echo("Which model provider should the worker use?")
                for index, (name, spec) in enumerate(credentials.PROVIDERS.items(), start=1):
                    self.echo(f"  {index}. {spec['label']}")
                answer = input("  select [1]: ").strip() or "1"
                keys = list(credentials.PROVIDERS)
                self.provider = keys[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(keys) else "openrouter"
        spec = credentials.PROVIDERS[self.provider]
        # Flags win; otherwise a saved route for the same provider is kept so an
        # explicit --model survives later repair runs.
        saved_provider = self.state.get("provider")
        same = saved_provider == self.provider
        self.state["base_url"] = (self.base_url or (self.state.get("base_url") if same else None)
                                  or spec["base_url"])
        self.state["model"] = (self.model or (self.state.get("model") if same else None)
                               or spec["model"])
        if self.state["model"] == spec["model"]:
            self.state["model_name"] = spec["model_name"]
            self.state["context_window"] = spec["context_window"]
            self.state["max_tokens"] = spec["max_tokens"]
        else:
            self.state["model_name"] = self.state.get("model_name") or self.state["model"]
            self.state["context_window"] = self.state.get("context_window") or 131072
            self.state["max_tokens"] = self.state.get("max_tokens") or 65536
        self.state["provider"] = self.provider
        self.state["api_key_env"] = spec["env"]
        self.result.add("provider", True,
                        f"{self.provider}: model={self.state['model']} key env={spec['env']}")

    def resolve_workspace(self) -> None:
        override = self.workspace_override
        candidate = override
        if candidate is None:
            existing = self.state.get("workspace")
            if existing and Path(existing).is_dir():
                candidate = Path(existing)
        if candidate is None:
            local = PACKAGE_DIR.parents[1]
            if (local / ".git").is_dir() and (local / "nova3_team").is_dir():
                candidate = local
        if candidate is None and not self.github:
            self.state["workspace_kind"] = "absent"
            self.result.add("workspace", True,
                            "no source checkout yet; it will be cloned into "
                            f"{self.install_root / 'source'} once a GitHub login exists")
            return
        target = Path(candidate).expanduser().resolve()
        marker = target / REPO_MARKER_FILE
        if marker.is_file():
            data = plat.read_json(marker)
            if data.get("repo") != self.github:
                raise InstallError(f"{target} belongs to {data.get('repo')}, not {self.github}")
        if (target / ".git").is_dir():
            self.state["workspace"] = str(target)
            self.state["workspace_kind"] = "checkout"
            self.result.add("workspace", True, f"using existing checkout {target}")
            return
        if marker.is_file():
            self.state["workspace"] = str(target)
            self.state["workspace_kind"] = "archive"
            self.result.add("workspace", True, f"using extracted distribution {target}")
            return
        # Clone fresh; refuse to overwrite anything that is not ours.
        if target.exists() and any(target.iterdir()):
            raise InstallError(f"{target} exists and is not a NOVA3 checkout; pass --workspace")
        if not self.allow_network:
            raise InstallError("no source checkout and network access is disabled")
        target.parent.mkdir(parents=True, exist_ok=True)
        gh = plat.find_executable("gh")
        if gh:
            cp = plat.run([gh, "repo", "clone", self.github, str(target), "--", "--quiet"], timeout=1800)
            how = f"gh repo clone {self.github}"
        else:
            cp = plat.run(["git", "clone", "--depth", "1", f"https://github.com/{self.github}.git", str(target)], timeout=1800)
            how = f"git clone https://github.com/{self.github}.git"
        if cp.returncode != 0:
            raise InstallError(f"{how} failed:\n" + credentials.mask_embedded_secrets((cp.stderr or "")[-1500:])
                               + "\nIf the repository is private, install the GitHub CLI with "
                                 "'gh auth login' first, or download the source archive from the "
                                 "project releases and run with --workspace <extracted folder>.")
        (target / REPO_MARKER_FILE).write_text(
            json.dumps({"repo": self.github, "kind": "checkout", "installer": "tools/dsh_loop"},
                       indent=2) + "\n", encoding="utf-8")
        self.state["workspace"] = str(target)
        self.state["workspace_kind"] = "checkout"
        self.result.add("workspace", True, f"{how} -> {target}", True)

    def check_github(self) -> None:
        gh = plat.find_executable("gh")
        if not gh:
            install = plat.gh_package_ids(self.facts)
            if not install:
                self.result.add("github-cli", True,
                                "GitHub CLI not installed; not required for a public repo")
                return
            if not self._confirm(
                    "GitHub CLI is not installed. Install it so issues, pull requests and "
                    f"releases work from the worker? ({' '.join(install)})"):
                self.result.add("github-cli", True, "skipped by user")
                return
            cp = plat.run(install, timeout=900)
            gh = plat.find_executable("gh")
            if not gh or cp.returncode != 0:
                self.result.add("github-cli", True,
                                "GitHub CLI install did not complete; installed later or manually")
                return
        cp = plat.run([gh, "auth", "status"], timeout=60)
        if cp.returncode != 0:
            self.result.add("github-cli", True,
                            "GitHub CLI present but not signed in; run 'gh auth login' with your "
                            "own account when you want to publish work")
            return
        who = plat.run([gh, "api", "user", "--jq", ".login"], timeout=60)
        login = (who.stdout or "").strip() if who.returncode == 0 else None
        self.state["github_login"] = login
        self.result.add("github-cli", True, f"signed in as {login or 'unknown'} (this machine only)")

    def write_profile(self) -> None:
        """Generate (or refresh) the DSH worker profile and its patch layer."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        package = TEMPLATE_DIR / "package.json.tmpl"
        patch_tmpl = TEMPLATE_DIR / "cordis.patch.yml.tmpl"
        if not package.is_file() or not patch_tmpl.is_file():
            raise InstallError(f"installer templates are missing under {TEMPLATE_DIR}")
        package_text = package.read_text(encoding="utf-8").replace("__DSH_VERSION__", self.dsh_version)
        package_path = self.profile_dir / "package.json"
        changed = self._write_if_changed(package_path, package_text)
        cordis = self.profile_dir / "cordis.yml"
        changed |= self._write_if_changed(cordis, "[]\n")
        patch_text = patch_tmpl.read_text(encoding="utf-8")
        replacements = {
            "BASE_URL": str(self.state["base_url"]),
            "API_KEY_ENV": str(self.state["api_key_env"]),
            "MODEL_ID": str(self.state["model"]),
            "MODEL_NAME": str(self.state.get("model_name") or self.state["model"]),
            "CONTEXT_WINDOW": str(self.state.get("context_window") or 131072),
            "MAX_TOKENS": str(self.state.get("max_tokens") or 65536),
        }
        for key, value in replacements.items():
            patch_text = patch_text.replace(f"__{key}__", value)
        if "__" in re.sub(r"!!js[^\n]*", "", patch_text):
            leftovers = sorted(set(re.findall(r"__[A-Z_]+__", patch_text)))
            if leftovers:
                raise InstallError(f"unfilled template fields: {leftovers}")
        patch_path = self.profile_dir / "cordis.patch.yml"
        changed |= self._write_if_changed(patch_path, patch_text)
        self.state["dsh_home"] = str(self.dsh_home)
        self.state["dsh_profile"] = PROFILE_NAME
        self.state["dsh_version"] = self.dsh_version
        self.result.add("dsh-profile", True,
                        f"profile '{PROFILE_NAME}' at {self.profile_dir}" + (" (updated)" if changed else ""),
                        changed)

    @staticmethod
    def _write_if_changed(path: Path, text: str) -> bool:
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return True

    def verify_profile(self) -> None:
        """Compose the profile headlessly; this catches YAML/route mistakes."""
        env = plat.subprocess_env_with(self.node_paths())
        env["DSH_HOME"] = str(self.dsh_home)
        cp = plat.run([str(self._node_exe()), str(self.dsh_entry()), "--profile", PROFILE_NAME,
                       "--dump-config"], env=env, timeout=300)
        if cp.returncode != 0:
            raise InstallError("the worker profile does not compose:\n"
                               + credentials.mask_embedded_secrets((cp.stderr or cp.stdout or "")[-2000:]))
        text = cp.stdout or ""
        missing = [needle for needle in (str(self.state["model"]), self.state["api_key_env"],
                                         "danger-full-access") if needle not in text]
        if missing:
            raise InstallError(f"composed profile is missing expected values: {missing}")
        self.result.add("dsh-profile-check", True,
                        "profile composes with the configured model route, credential reference "
                        "and autonomous permission policy")

    def check_key(self) -> None:
        """Report key presence only; never print the key."""
        if self.api_key:
            from . import wizard
            ok, message = wizard.verify_key(str(self.state["base_url"]), str(self.state["model"]),
                                            self.api_key, timeout=30)
            self.result.add("api-key-check", ok, message)
            self.api_key = None
            spec = credentials.PROVIDERS[self.provider]
            if not credentials.key_status(self.provider).present:
                profile = credentials.shell_profile_for_shell(os.environ.get("SHELL"))
                self.result.add(
                    "api-key", True,
                    f"verified in memory only. Keep {spec['env']} for the worker by adding "
                    f"'{credentials.shell_export_line(os.environ.get('SHELL'), spec['env'])}' "
                    f"to {profile} (then open a new terminal). It is never written by this program.")
                return
        status = credentials.key_status(self.provider)
        if status.present:
            note = "" if status.valid_shape in (True, None) else " (shape looks unusual; the provider will decide)"
            self.result.add("api-key", True,
                            f"{status.env_name} is set for this terminal: {status.fingerprint}{note}")
            return
        spec = credentials.PROVIDERS[self.provider]
        profile = credentials.shell_profile_for_shell(os.environ.get("SHELL"))
        touched = credentials.install_env_line(profile, spec["env"], state_path=self.state_path,
                                               echo=self.echo)
        self.result.add("api-key", True,
                        f"{spec['env']} is not set yet. Create a key at {spec['key_url']}, then run "
                        f"'{plat.user_scripts_dir() / 'nova3-dsh'} setup' (or export {spec['env']} "
                        "yourself), and start a new terminal."
                        + (f" A placeholder line was appended to {profile}." if touched else ""))
        self.state["awaiting_key"] = True

    def write_launchers(self) -> None:
        """Create the console entry points that the user actually double-clicks."""
        from . import launch  # local import: launch imports this module's constants

        scripts = plat.user_scripts_dir()
        scripts.mkdir(parents=True, exist_ok=True)
        workspace = Path(self.state["workspace"]) if self.state.get("workspace") else None
        paths: list[str] = []
        if plat.WINDOWS:
            cmd = scripts / "nova3-dsh.cmd"
            body = launch.windows_launcher_text(self.state_path)
            changed = self._write_if_changed(cmd, body)
            paths.append(str(cmd))
            ps1 = scripts / "nova3-dsh.ps1"
            changed |= self._write_if_changed(ps1, launch.powershell_launcher_text(self.state_path))
            paths.append(str(ps1))
            changed |= self._write_windows_shortcuts(cmd)
        else:
            sh = scripts / "nova3-dsh"
            changed = self._write_if_changed(sh, launch.unix_launcher_text(self.state_path))
            plat.make_executable(sh)
            paths.append(str(sh))
        self.state["launcher_paths"] = paths
        self.state["launcher_mode"] = "source"
        self.result.add("launchers", True, ", ".join(paths), changed)
        self._path_hint(scripts)

    def _path_hint(self, scripts: Path) -> None:
        if plat.WINDOWS:
            env = plat.subprocess_env_with([])
            if str(scripts).lower() not in env.get("PATH", "").lower():
                self.echo(f"  note: add {scripts} to PATH to run 'nova3-dsh' from any prompt")
            return
        path = os.environ.get("PATH", "").split(os.pathsep)
        if str(scripts) not in path:
            self.echo(f"  note: {scripts} is not on PATH in this terminal; "
                      "start a new terminal session after setup")

    def _write_windows_shortcuts(self, cmd_launcher: Path) -> bool:
        """Start-menu shortcut via PowerShell's WScript.Shell, if available."""
        if not plat.find_executable("powershell") and not plat.find_executable("pwsh"):
            return False
        ps = """$ErrorActionPreference='Stop'
$shell = New-Object -ComObject WScript.Shell
$dir = Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\NOVA3'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$lnk = $shell.CreateShortcut((Join-Path $dir 'NOVA3 DeepSeek Worker.lnk'))
$lnk.TargetPath = $args[0]
$lnk.WorkingDirectory = Split-Path $args[0]
$lnk.Description = 'NOVA3 reconstruction worker (DeepSeek Harness loop)'
$lnk.Save()
"""
        cp = plat.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps, str(cmd_launcher)], timeout=120)
        return cp.returncode == 0

    def record_provenance(self) -> None:
        """Record how this installation was produced (no host paths, no identity)."""
        state = self.state
        state["python"] = f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"
        state["platform"] = f"{self.facts.os_name}-{self.facts.arch}"
        if state.get("workspace"):
            state["workspace_kind"] = state.get("workspace_kind") or "checkout"
        self.result.add("provenance", True, "installation details recorded in the config file")

    def run_all(self) -> InstallResult:
        self.echo(f"NOVA3 DeepSeek Harness installer -- {resource.describe()}")
        self.check_root()
        self.check_python()
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.check_resources()
        self.check_git()
        self.check_node()
        npm = self.check_npm()
        self.check_runtime(npm)
        self.resolve_provider()
        self.check_github()
        self.resolve_workspace()
        self.write_profile()
        self.verify_profile()
        self.check_key()
        self.write_launchers()
        self.record_provenance()
        self.state["installer_version"] = 1
        save_state(self.state_path, self.state)
        self.result.ready = not self.result.failures()
        return self.result

