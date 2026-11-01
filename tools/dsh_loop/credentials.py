"""Per-user credential handling for the NOVA3 DSH loop.

Rules this module enforces:

* No secret is ever written by this program to disk, to a log line, to a
  session transcript, or to a command line (which the OS makes visible to other
  users). The key is typed/pasted once into a terminal and kept as a shell
  environment variable the user owns (``export`` line in their shell profile).
* Only the *name* of the environment variable is stored in the installer's
  JSON state; the value is read from the launching environment at run time.
* A key is echoed back only as a masked fingerprint (last four characters and
  a short SHA-256 prefix) so a user can confirm which key is active without
  the key being displayed again.

The parent shell keeps a plain environment variable; this is the
conventional, documented behaviour of coding CLIs (and what the DeepSeek
Harness expects). Contributors who want OS-keyring storage can point the
profile at a credential reference their own secret manager exports.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SKIP_VALUES = {"", "skip", "-", "none"}

PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter (recommended: any DeepSeek model id, gateway routing)",
        "env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-v4.1-flash-1017",
        "model_name": "DeepSeek V4.1 Flash (1017)",
        "key_url": "https://openrouter.ai/keys",
        "context_window": 1048576,
        "max_tokens": 393216,
    },
    "deepseek": {
        "label": "DeepSeek direct API (api.deepseek.com)",
        "env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "model_name": "DeepSeek direct (deepseek-chat)",
        "key_url": "https://platform.deepseek.com/api_keys",
        "context_window": 131072,
        "max_tokens": 65536,
    },
}

# Conservative token shapes; the authoritative validation is the provider's
# own HTTP response, which the launcher surfaces verbatim on failure.
_KEY_PATTERNS = {
    "openrouter": re.compile(r"^sk-or-v1-[A-Za-z0-9]{16,}$"),
    "deepseek": re.compile(r"^sk-[A-Za-z0-9]{16,}$"),
}


class CredentialError(RuntimeError):
    """The user must (re)configure a key before the loop can talk to a model."""


@dataclass(frozen=True)
class KeyStatus:
    provider: str
    env_name: str
    present: bool
    fingerprint: str  # masked; empty when absent
    valid_shape: bool | None


def fingerprint(secret: str) -> str:
    """Masked identity of a key: enough to tell two keys apart, nothing more."""
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]
    tail = secret[-4:] if len(secret) >= 4 else "****"
    return f"…{tail} (sha256:{digest})"


def key_status(provider: str, env: dict | None = None) -> KeyStatus:
    spec = PROVIDERS[provider]
    env = os.environ if env is None else env
    secret = (env.get(spec["env"]) or "").strip()
    if not secret:
        return KeyStatus(provider, spec["env"], False, "", None)
    pattern = _KEY_PATTERNS.get(provider)
    shape = bool(pattern.match(secret)) if pattern else None
    return KeyStatus(provider, spec["env"], True, fingerprint(secret), shape)


def read_secret(prompt: str, *, echo: Callable[[str], None] = print) -> str:
    """Read a secret without echoing it. Falls back to visible input when the
    terminal cannot hide input, and says so rather than silently echoing."""
    echo(prompt)
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            value = getpass.getpass("  key (input hidden): ")
        except (EOFError, KeyboardInterrupt):
            raise CredentialError("no key entered; setup was cancelled.") from None
        return value.strip()
    echo("  (stdin is not a terminal, so this input cannot be hidden; "
         "nothing is logged by this program)")
    try:
        return input("  key: ").strip()
    except EOFError:
        raise CredentialError("no key entered; setup was cancelled.") from None


def shell_profile_for_shell(shell: str | None) -> Path:
    """The file where an ``export`` line belongs for the user's login shell."""
    name = (shell or "").rsplit("/", 1)[-1]
    home = Path.home()
    if os.name == "nt":
        return home / "Documents" / "WindowsPowerShell" / "profile.ps1"
    if name in ("zsh",):
        return home / ".zshrc"
    if name in ("fish",):
        return home / ".config" / "fish" / "config.fish"
    if name in ("bash",):
        return home / ".bashrc"
    return home / ".profile"


def shell_export_line(shell: str | None, env_name: str) -> str:
    name = (shell or "").rsplit("/", 1)[-1]
    if os.name == "nt":
        return f'$env:{env_name} = "<paste your key here>"'
    if name == "fish":
        return f'set -gx {env_name} "<paste your key here>"'
    return f'export {env_name}="<paste your key here>"'


def install_env_line(profile: Path, env_name: str, *, state_path: Path,
                     echo: Callable[[str], None] = print) -> bool:
    """Prepare the shell profile without ever writing the real secret.

    An empty ``export NAME=""`` placeholder is appended (once) if the name is
    not already mentioned, so the user pastes the key themselves and the setup
    program never learns where it is stored on disk. Returns True when the file
    was touched.
    """
    line_name = env_name
    try:
        existing = profile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        existing = ""
    if line_name in existing:
        echo(f"  {profile} already mentions {env_name}; leaving it alone.")
        return False
    shell = os.environ.get("SHELL")
    placeholder = shell_export_line(shell, env_name)
    comment = (f"# NOVA3 DSH loop: paste your {env_name} value below "
               f"(quoted), then start a new terminal.\n")
    try:
        profile.parent.mkdir(parents=True, exist_ok=True)
        with profile.open("a", encoding="utf-8") as handle:
            handle.write("\n" + comment + placeholder + "\n")
    except OSError as exc:
        echo(f"  could not write {profile}: {exc}")
        return False
    echo(f"  appended a placeholder line to {profile}; nothing secret was written.")
    return True


def mask_embedded_secrets(text: str) -> str:
    """Strip anything that looks like an API key from text that may be logged."""
    text = re.sub(r"sk-or-v1-[A-Za-z0-9]{8,}", "sk-or-v1-<redacted>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9]{16,}", "sk-<redacted>", text)
    text = re.sub(r"(Authorization:\s*Bearer\s+)\S+", r"\1<redacted>", text, flags=re.I)
    return text
