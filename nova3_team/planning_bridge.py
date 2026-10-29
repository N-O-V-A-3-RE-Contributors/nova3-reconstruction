"""Provider-neutral planning bridge: AI proposals -> reviewed GitHub drafts.

Design rules (see docs/progress/PLANNING_BRIDGE.md):

* The proposal schema is the only accepted input. Arbitrary model prose is
  never executed, never shell-evaluated and never treated as authorization.
* Validation is pure and deterministic, so any provider (Hermes, OpenCode,
  Codex, Claude, Gemini, a human) produces the same fingerprint for the same
  plan and duplicates collapse to one issue.
* Publishing a draft creates a NON-QUEUED issue. Adding ``nova:queued`` is a
  separate owner/admin-authorized transition delegated to the access-policy
  gate from #134 and the queue gate from #135. This module never approves
  itself, never merges and never starts a worker.
* Sanitised context export contains only GitHub metadata: no proprietary
  binaries, assets, decompiler output, credentials or absolute local paths.
"""
from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import time
from typing import Callable, Iterable, Mapping, Protocol, Sequence

from . import access_policy

SCHEMA_VERSION = 1
MAX_PROPOSAL_BYTES = 256 * 1024
MAX_BODY_CHARS = 60000
MAX_TITLE_CHARS = 240
MAX_PATHS = 40
MAX_PREREQUISITES = 40
MAX_ACCEPTANCE = 40
MAX_EVIDENCE = 40
MAX_TEST_COMMANDS = 20
MAX_ARGV = 40
FINGERPRINT_MARKER = "<!-- NOVA3_PLAN_FINGERPRINT: {} -->"

# Files that are never acceptable as task scope: private inputs, build output,
# credentials and git internals. Matching is by literal prefix, not a guess.
PRIVATE_PREFIXES = (
    "originals/", "analysis/", "analysis_db/", "archive/", "generated/",
    "research/", ".git/", ".venv/", "build/", ".cache/", "originals",
    "analysis", "archive", "generated", "research",
)
SECRET_HINTS = (
    "github_pat_", "-----BEGIN", "OPENROUTER_API_KEY", "GITHUB_TOKEN",
    "GH_TOKEN", "ANTHROPIC_API_KEY", "AKIA",
)
_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{16,}"),
)


def looks_secret(value: str) -> bool:
    if any(hint in value for hint in SECRET_HINTS):
        return True
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)
# Markers reserved for authenticated GitHub records. A proposal that embeds
# one is trying to smuggle an approval/lease into issue text.
RESERVED_MARKERS = (
    "NOVA3_LEASE_START", "nova3.approval", "nova3.assignment",
    "NOVA3_APPROVED", "NOVA3_PLAN_FINGERPRINT",
)
ALLOWED_TEST_PROGRAMS = (
    "python", "python3", "ctest", "cmake", "ninja", "make", "git",
    "bash", "sh", "node", "npm", "gradle", "./gradlew",
)
SHELL_META = re.compile(r"[;&|><`$()\\\n\r]")
CLAIM_STATES = frozenset({"verified", "inferred", "synthetic", "unknown", "blocked"})
RISK_CATEGORIES = frozenset({"low", "medium", "high"})
PRIVACY_CATEGORIES = frozenset({"public-metadata", "private-local", "restricted"})


