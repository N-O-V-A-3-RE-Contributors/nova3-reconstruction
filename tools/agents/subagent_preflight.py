"""Offline checks for the opt-in, manual #143 research pilot (Python 3.11+).

Default: inspect allowlisted profiles/docs and executable visibility only.
Never inspect authentication, repair a runtime, or call a provider API. Opt-in
probes run help/version or native children with loopback-only simulated responses.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
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


def _fixture_events(item: dict) -> bytes:
    """Minimal deterministic Responses SSE, generated locally without a model."""
    events = [
        {"type": "response.created", "response": {"id": "resp_fixture", "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": {"id": "resp_fixture", "status": "completed", "output": [item]}},
    ]
    return "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                   for event in events).encode()


def probe_profile_loading(executable: str, root: Path) -> dict:
    """Apply each role in native Codex using ONLY a loopback response fixture.

    One child at a time, fresh credential-free home, no model/provider API.
    The fixture invokes only native spawn/close; it never requests shell tools.
    Exact developer-instruction injection and close acknowledgments are checked.
    """
    verified = []
    for name, relative in PROFILES.items():
        source = read_source(root, relative)
        if validate_profile(name, source):
            raise ValueError("invalid profile for native loader fixture")
        expected = tomllib.loads(source)["developer_instructions"]
        seen_child = threading.Event()
        state = {"requests": 0, "catalog": False, "closed": False}

        class FixtureHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                state["requests"] += 1
                if not 0 < length <= 1_048_576 or state["requests"] > 12:
                    self.send_error(413)
                    return
                request = json.loads(self.rfile.read(length))
                inputs = request.get("input", [])
                texts = [part.get("text", "") for item in inputs
                         for part in item.get("content", []) if isinstance(part, dict)]
                item = {"id": "msg_fixture", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text",
                        "text": "Offline fixture complete.", "annotations": []}]}

                def call(tool, arguments, call_id):
                    return {"id": "fc_" + call_id, "type": "function_call",
                            "call_id": call_id, "namespace": "multi_agent_v1",
                            "name": tool, "arguments": json.dumps(arguments)}

                if any(expected in text for text in texts):
                    seen_child.set()
                elif state["requests"] == 1:
                    tools = [tool for group in request.get("tools", [])
                             if group.get("name") == "multi_agent_v1"
                             for tool in group.get("tools", [])]
                    catalog = json.dumps(tools)
                    state["catalog"] = any(t.get("name") == "spawn_agent" for t in tools) and name in catalog
                    if state["catalog"]:
                        item = call("spawn_agent", {"agent_type": name, "fork_context": False,
                                    "message": "Explicit parent assignment: read-only offline loader fixture. "
                                    "Return the literal OFFLINE; do not call tools or spawn agents."}, "spawn_fixture")
                else:
                    outputs = {entry.get("call_id"): entry.get("output") for entry in inputs
                               if entry.get("type") == "function_call_output"}
                    if "close_fixture" in outputs:
                        # Native close acknowledges shutdown, not just a fake model assertion.
                        closed = json.loads(outputs["close_fixture"])
                        state["closed"] = isinstance(closed, dict) and "previous_status" in closed
                    elif "spawn_fixture" in outputs:
                        spawned = json.loads(outputs["spawn_fixture"])
                        target = spawned.get("agent_id")
                        if isinstance(target, str):
                            seen_child.wait(timeout=5)
                            item = call("close_agent", {"target": target}, "close_fixture")
                data = _fixture_events(item)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        except OSError:
            raise ValueError("loopback fixture unavailable under current permissions") from None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="nova3-native-role-") as temp:
                home = Path(temp)
                project = home / "project"
                profile = project / relative
                profile.parent.mkdir(parents=True)
                profile.write_text(source, encoding="utf-8")
                config = (
                    'model="nova3-offline-fixture"\nmodel_provider="nova3_fixture"\n'
                    '[model_providers.nova3_fixture]\nname="NOVA3 loopback fixture"\n'
                    f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
                    'wire_api="responses"\nrequires_openai_auth=false\n'
                    '[agents]\nenabled=true\nmax_concurrent_threads_per_session=1\n'
                    '[analytics]\nenabled=false\n[feedback]\nenabled=false\n'
                    '[features]\napps=false\nplugins=false\nmemories=false\n'
                    f'[projects.{json.dumps(str(project))}]\ntrust_level="trusted"\n')
                (home / "config.toml").write_text(config, encoding="utf-8")
                _probe_output(executable, ["exec", "--ephemeral", "--skip-git-repo-check",
                              "--sandbox", "read-only", "-C", str(project),
                              "Explicitly run the local offline profile-loading fixture with one child; "
                              "close that child before finishing. No shell tools or other work."], home)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        if not (state["catalog"] and seen_child.is_set() and state["closed"]):
            raise ValueError("native role discovery/application/closure not verified")
        verified.append(name)
    return {"profile_loading": "VERIFIED_LOCAL_FIXTURE", "verified_profiles": verified,
            "children_closed": True, "model_responses": "SIMULATED_LOOPBACK_ONLY"}


def inspect(root: Path, *, probe: bool = False, loader: bool = False) -> dict:
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
    if (probe or loader) and codex:
        try:
            cli = probe_codex(codex)
            if loader and not errors:
                cli.update(probe_profile_loading(codex, root))
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
    parser.add_argument("--probe-codex-loader", action="store_true",
                        help="opt in to native profile checks using a loopback-only fake model; no paid calls")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error("repository directory not found")
    result = inspect(root, probe=args.probe_codex, loader=args.probe_codex_loader)
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(result["validation"] != "PASS" or
               ((args.probe_codex or args.probe_codex_loader) and result["codex"]["status"] != "CLI_FLAGS_VERIFIED") or
               (args.probe_codex_loader and result["codex"]["profile_loading"] != "VERIFIED_LOCAL_FIXTURE"))


if __name__ == "__main__":
    raise SystemExit(main())
