"""Interactive first-run setup (what a contributor actually sees).

The rules that shape this file:

* The API key is typed into a hidden prompt, used for **one** verification
  request, and kept in memory only. It is offered for storage as a shell
  variable (via the user's own shell profile) or via ``mix of explicit env
  export``; this program never writes it to any file and never logs it.
* Every question has a default; ``--yes`` skips the questions and uses them.
* A failure is explained with the exact next action, never with a traceback.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import credentials
from . import platforms as plat

Echo = Callable[[str], None]

BANNER = """
  N.O.V.A. 3 reconstruction -- worker setup
  DeepSeek Harness loop (source-only project; no game data included)
"""


def _ask(question: str, default: str = "", echo: Echo = print) -> str:
    try:
        answer = input(f"{question}" + (f" [{default}]" if default else "") + ": ").strip()
    except EOFError:
        return default
    return answer or default


def _ask_yes(question: str, default: bool = True, echo: Echo = print) -> bool:
    marker = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {marker} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def verify_key(base_url: str, model: str, api_key: str, *, timeout: float = 30) -> tuple[bool, str]:
    """One minimal chat request. Returns (ok, message-without-secret)."""
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 8,
        "stream": False,
    }).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user's own endpoint
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = credentials.mask_embedded_secrets(exc.read().decode("utf-8", "replace"))[:400]
        return False, f"HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, credentials.mask_embedded_secrets(str(exc))[:400]
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        return False, "the endpoint answered but returned no completion: " + str(payload)[:300]
    return True, "the provider accepted the request"


def run_wizard(*, echo: Echo = print, assume_yes: bool = False, provider: str | None = None,
               model: str | None = None, base_url: str | None = None,
               previous: dict | None = None) -> dict:
    """Collect settings. Returns keyword arguments for ``installer.Installer``."""
    if not assume_yes:
        echo(BANNER)
    chosen = provider or ((previous or {}).get("provider") if previous else None)
    if not chosen:
        if assume_yes or not os.isatty(0):
            chosen = "openrouter"
        else:
            echo("Which model provider will run the worker?")
            keys = list(credentials.PROVIDERS)
            for index, key in enumerate(keys, start=1):
                echo(f"  {index}. {credentials.PROVIDERS[key]['label']}")
            answer = _ask("Select", "1", echo)
            chosen = keys[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(keys) else keys[0]
    spec = credentials.PROVIDERS[chosen]
    route_model = model or ((previous or {}).get("model") if previous and
                            (previous or {}).get("provider") == chosen else None)
    if not route_model and not assume_yes and os.isatty(0):
        route_model = _ask(f"Model id ({spec['model']} is recommended; press Enter to accept)",
                           spec["model"], echo).strip() or spec["model"]
    settings = {
        "provider": chosen,
        "model": route_model or spec["model"],
        "base_url": base_url or spec["base_url"],
    }

    existing = credentials.key_status(chosen)
    if existing.present:
        echo(f"{existing.env_name} is already set in this terminal ({existing.fingerprint}); "
             "keeping it.")
        settings["api_key"] = None
        return settings
    if assume_yes or not os.isatty(0):
        settings["api_key"] = None
        return settings

    echo("")
    echo(f"Create an API key at {spec['key_url']} (your own account; it is billed to you).")
    if not _ask_yes(f"Paste a {spec['env_name']} now and verify it?", True, echo):
        settings["api_key"] = None
        return settings
    secret = credentials.read_secret("  The key is hidden while you type and is never written to disk by this program.", echo=echo)
    if not secret:
        settings["api_key"] = None
        return settings
    ok, message = verify_key(settings["base_url"], settings["model"], secret, timeout=30)
    if ok:
        echo(f"  verified: {message} (model {settings['model']})")
        settings["api_key"] = secret
    else:
        echo(f"  verification failed: {message}")
        if _ask_yes("  Use it anyway (the provider may need a different model id)?", False, echo):
            settings["api_key"] = secret
        else:
            settings["api_key"] = None
    return settings


def offer_persistence(env_name: str, *, echo: Echo = print) -> None:
    """Explain how to keep the key, and offer the safe placeholder route."""
    shell = os.environ.get("SHELL")
    profile = credentials.shell_profile_for_shell(shell)
    echo("")
    echo(f"To make {env_name} available in every new terminal:")
    echo(f"  1. open {profile} (or your shell's startup file)")
    echo(f"  2. add:  {credentials.shell_export_line(shell, env_name)}")
    echo("     replacing the placeholder with your key")
    echo("  3. open a new terminal")
    if plat.WINDOWS:
        echo("  Windows equivalent: setx %s \"<your key>\" then open a new window" % env_name)