class ProposalError(ValueError):
    """Deterministic, actionable validation failure. Never echoes secrets."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: object, *, code: str, limit: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ProposalError(code, "must be a string")
    text = value.strip()
    if required and not text:
        raise ProposalError(code, "must not be empty")
    if len(value) > limit:
        raise ProposalError(code, f"exceeds {limit} characters")
    lowered = text
    if looks_secret(lowered):
        raise ProposalError(code, "contains credential-like material")
    for marker in RESERVED_MARKERS:
        if marker in lowered:
            raise ProposalError(code, "contains a reserved authorization marker")
    if "\x00" in value:
        raise ProposalError(code, "contains NUL")
    return text


def _seq(value: object, *, code: str, limit: int, required: bool = True) -> tuple:
    if not isinstance(value, list):
        raise ProposalError(code, "must be a list")
    if len(value) > limit:
        raise ProposalError(code, f"exceeds {limit} entries")
    if required and not value:
        raise ProposalError(code, "must not be empty")
    return tuple(value)


def _repo_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ProposalError("allowed_paths", "must be a non-empty string")
    if "\\" in value:
        raise ProposalError("allowed_paths", "must use forward slashes")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ProposalError("allowed_paths", "must be repository-relative")
    if ".." in value.split("/"):
        raise ProposalError("allowed_paths", "must not traverse upwards")
    normalised = posixpath.normpath(value)
    if normalised in {"", "."}:
        raise ProposalError("allowed_paths", "must name a file or directory")
    lowered = normalised.lower() + ("/" if not normalised.endswith("/") else "")
    for prefix in PRIVATE_PREFIXES:
        if lowered == prefix or lowered.startswith(prefix):
            raise ProposalError("allowed_paths", f"private or generated path rejected: {normalised}")
    return normalised


def _test_command(value: object) -> tuple[str, ...]:
    """Accept argv lists only; reject shell syntax outright."""
    if not isinstance(value, list) or not value:
        raise ProposalError("test_commands", "must be a non-empty argv list")
    if len(value) > MAX_ARGV:
        raise ProposalError("test_commands", "too many argv elements")
    argv: list[str] = []
    for item in value:
        if not isinstance(item, str) or "\x00" in item:
            raise ProposalError("test_commands", "argv elements must be strings")
        if SHELL_META.search(item):
            raise ProposalError("test_commands", "shell metacharacters are not permitted in argv")
        argv.append(item)
    program = argv[0]
    base = posixpath.basename(program)
    if program not in ALLOWED_TEST_PROGRAMS and base not in ALLOWED_TEST_PROGRAMS:
        raise ProposalError("test_commands", f"program not allowed: {base}")
    # Never accept inline code evaluation: `-c`/`-e` turns an argv list into a
    # shell or interpreter script and defeats the argv guarantee.
    if base in {"python", "python3", "sh", "bash", "node"}:
        if any(arg in {"-c", "--command", "-e", "--eval"} for arg in argv[1:]):
            raise ProposalError("test_commands", "inline code evaluation is not permitted")
    return tuple(argv)


@dataclass(frozen=True)
class SourceStandard:
    """Explicit epistemic state; byte-match claims fail closed without proof."""

    compiler_abi: str = "unknown"
    tu_object_first: bool = False
    original_match: str = "not_claimed"
    evidence: tuple[str, ...] = ()

    @staticmethod
    def parse(raw: object) -> "SourceStandard":
        if not isinstance(raw, dict):
            raise ProposalError("source_standard", "must be an object")
        unknown = set(raw) - {"compiler_abi", "tu_object_first", "original_match", "evidence"}
        if unknown:
            raise ProposalError("source_standard", f"unknown keys: {sorted(unknown)}")
        compiler = raw.get("compiler_abi", "unknown")
        if compiler not in {"verified", "inferred", "unknown"}:
            raise ProposalError("source_standard", "compiler_abi must be verified|inferred|unknown")
        tu_first = raw.get("tu_object_first", False)
        if type(tu_first) is not bool:
            raise ProposalError("source_standard", "tu_object_first must be a boolean")
        match = raw.get("original_match", "not_claimed")
        if match not in {"not_claimed", "synthetic_only", "verified_byte_match"}:
            raise ProposalError("source_standard", "invalid original_match")
        evidence = _seq(raw.get("evidence", []), code="source_standard", limit=20, required=False)
        evidence = tuple(_text(e, code="source_standard", limit=500) for e in evidence)
        if match == "verified_byte_match" and not evidence:
            raise ProposalError("source_standard", "byte-match claims require reproducible evidence references")
        return SourceStandard(compiler, tu_first, match, evidence)


@dataclass(frozen=True)
class Estimate:
    cost_micro_usd: int = 0
    runtime_minutes: int = 0
    confidence: str = "unknown"

    @staticmethod
    def parse(raw: object) -> "Estimate":
        if raw is None:
            return Estimate()
        if not isinstance(raw, dict):
            raise ProposalError("estimate", "must be an object")
        cost = raw.get("cost_micro_usd", 0)
        runtime = raw.get("runtime_minutes", 0)
        confidence = raw.get("confidence", "unknown")
        if type(cost) is not int or cost < 0 or cost > 500_000_000:
            raise ProposalError("estimate", "cost_micro_usd must be a bounded non-negative int")
        if type(runtime) is not int or runtime < 0 or runtime > 10_000:
            raise ProposalError("estimate", "runtime_minutes must be a bounded non-negative int")
        if confidence not in {"low", "medium", "high", "unknown"}:
            raise ProposalError("estimate", "confidence must be low|medium|high|unknown")
        return Estimate(cost, runtime, confidence)


@dataclass(frozen=True)
class TaskSpec:
    key: str
    objective: str
    title: str
    body: str
    allowed_paths: tuple[str, ...]
    prerequisites: tuple[int, ...]
    evidence: tuple[str, ...]
    acceptance: tuple[str, ...]
    test_commands: tuple[tuple[str, ...], ...]
    priority: int
    risk: str
    privacy: str
    requires_owner_review: bool
    source_standard: SourceStandard
    estimate: Estimate

    def payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "key": self.key,
            "objective": self.objective,
            "title": self.title,
            "body": self.body,
            "allowed_paths": list(self.allowed_paths),
            "prerequisites": list(self.prerequisites),
            "evidence": list(self.evidence),
            "acceptance": list(self.acceptance),
            "test_commands": [list(argv) for argv in self.test_commands],
            "priority": self.priority,
            "risk": self.risk,
            "privacy": self.privacy,
            "requires_owner_review": self.requires_owner_review,
            "source_standard": {
                "compiler_abi": self.source_standard.compiler_abi,
                "tu_object_first": self.source_standard.tu_object_first,
                "original_match": self.source_standard.original_match,
                "evidence": list(self.source_standard.evidence),
            },
            "estimate": {
                "cost_micro_usd": self.estimate.cost_micro_usd,
                "runtime_minutes": self.estimate.runtime_minutes,
                "confidence": self.estimate.confidence,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload())


def validate_proposal(raw: object) -> TaskSpec:
    if not isinstance(raw, dict):
        raise ProposalError("schema", "proposal must be a JSON object")
    allowed = {
        "schema_version", "key", "objective", "title", "body", "allowed_paths",
        "prerequisites", "evidence", "acceptance", "test_commands", "priority",
        "risk", "privacy", "requires_owner_review", "source_standard", "estimate",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ProposalError("schema", f"unknown keys: {sorted(unknown)}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ProposalError("schema_version", f"must equal {SCHEMA_VERSION}")
    key = _text(raw.get("key"), code="key", limit=64)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", key):
        raise ProposalError("key", "must be a lowercase slug of 3-64 chars")
    objective = _text(raw.get("objective"), code="objective", limit=2000)
    title = _text(raw.get("title"), code="title", limit=MAX_TITLE_CHARS)
    body = _text(raw.get("body"), code="body", limit=MAX_BODY_CHARS)
    paths = _seq(raw.get("allowed_paths"), code="allowed_paths", limit=MAX_PATHS)
    paths = tuple(dict.fromkeys(_repo_path(p) for p in paths))
    prereq = _seq(raw.get("prerequisites", []), code="prerequisites", limit=MAX_PREREQUISITES, required=False)
    prerequisites: list[int] = []
    for item in prereq:
        if type(item) is not int or item <= 0:
            raise ProposalError("prerequisites", "must be positive issue numbers")
        prerequisites.append(item)
    evidence = _seq(raw.get("evidence", []), code="evidence", limit=MAX_EVIDENCE, required=False)
    evidence = tuple(_text(e, code="evidence", limit=500) for e in evidence)
    acceptance = _seq(raw.get("acceptance"), code="acceptance", limit=MAX_ACCEPTANCE)
    acceptance = tuple(_text(a, code="acceptance", limit=1000) for a in acceptance)
    commands = _seq(raw.get("test_commands"), code="test_commands", limit=MAX_TEST_COMMANDS)
    parsed_commands = tuple(_test_command(c) for c in commands)
    priority = raw.get("priority", 50)
    if type(priority) is not int or not 0 <= priority <= 100:
        raise ProposalError("priority", "must be an int in 0..100")
    risk = raw.get("risk", "medium")
    if risk not in RISK_CATEGORIES:
        raise ProposalError("risk", "must be low|medium|high")
    privacy = raw.get("privacy", "public-metadata")
    if privacy not in PRIVACY_CATEGORIES:
        raise ProposalError("privacy", "invalid privacy category")
    review = raw.get("requires_owner_review", True)
    if type(review) is not bool:
        raise ProposalError("requires_owner_review", "must be a boolean")
    if risk == "high" and not review:
        raise ProposalError("requires_owner_review", "high-risk proposals always require owner review")
    standard = SourceStandard.parse(raw.get("source_standard", {}))
    estimate = Estimate.parse(raw.get("estimate"))
    return TaskSpec(key, objective, title, body, paths, tuple(prerequisites), evidence,
                    acceptance, parsed_commands, priority, risk, privacy, review,
                    standard, estimate)


def load_proposal(path: str) -> TaskSpec:
    """Read a bounded JSON file (or stdin with '-'). No execution involved."""
    if path == "-":
        raw_bytes = os.read(0, MAX_PROPOSAL_BYTES + 1)
    else:
        if os.path.getsize(path) > MAX_PROPOSAL_BYTES:
            raise ProposalError("io", "proposal file exceeds size limit")
        with open(path, "rb") as handle:
            raw_bytes = handle.read(MAX_PROPOSAL_BYTES + 1)
    if len(raw_bytes) > MAX_PROPOSAL_BYTES:
        raise ProposalError("io", "proposal exceeds size limit")
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProposalError("json", "proposal is not valid UTF-8 JSON") from None
    return validate_proposal(raw)


# --------------------------------------------------------------------------
# DAG and scope analysis
# --------------------------------------------------------------------------

def detect_cycles(graph: Mapping[int, Sequence[int]]) -> tuple[int, ...] | None:
    """Return one cycle as a tuple, or None. Deterministic iteration order."""
    state: dict[int, int] = {}
    stack: list[int] = []

    def visit(node: int) -> tuple[int, ...] | None:
        state[node] = 1
        stack.append(node)
        for dep in sorted(graph.get(node, ())):
            if state.get(dep) == 1:
                start = stack.index(dep)
                return tuple(stack[start:] + [dep])
            if state.get(dep, 0) == 0:
                found = visit(dep)
                if found:
                    return found
        state[node] = 2
        stack.pop()
        return None

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return None


def _literal_prefix(path: str) -> str:
    parts = []
    for part in path.split("/"):
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    return "/".join(parts)


def paths_overlap(left: str, right: str) -> bool:
    """Conservative overlap test: globs widen to their literal prefix."""
    a, b = _literal_prefix(left), _literal_prefix(right)
    if a == b:
        return True
    if a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/"):
        return True
    if any(ch in left for ch in "*?[") or any(ch in right for ch in "*?["):
        return fnmatch.fnmatch(left, right) or fnmatch.fnmatch(right, left)
    return False


def find_path_conflicts(specs: Sequence[TaskSpec]) -> list[tuple[str, str, str, str]]:
    conflicts = []
    for i, first in enumerate(specs):
        for second in specs[i + 1:]:
            for a in first.allowed_paths:
                for b in second.allowed_paths:
                    if paths_overlap(a, b):
                        conflicts.append((first.key, a, second.key, b))
    return conflicts


# --------------------------------------------------------------------------
# GitHub side (transport is injectable; nothing here is used by unit tests)
# --------------------------------------------------------------------------

class Transport(Protocol):
    def viewer(self) -> Mapping[str, object]: ...
    def user(self, login: str) -> Mapping[str, object]: ...
    def repository(self, owner: str, name: str) -> Mapping[str, object]: ...
    def permission(self, owner: str, name: str, login: str) -> Mapping[str, object]: ...
    def membership(self, org: str, login: str) -> Mapping[str, object]: ...
    def issue(self, owner: str, name: str, number: int) -> Mapping[str, object]: ...
    def issue_list(self, owner: str, name: str, *, state: str = "open") -> Sequence[Mapping[str, object]]: ...
    def issue_comments(self, owner: str, name: str, number: int) -> Sequence[Mapping[str, object]]: ...
    def comment(self, owner: str, name: str, comment_id: int) -> Mapping[str, object]: ...
    def create_issue(self, owner: str, name: str, title: str, body: str,
                     labels: Sequence[str]) -> Mapping[str, object]: ...
    def update_issue(self, owner: str, name: str, number: int,
                     payload: Mapping[str, object]) -> Mapping[str, object]: ...


class GhTransport:
    """Thin ``gh api`` wrapper. Uses the operator's existing gh login only."""

    def __init__(self, runner: Callable[..., object] | None = None) -> None:
        self._run = runner or self._gh

    @staticmethod
    def _gh(args: Sequence[str], stdin: str | None = None) -> object:
        import subprocess

        completed = subprocess.run(
            ["gh", *args], input=stdin, capture_output=True, text=True, timeout=60,
        )
        if completed.returncode != 0:
            # Fixed diagnostic; never echo stderr which may contain a token echo.
            raise ProposalError("github", f"gh {' '.join(args[:2])} failed with exit {completed.returncode}")
        try:
            return json.loads(completed.stdout) if completed.stdout.strip() else {}
        except json.JSONDecodeError:
            raise ProposalError("github", "gh returned non-JSON output") from None

    def _api(self, path: str) -> Mapping[str, object]:
        result = self._run(["api", path])
        if not isinstance(result, Mapping):
            raise ProposalError("github", "unexpected API payload")
        return result

    def viewer(self) -> Mapping[str, object]:
        return self._api("user")

    def user(self, login: str) -> Mapping[str, object]:
        return self._api(f"users/{login}")

    def repository(self, owner: str, name: str) -> Mapping[str, object]:
        return self._api(f"repos/{owner}/{name}")

    def issue_list(self, owner: str, name: str, *, state: str = "open") -> Sequence[Mapping[str, object]]:
        result = self._run([
            "api", "--method", "GET", f"repos/{owner}/{name}/issues",
            "-f", f"state={state}", "-f", "per_page=100",
        ])
        return result if isinstance(result, list) else []

    def permission(self, owner: str, name: str, login: str) -> Mapping[str, object]:
        return self._api(f"repos/{owner}/{name}/collaborators/{login}/permission")

    def membership(self, org: str, login: str) -> Mapping[str, object]:
        return self._api(f"orgs/{org}/memberships/{login}")

    def issue(self, owner: str, name: str, number: int) -> Mapping[str, object]:
        return self._api(f"repos/{owner}/{name}/issues/{number}")

    def issue_comments(self, owner: str, name: str, number: int) -> Sequence[Mapping[str, object]]:
        result = self._run([
            "api", "--method", "GET", f"repos/{owner}/{name}/issues/{number}/comments",
            "-f", "per_page=100",
        ])
        return result if isinstance(result, list) else []

    def comment(self, owner: str, name: str, comment_id: int) -> Mapping[str, object]:
        return self._api(f"repos/{owner}/{name}/issues/comments/{comment_id}")

    def create_issue(self, owner: str, name: str, title: str, body: str,
                     labels: Sequence[str]) -> Mapping[str, object]:
        payload = json.dumps({"title": title, "body": body, "labels": list(labels)})
        result = self._run([
            "api", "--method", "POST", f"repos/{owner}/{name}/issues", "--input", "-",
        ], stdin=payload)
        if not isinstance(result, Mapping):
            raise ProposalError("github", "unexpected create-issue payload")
        return result

    def update_issue(self, owner: str, name: str, number: int,
                     payload: Mapping[str, object]) -> Mapping[str, object]:
        result = self._run([
            "api", "--method", "PATCH", f"repos/{owner}/{name}/issues/{number}",
            "--input", "-",
        ], stdin=json.dumps(dict(payload)))
        if not isinstance(result, Mapping):
            raise ProposalError("github", "unexpected update-issue payload")
        return result


