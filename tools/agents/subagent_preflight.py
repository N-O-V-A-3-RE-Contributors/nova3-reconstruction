"""Offline checks for the opt-in, manual #143 research pilot (Python 3.11+).

Default: inspect allowlisted profiles/docs and executable visibility only. Never
start agents, inspect authentication, repair a runtime, or call a provider API.
The optional Codex probe runs only help/version commands in a disposable home.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    "nova3_tu_mapper": ".codex/agents/nova3_tu_mapper.toml",
    "nova3_match_reviewer": ".codex/agents/nova3_match_reviewer.toml",
    "nova3_test_reviewer": ".codex/agents/nova3_test_reviewer.toml",
}
ALLOWED_PATHS = (*PROFILES.values(), "tools/agents/subagent_preflight.py",
                 "nova3_team/tests/test_subagent_profiles.py",
                 "docs/progress/SUBAGENT_PILOT.md")
DISABLED_FEATURES = {"apps", "plugins", "memories", "request_permissions_tool"}
MAX_FILE_BYTES = 32_768
PROBE_TIMEOUT_SECONDS = 10
MAX_PROBE_BYTES = 65_536
PINNED_HERMES_MODEL = "deepseek/deepseek-v4.1-flash-1017"

# Deliberate, inspectable instruction contract; this lint is not a sandbox or
# proof that a model will obey instructions. Permissions always inherit.
REQUIRED_INSTRUCTIONS = (
    "Default off", "explicit parent assignment", "read-only child",
    "parent is the sole writer", "Do not edit repository files",
    "Do not create, claim, queue, assign, reprioritize, or merge",
    "Do not spawn child agents", "10 minutes", "20 commands", "30 files",
    "allowed paths", "No paid inference", "No original game bytes",
    "NOT MATCHED", "synthetic", "evidence", "handoff",
)


def read_source(root: Path, relative: str) -> str:
    """Read only a regular bounded allowlisted source file, without symlinks."""
    if relative not in ALLOWED_PATHS:
        raise ValueError("path outside pilot allowlist")
    current = root
    for component in Path(relative).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("symlink in pilot source path")
    if not current.is_file() or current.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("missing, non-regular or oversized pilot source")
    with current.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("oversized pilot source")
    return data.decode("utf-8")


def validate_profile(name: str, source: str) -> list[str]:
    """Validate the intentionally narrow official custom-agent TOML subset."""
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError:
        return ["invalid TOML"]
    errors = []
    if set(data) != {"name", "description", "developer_instructions", "features"}:
        errors.append("unexpected or missing profile fields")
    if name not in PROFILES or data.get("name") != name:
        errors.append("agent name does not match allowlisted profile")
    if not isinstance(data.get("description"), str) or not data["description"].strip():
        errors.append("missing description")
    instructions = data.get("developer_instructions")
    if not isinstance(instructions, str):
        errors.append("missing developer instructions")
    elif any(term not in instructions for term in REQUIRED_INSTRUCTIONS):
        errors.append("missing manual/read-only/budget/evidence instruction")
    features = data.get("features")
    if not isinstance(features, dict) or set(features) != DISABLED_FEATURES:
        errors.append("unexpected or missing feature controls")
    elif any(value is not False for value in features.values()):
        errors.append("optional capability must stay disabled")
    return errors


def validate_document(source: str) -> list[str]:
    """Check the documented native Hermes recipe and manual-pilot boundaries."""
    required = (
        "https://learn.chatgpt.com/docs/agent-configuration/subagents",
        "https://learn.chatgpt.com/docs/cli/reference",
        "https://github.com/NousResearch/hermes-agent/",
        "delegate_task", "max_concurrent_children: 2", "max_spawn_depth: 1",
        "max_iterations: 20", "oneshot_max_children: 2", PINNED_HERMES_MODEL,
        "UNAVAILABLE", "UNKNOWN", "--ephemeral", "--probe-codex",
        "agents.enabled=true", "agents.max_concurrent_threads_per_session=2",
        "parent is the sole writer", "not a separate read-only sandbox",
        "#136", "#144", "spend cap", "tool calling", "handoff",
    )
    return [] if all(term in source for term in required) else [
        "missing native Hermes/Codex capability or manual safety documentation"]


def _safe_environment(home: Path) -> dict[str, str]:
    # Do not forward keys, tokens, provider URLs, user hooks or daemon settings.
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR")
           if k in os.environ}
    env.update(HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(home),
               XDG_CONFIG_HOME=str(home / "config"),
               XDG_CACHE_HOME=str(home / "cache"),
               XDG_DATA_HOME=str(home / "data"),
               TMPDIR=str(home), TMP=str(home), TEMP=str(home),
               LANG="C", LC_ALL="C")
    return env


def _probe_output(executable: str, args: list[str], home: Path) -> str:
    """Bounded diagnostics, never return a child's raw stderr to the report."""
    with tempfile.TemporaryFile() as output:
        try:
            done = subprocess.run([executable, *args], cwd=home,
                                  env=_safe_environment(home),
                                  stdin=subprocess.DEVNULL, stdout=output,
                                  stderr=subprocess.DEVNULL,
                                  timeout=PROBE_TIMEOUT_SECONDS, check=False,
                                  shell=False)
        except (OSError, subprocess.SubprocessError):
            raise ValueError("Codex capability probe failed or timed out") from None
        if done.returncode != 0:
            raise ValueError("Codex capability probe returned failure")
        output.seek(0)
        raw = output.read(MAX_PROBE_BYTES + 1)
    if len(raw) > MAX_PROBE_BYTES:
        raise ValueError("Codex capability output exceeded limit")
    return raw.decode("utf-8", errors="replace")


