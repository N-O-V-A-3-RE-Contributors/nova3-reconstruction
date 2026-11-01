"""Run one DeepSeek Harness (DSH) headless pass and interpret its result.

One pass is exactly one ``dsh --profile <p> headless --json <prompt>`` process
from the worker profile directory. The JSON event stream gives the loop an
objective signal of what happened (usage, turn outcome, final text) without
parsing a transcript, and the process exit code gives the coarse verdict.

Nothing here interprets *content*; deciding whether a pass made progress
belongs to ``loop.py``, which compares durable workspace state.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field

from . import credentials
from . import platforms as plat


@dataclass
class PassResult:
    returncode: int
    started: float
    finished: float
    session_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    final_text: str = ""
    events: list[dict] = field(default_factory=list)
    stderr_tail: str = ""
    timed_out: bool = False

    @property
    def elapsed(self) -> float:
        return self.finished - self.started

    @property
    def ok(self) -> bool:
        return (not self.timed_out) and self.returncode == 0 and self.error_code is None

    @property
    def fatal_credential(self) -> bool:
        return self.error_code in ("MISSING_CREDENTIAL", "AUTH", "INVALID_CREDENTIAL")

    def summary(self) -> str:
        if self.timed_out:
            return f"timeout after {self.elapsed:.0f}s"
        if self.error_code:
            return f"error {self.error_code}: {(self.error_message or '')[:300]}"
        return f"exit {self.returncode} in {self.elapsed:.0f}s, tokens in/out {self.input_tokens}/{self.output_tokens}"


def parse_events(lines: list[str]) -> tuple[list[dict], PassResult]:
    events: list[dict] = []
    result = PassResult(returncode=0, started=0.0, finished=0.0)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        kind = event.get("type")
        if kind == "session" and event.get("sessionId"):
            result.session_id = str(event["sessionId"])
        elif kind == "status" and event.get("phase") == "turn_end":
            reason = event.get("reason") or {}
            if isinstance(reason, dict) and reason.get("kind") == "error":
                err = reason.get("error") or {}
                result.error_code = err.get("code") or "ERROR"
                result.error_message = err.get("message")
            usage = event.get("usage") or {}
            result.input_tokens += int(usage.get("inputTokens") or 0)
            result.output_tokens += int(usage.get("outputTokens") or 0)
        elif kind == "error":
            result.error_code = "ERROR"
            result.error_message = str(event.get("message") or "")
        elif kind == "final":
            result.final_text = str(event.get("text") or "")
    result.events = events
    return events, result


class DshNotFound(RuntimeError):
    pass


def build_command(node: str, entry: str, profile: str) -> list[str]:
    """argv for one headless pass; the prompt travels on stdin, not argv."""
    return [node, entry, "--profile", profile, "headless", "--json"]


class DshRunner:
    def __init__(self, *, node: Path, entry: Path, profile: str, dsh_home: Path,
                 cwd: Path, env: dict | None = None, log_path: Path | None = None) -> None:
        self.node = node
        self.entry = entry
        self.profile = profile
        self.dsh_home = dsh_home
        self.cwd = cwd
        self.env = dict(env or os.environ)
        self.env["DSH_HOME"] = str(dsh_home)
        self.env.setdefault("NOVA3_DSH_LOOP", "1")
        self.log_path = log_path

    def run(self, prompt: str, *, timeout: float) -> PassResult:
        # No positional task: the headless runner reads the whole task from
        # stdin, which keeps long prompts out of argv.
        argv = build_command(str(self.node), str(self.entry), self.profile)
        started = time.time()
        timed_out = False
        try:
            proc = subprocess.run(
                argv, cwd=str(self.cwd), env=self.env, input=prompt,
                capture_output=True, text=True, timeout=timeout, check=False)
            rc = proc.returncode
            out, err = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            rc = -9
            out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
            err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        finished = time.time()
        _, result = parse_events((out or "").splitlines())
        result.returncode = rc
        result.started = started
        result.finished = finished
        result.timed_out = timed_out
        result.stderr_tail = credentials.mask_embedded_secrets((err or "")[-4000:])
        if self.log_path is not None:
            self._append_log(prompt, result, out or "")
        return result

    def _append_log(self, prompt: str, result: PassResult, stdout: str) -> None:
        """Append a bounded pass record. The prompt is included: it is the
        loop's own instruction, never user secrets (keys are env-only)."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(result.started))
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n===== pass {stamp} session={result.session_id} =====\n")
            handle.write(f"result: {result.summary()}\n")
            if result.stderr_tail.strip():
                handle.write("stderr tail:\n" + result.stderr_tail + "\n")
            if result.final_text:
                handle.write("final text:\n" + result.final_text[-6000:] + "\n")
            handle.write(f"prompt bytes: {len(prompt)}\n")