def build_issue_body(spec: TaskSpec) -> str:
    """Deterministic issue body; the fingerprint makes publish idempotent."""
    lines = [
        FINGERPRINT_MARKER.format(spec.fingerprint),
        "<!-- Generated by nova3_team.planning_bridge; non-queued draft. -->",
        "",
        "## Objective",
        spec.objective,
        "",
        "## Scope (allowed paths)",
        *[f"- `{path}`" for path in spec.allowed_paths],
        "",
        "## Prerequisites",
        *([f"- #{n}" for n in spec.prerequisites] or ["- none"]),
        "",
        "## Evidence",
        *([f"- {item}" for item in spec.evidence] or ["- none recorded"]),
        "",
        "## Acceptance criteria",
        *[f"- [ ] {item}" for item in spec.acceptance],
        "",
        "## Test commands",
        *[f"- `{' '.join(argv)}`" for argv in spec.test_commands],
        "",
        "## Source standard",
        f"- claim state: `{spec.source_standard.original_match}`",
        f"- compiler ABI: `{spec.source_standard.compiler_abi}`",
        f"- object-first: `{'yes' if spec.source_standard.tu_object_first else 'no'}`",
        "",
        "## Risk",
        f"- risk: `{spec.risk}` / privacy: `{spec.privacy}` / owner review: "
        f"`{'required' if spec.requires_owner_review else 'not flagged'}`",
        "",
        "## Details",
        spec.body,
        "",
        "> This draft is NOT queued. A verified organization owner or repository",
        "> ADMIN must approve the exact operation before `nova:queued` is applied.",
    ]
    return "\n".join(lines).strip() + "\n"