def probe_codex(executable: str) -> dict:
    """No prompt/session or credentials: CLI flags do not prove profile loading."""
    with tempfile.TemporaryDirectory(prefix="nova3-subagent-probe-") as temp:
        home = Path(temp)
        version = _probe_output(executable, ["--version"], home)
        help_text = _probe_output(executable, ["exec", "--help"], home)
    match = re.fullmatch(r"codex-cli ([0-9]+\.[0-9]+\.[0-9]+)\s*", version)
    if not match or any(flag not in help_text for flag in
                        ("--ephemeral", "--config", "--sandbox")):
        raise ValueError("unsupported Codex version/help contract")
    return {"status": "CLI_FLAGS_VERIFIED", "version": match.group(1),
            "profile_loading": "UNKNOWN", "inference_run": False}


def inspect(root: Path, *, probe: bool = False) -> dict:
    errors = {}
    for name, relative in PROFILES.items():
        try:
            found = validate_profile(name, read_source(root, relative))
        except (ValueError, OSError, UnicodeError):
            found = ["cannot safely read profile"]
        if found:
            errors[relative] = found
    try:
        doc_errors = validate_document(read_source(root, "docs/progress/SUBAGENT_PILOT.md"))
    except (ValueError, OSError, UnicodeError):
        doc_errors = ["cannot safely read pilot documentation"]
    if doc_errors:
        errors["docs/progress/SUBAGENT_PILOT.md"] = doc_errors
    codex = shutil.which("codex")
    hermes = shutil.which("hermes")
    cli = {"status": "NOT_PROBED" if codex else "UNAVAILABLE",
           "profile_loading": "UNKNOWN", "inference_run": False}
    if probe and codex:
        try:
            cli = probe_codex(codex)
        except ValueError as error:
            cli["status"] = "PROBE_FAILED"
            cli["reason"] = str(error)
    return {
        "schema": "nova3.manual_subagents/1", "enabled": False,
        "validation": "PASS" if not errors else "FAIL", "errors": errors,
        "max_children": 2, "max_spawn_depth": 1,
        "codex": cli,
        "hermes": {"status": "PRESENT_UNVERIFIED" if hermes else "UNAVAILABLE",
                   "cli_probe": "NOT_RUN", "config_schema": "UNKNOWN",
                   "endpoint_tool_calling": "UNKNOWN",
                   "model": PINNED_HERMES_MODEL},
        "automatic_dispatch": False, "paid_calls": 0,
        "note": "Instruction lint and CLI visibility are not runtime permission or model verification",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="checkout containing the six pilot source paths")
    parser.add_argument("--probe-codex", action="store_true",
                        help="opt in to isolated Codex help/version only; no model session")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error("repository directory not found")
    result = inspect(root, probe=args.probe_codex)
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(result["validation"] != "PASS" or
               (args.probe_codex and result["codex"]["status"] != "CLI_FLAGS_VERIFIED"))


if __name__ == "__main__":
    raise SystemExit(main())