def fingerprint_of_body(body: str) -> str | None:
    match = re.search(r"<!-- NOVA3_PLAN_FINGERPRINT: ([0-9a-f]{64}) -->", body or "")
    return match.group(1) if match else None


def find_existing(transport: Transport, owner: str, name: str, spec: TaskSpec,
                  issues: Sequence[Mapping[str, object]] | None = None) -> Mapping[str, object] | None:
    """Idempotent publish: same fingerprint or same title => existing draft."""
    candidates = issues if issues is not None else transport.issue_list(owner, name)  # type: ignore[attr-defined]
    for issue in candidates:
        if "pull_request" in issue:
            continue
        if fingerprint_of_body(str(issue.get("body", ""))) == spec.fingerprint:
            return issue
        if issue.get("title") == spec.title:
            return issue
    return None


def publish_draft(transport: Transport, owner: str, name: str, spec: TaskSpec,
                  *, apply: bool = False, issues: Sequence[Mapping[str, object]] | None = None) -> dict:
    """Create a non-queued draft issue. Dry-run unless ``apply`` is true."""
    existing = find_existing(transport, owner, name, spec, issues)
    if existing is not None:
        return {"action": "existing", "number": existing.get("number"),
                "fingerprint": spec.fingerprint, "applied": False}
    body = build_issue_body(spec)
    labels = ["nova:draft"] + (["nova:owner-review"] if spec.requires_owner_review else [])
    if not apply:
        return {"action": "would-create", "title": spec.title, "labels": labels,
                "fingerprint": spec.fingerprint, "applied": False, "body": body}
    created = transport.create_issue(owner, name, spec.title, body, labels)
    number = created.get("number")
    if type(number) is not int:
        raise ProposalError("github", "create-issue response had no issue number")
    return {"action": "created", "number": number, "fingerprint": spec.fingerprint,
            "applied": True, "url": created.get("html_url", "")}


# --------------------------------------------------------------------------
# Approval binding (delegates every decision to access_policy)
# --------------------------------------------------------------------------

class ReplayStore(Protocol):
    def reserve(self, keys: Sequence[str]) -> None: ...


class FileReplayStore:
    """Atomic cross-process reservation using O_EXCL per key.

    Directory must be outside the repository (operator-private state).
    """

    def __init__(self, directory: str) -> None:
        self.directory = directory

    def reserve(self, keys: Sequence[str]) -> None:
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        created: list[str] = []
        try:
            for key in keys:
                safe = hashlib.sha256(key.encode("utf-8")).hexdigest()
                path = os.path.join(self.directory, safe)
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    raise ProposalError("replay", "approval was already consumed") from None
                os.write(fd, key.encode("utf-8"))
                os.close(fd)
                created.append(path)
        except Exception:
            for path in created:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise


@dataclass(frozen=True)
class ApprovalRequest:
    """Exact operation the owner is asked to approve; contains no authority."""

    owner: str
    name: str
    issue_number: int
    nonce: str
    operation_arguments: dict
    operation_sha256: str
    task_spec_sha256: str
    binding: dict

    def as_json(self) -> dict:
        return {
            "kind": "nova3.approval_request",
            "repository": {"owner": self.owner, "name": self.name},
            "issue_number": self.issue_number,
            "nonce": self.nonce,
            "operation_arguments": self.operation_arguments,
            "operation_sha256": self.operation_sha256,
            "task_spec_sha256": self.task_spec_sha256,
            "binding": self.binding,
        }


def approval_comment_body(binding: Mapping[str, object], *, actor_id: int,
                          actor_login: str, issued_at: int, expires_at: int) -> str:
    """The exact JSON an owner posts to approve one request. Not a signature."""
    record = dict(binding)
    record.update({
        "kind": "nova3.approval",
        "actor_id": actor_id,
        "actor_login": actor_login,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
    })
    return _canonical(record)


def _iso(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _read(path: str, payload: Mapping[str, object], reader: int | None,
          boundary: str, now: int) -> access_policy.GitHubRead:
    return access_policy.GitHubRead(
        path=path, reader_id=reader, boundary=boundary, fetched_at=now,
        status=200, payload=payload,
    )


def build_request(owner: str, name: str, repo_payload: Mapping[str, object],
                  issue_payload: Mapping[str, object], *, action: access_policy.Action,
                  operation_arguments: dict, nonce: str, executor_user_id: int | None) -> tuple:
    repository = access_policy.Repository(
        id=int(repo_payload["id"]), owner_id=int(repo_payload["owner"]["id"]),
        owner=owner, name=name,
    )
    request = access_policy.Request(
        repository=repository, action=action, issue_number=int(issue_payload["number"]),
        operation_sha256=access_policy.operation_digest(operation_arguments),
        operation_arguments=operation_arguments, nonce=nonce,
        executor_user_id=executor_user_id,
    )
    binding = access_policy.request_binding(request, issue_payload)
    return request, binding


class PolicyQueueGate:
    """Owner/admin queue transition enforced by access_policy (fail closed).

    Collects fresh evidence at the action boundary, asks access_policy for a
    decision, then atomically reserves the returned consume_keys and only then
    returns an allowed Decision. Nothing is cached across calls.
    """

    def __init__(self, transport: Transport, replay: ReplayStore,
                 clock: Callable[[], int] = lambda: int(time.time())) -> None:
        self.transport = transport
        self.replay = replay
        self.clock = clock

    def _author_reads(self, owner: str, name: str, login: str,
                      boundary: str, now: int) -> tuple[access_policy.AccessReads, int]:
        repo_path = f"/repos/{owner}/{name}"
        user_payload = self.transport.user(login)
        user_id = int(user_payload["id"])
        return access_policy.AccessReads(
            user=_read(f"/users/{login}", user_payload, user_id, boundary, now),
            permission=_read(f"{repo_path}/collaborators/{login}/permission",
                             self.transport.permission(owner, name, login),
                             user_id, boundary, now),
        ), user_id

    def authorize(self, owner: str, name: str, number: int, *, nonce: str,
                  operation_arguments: dict,
                  consent_factory: Callable[[dict, int], access_policy.Consent | None] | None = None
                  ) -> tuple[access_policy.Decision, dict]:
        now = self.clock()
        boundary = nonce
        viewer_payload = self.transport.viewer()
        reader = int(viewer_payload["id"])
        login = str(viewer_payload.get("login", ""))
        repo_payload = self.transport.repository(owner, name)
        issue_payload = self.transport.issue(owner, name, number)
        repo_path = f"/repos/{owner}/{name}"
        viewer = access_policy.AccessReads(
            user=_read("/user", viewer_payload, reader, boundary, now),
            permission=_read(f"{repo_path}/collaborators/{login}/permission",
                             self.transport.permission(owner, name, login),
                             reader, boundary, now),
        )
        request, binding = None, {}
        try:
            request, binding = build_request(
                owner, name, repo_payload, issue_payload,
                action=access_policy.Action.APPROVE_QUEUE,
                operation_arguments=operation_arguments, nonce=nonce,
                executor_user_id=reader,
            )
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            # A payload that cannot be bound (wrong repo, missing attestation,
            # malformed issue) is a denial, never a traceback in the CLI.
            return access_policy.Decision(
                False, access_policy.Role.UNAUTHENTICATED,
                access_policy.Reason.INVALID_EVIDENCE,
            ), {}
        # Exactly one owner-written approval comment must match this binding.
        approvals: list[access_policy.ApprovalEvidence] = []
        for stub in self.transport.issue_comments(owner, name, number):
            body = str(stub.get("body", ""))
            # Only a structurally valid approval record may participate. A
            # plain comment that merely mentions "nova3.approval" must not
            # create ambiguity that blocks every approve-queue call.
            try:
                record = json.loads(body)
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict) or record.get("kind") != "nova3.approval":
                continue
            comment_id = int(stub.get("id", 0))
            comment = self.transport.comment(owner, name, comment_id)
            author_login = str(comment.get("user", {}).get("login", ""))
            if not author_login:
                continue
            author, _ = self._author_reads(owner, name, author_login, boundary, now)
            approvals.append(access_policy.ApprovalEvidence(
                comment=_read(f"{repo_path}/issues/comments/{comment_id}", comment,
                              reader, boundary, now),
                author=author,
            ))
        evidence = access_policy.Evidence(
            viewer=viewer,
            repository=_read(repo_path, repo_payload, reader, boundary, now),
            issue=_read(f"{repo_path}/issues/{number}", issue_payload, reader, boundary, now),
            approvals=tuple(approvals),
            replay=access_policy.ReplayState(boundary=boundary, fetched_at=now),
            consent=(consent_factory(binding, reader) if consent_factory else None),
        )
        decision = access_policy.authorize(request, evidence, now=now)
        if not decision.allowed:
            return decision, binding
        if not decision.consume_keys:
            raise ProposalError("policy", "allowed decision carried no consume keys")
        # Atomic: a second planner racing on the same approval loses here.
        self.replay.reserve(decision.consume_keys)
        return decision, binding


def queue_transition(transport: Transport, replay: ReplayStore, owner: str, name: str,
                     number: int, *, nonce: str, task_spec_sha256: str,
                     apply: bool, confirm: Callable[[dict], bool] | None = None,
                     clock: Callable[[], int] = lambda: int(time.time())) -> dict:
    """Ask access_policy; only an allowed decision applies ``nova:queued``."""
    arguments = {
        "issue_number": number,
        "label": "nova:queued",
        "task_spec_sha256": task_spec_sha256,
    }
    gate = PolicyQueueGate(transport, replay, clock)

    def consent_factory(binding: dict, actor_id: int) -> access_policy.Consent | None:
        # Explicit human confirmation bound to the exact displayed binding.
        if confirm is None or not confirm(binding):
            return None
        now = clock()
        return access_policy.Consent(
            request_sha256=_digest(binding), user_id=actor_id,
            issued_at=now, expires_at=now + 120,
        )

    decision, binding = gate.authorize(
        owner, name, number, nonce=nonce, operation_arguments=arguments,
        consent_factory=consent_factory,
    )
    if not decision.allowed:
        return {"action": "denied", "reason": decision.reason.value, "applied": False}
    if not apply:
        return {"action": "would-queue", "binding": binding, "applied": False}
    # Additive and idempotent: never drop unrelated labels.
    current = transport.issue(owner, name, number)
    labels = sorted({
        str(label.get("name")) for label in current.get("labels", [])
        if isinstance(label, Mapping) and label.get("name")
    } | {"nova:queued"})
    transport.update_issue(owner, name, number, {"labels": labels})
    return {"action": "queued", "binding": binding, "applied": True, "labels": labels}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _emit(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m nova3_team.planning_bridge",
        description="Validate, preview and publish provider-neutral NOVA3 plans.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a proposal JSON file")
    validate.add_argument("proposal")

    preview = sub.add_parser("preview", help="render the issue that would be created")
    preview.add_argument("proposal")

    publish = sub.add_parser("publish-draft", help="create a NON-queued draft issue")
    publish.add_argument("proposal")
    publish.add_argument("--repo", required=True, help="owner/name")
    publish.add_argument("--apply", action="store_true", help="actually create (default dry-run)")

    approve = sub.add_parser("approval-request", help="emit the exact JSON an owner must approve")
    approve.add_argument("--repo", required=True)
    approve.add_argument("--issue", type=int, required=True)
    approve.add_argument("--task-spec-sha256", required=True)
    approve.add_argument("--nonce", required=True)

    queue = sub.add_parser("approve-queue", help="owner/admin-authorized nova:queued transition")
    queue.add_argument("--repo", required=True)
    queue.add_argument("--issue", type=int, required=True)
    queue.add_argument("--task-spec-sha256", required=True)
    queue.add_argument("--nonce", required=True)
    queue.add_argument("--state-dir", required=True, help="private replay-store directory")
    queue.add_argument("--apply", action="store_true", help="apply the label (default dry-run)")

    context = sub.add_parser("context", help="export sanitised planning context")
    context.add_argument("--repo", required=True)
    context.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            spec = load_proposal(args.proposal)
            _emit({"valid": True, "key": spec.key, "fingerprint": spec.fingerprint})
            return 0
        if args.command == "preview":
            spec = load_proposal(args.proposal)
            _emit({"fingerprint": spec.fingerprint, "body": build_issue_body(spec)})
            return 0
        if args.command in {"publish-draft", "approval-request", "approve-queue", "context"}:
            owner, _, name = args.repo.partition("/")
            if not owner or not name or "/" in name:
                raise ProposalError("repo", "must be owner/name")
            transport = GhTransport()
            if args.command == "publish-draft":
                spec = load_proposal(args.proposal)
                _emit(publish_draft(transport, owner, name, spec, apply=args.apply))
                return 0
            if args.command == "approval-request":
                issue = transport.issue(owner, name, args.issue)
                repo_payload = transport.repository(owner, name)
                viewer = transport.viewer()
                _, binding = build_request(
                    owner, name, repo_payload, issue,
                    action=access_policy.Action.APPROVE_QUEUE,
                    operation_arguments={
                        "issue_number": args.issue, "label": "nova:queued",
                        "task_spec_sha256": args.task_spec_sha256,
                    },
                    nonce=args.nonce, executor_user_id=int(viewer["id"]),
                )
                _emit({"binding": binding, "post_this_comment": approval_comment_body(
                    binding, actor_id=int(viewer["id"]),
                    actor_login=str(viewer["login"]),
                    issued_at=int(time.time()),
                    expires_at=int(time.time()) + access_policy.MAX_APPROVAL_AGE - 5,
                )})
                return 0
            if args.command == "approve-queue":
                def confirm(preview: dict) -> bool:
                    print(json.dumps(preview, indent=2, sort_keys=True))
                    return True
                result = queue_transition(
                    transport, FileReplayStore(args.state_dir), owner, name, args.issue,
                    nonce=args.nonce, task_spec_sha256=args.task_spec_sha256,
                    apply=args.apply, confirm=confirm,
                )
                _emit(result)
                return 0 if result["action"] != "denied" else 3
            # context
            issues = transport.issue_list(owner, name, state="all")
            pulls = [i for i in issues if "pull_request" in i]
            repo_payload = transport.repository(owner, name)
            snapshot = build_context(
                repository=repo_payload,
                issues=[i for i in issues if "pull_request" not in i],
                pulls=pulls, policy_version=access_policy.POLICY_VERSION,
            )
            assert_sanitized(snapshot)
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2, sort_keys=True)
            _emit({"written": args.out, "issues": len(snapshot["issues"])})
            return 0
    except ProposalError as exc:
        _emit({"error": exc.code, "detail": str(exc)})
        return 2
    return 2




# --------------------------------------------------------------------------
# Sanitised context export
# --------------------------------------------------------------------------

def sanitize_text(value: str) -> str:
    if looks_secret(value):
        return "[redacted]"
    value = re.sub(r"/home/[A-Za-z0-9._-]+/[^\s`\"']*", "[local-path]", value)
    value = re.sub(r"[A-Za-z]:\\\\Users\\\\[^\s`\"']*", "[local-path]", value)
    return value


def sanitize_issue(issue: Mapping[str, object]) -> dict:
    labels = sorted(
        str(label.get("name")) for label in issue.get("labels", [])
        if isinstance(label, Mapping) and label.get("name")
    )
    return {
        "number": issue.get("number"),
        "title": sanitize_text(str(issue.get("title", "")))[:240],
        "state": issue.get("state"),
        "labels": labels,
        "prerequisites": sorted(set(
            int(m) for m in re.findall(r"#(\d+)", str(issue.get("body", "")))
        ))[:40],
        "is_pull_request": "pull_request" in issue,
    }


def build_context(*, repository: Mapping[str, object], issues: Sequence[Mapping[str, object]],
                  pulls: Sequence[Mapping[str, object]], policy_version: int,
                  blockers: Sequence[str] = (), metrics: Mapping[str, object] | None = None) -> dict:
    return {
        "schema_version": 1,
        "source_of_truth": "github-issues",
        "policy_version": policy_version,
        "repository": {
            "name": sanitize_text(str(repository.get("full_name", ""))),
            "default_branch": repository.get("default_branch"),
        },
        "issues": [sanitize_issue(i) for i in issues],
        "pull_requests": [sanitize_issue(p) for p in pulls],
        "blockers": [sanitize_text(b)[:500] for b in blockers],
        "metrics": {str(k): v for k, v in (metrics or {}).items()
                    if type(v) in (int, bool, str)},
        "excluded": ["binaries", "assets", "decompiler output", "credentials",
                     "absolute local paths", "private logs"],
    }


def assert_sanitized(snapshot: Mapping[str, object]) -> None:
    wire = _canonical(snapshot)
    if looks_secret(wire):
        raise ProposalError("context", "snapshot contains credential-like material")
    if re.search(r"/home/[A-Za-z0-9._-]+/", wire):
        raise ProposalError("context", "snapshot contains an absolute local path")
    for prefix in ("originals/", "analysis/", "generated/"):
        if prefix in wire:
            raise ProposalError("context", "snapshot references private project data")


if __name__ == "__main__":
    raise SystemExit(main())
