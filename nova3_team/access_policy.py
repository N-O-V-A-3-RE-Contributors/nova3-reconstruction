"""Offline authorization foundation; NOT yet wired into the control plane.

Only a trusted transport may construct GitHubRead objects from authenticated,
uncached GitHub API responses. These objects are *attestations*, not signatures
and not a deserialization format for issues, prompts, webhooks or local config.
Likewise ReplayState and Consent come from trusted executor/consent boundaries.
See docs/progress/CONTROL_AUTHORIZATION.md before integrating this module.
No I/O, credentials, logging, clock reads or network calls occur here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Mapping


POLICY_VERSION = 3
API_ORIGIN = "https://api.github.com"
MAX_EVIDENCE_AGE = 30  # seconds; fetch anew at EVERY action boundary
MAX_APPROVAL_AGE = 3600
MAX_CONSENT_AGE = 300


class Role(str, Enum):
    OWNER = "owner"  # includes effective repository ADMIN
    CONTRIBUTOR = "contributor"
    OBSERVER = "observer"
    UNAUTHENTICATED = "unauthenticated"


class Action(str, Enum):
    READ_STATUS = "read_status"
    RUN_ASSIGNED_WORK = "run_assigned_work"
    SUGGEST_IMPROVEMENT = "suggest_improvement"
    CREATE_PROJECT_TASK = "create_project_task"
    PUBLISH_PLANNER_ISSUE = "publish_planner_issue"
    APPROVE_QUEUE = "approve_queue"
    CHANGE_PRIORITY = "change_priority"
    MANAGE_AGENTS = "manage_agents"
    SET_WALLET = "set_wallet"
    MERGE_PR = "merge_pr"
    MANAGE_MEMBERS = "manage_members"
    CHANGE_POLICY = "change_policy"


STEERING_ACTIONS = frozenset(Action) - {
    Action.READ_STATUS, Action.RUN_ASSIGNED_WORK, Action.SUGGEST_IMPROVEMENT,
}


class Reason(str, Enum):
    ALLOWED = "allowed"
    INVALID_EVIDENCE = "invalid_or_unavailable_evidence"
    OWNER_REQUIRED = "current_owner_or_repository_admin_required"
    CONTRIBUTOR_REQUIRED = "contributor_required"
    APPROVAL_REQUIRED = "exact_current_approval_required"
    CONSENT_REQUIRED = "explicit_bound_human_consent_required"
    ASSIGNMENT_REQUIRED = "coordinator_assignment_required"
    REPLAY = "approval_revoked_or_already_consumed"


@dataclass(frozen=True)
class Repository:
    id: int
    owner_id: int
    owner: str
    name: str

    @property
    def path(self) -> str:
        _positive(self.id)
        _positive(self.owner_id)
        for value in (self.owner, self.name):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value):
                raise ValueError("invalid repository")
        return f"/repos/{self.owner}/{self.name}"


@dataclass(frozen=True)
class Request:
    repository: Repository
    action: Action
    issue_number: int
    operation_sha256: str  # canonical digest of ALL operation arguments
    operation_arguments: dict[str, object]  # exact canonical JSON arguments
    nonce: str  # unique 256-bit executor challenge, not a prompt-selected value
    executor_user_id: int | None = None  # binds approvals to the acting account


@dataclass(frozen=True)
class GitHubRead:
    """Trusted transport receipt, scoped to /user identity and this boundary.

    `reader_id` is the numeric /user ID of the authenticated credential used
    for the request, never the subject of a permission/membership lookup.
    Headers, tokens, raw errors and transport clients must NOT be stored here.
    """

    path: str
    reader_id: int | None
    boundary: str
    fetched_at: int
    status: int
    payload: Mapping[str, object] = field(repr=False)
    origin: str = API_ORIGIN


@dataclass(frozen=True)
class AccessReads:
    user: GitHubRead
    permission: GitHubRead
    membership: GitHubRead | None = None


@dataclass(frozen=True)
class ApprovalEvidence:
    comment: GitHubRead
    author: AccessReads  # current /users/login + current permission/membership


@dataclass(frozen=True)
class ReplayState:
    """Fresh authoritative durable state; never take this from issue text.

    Executor MUST atomically reserve all Decision.consume_keys against this
    state before side effects; fail on a concurrent reservation. Pure decisions
    alone cannot enforce replay protection. Do not release keys on uncertainty.
    """

    boundary: str
    fetched_at: int
    consumed: frozenset[str] = frozenset()
    revoked_comments: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Consent:
    """Trusted explicit human confirmation, never parsed from an AI answer.

    Bind the displayed full operation to its request digest. For local workers,
    this permits only the contributor's own provider; project spending requires
    MANAGE_AGENTS/SET_WALLET authorization instead.
    """

    request_sha256: str
    user_id: int
    issued_at: int
    expires_at: int
    project_spending: bool = False


@dataclass(frozen=True)
class CoordinatorIdentity:
    """Reviewed deployment trust root, NOT a role from task/config input.

    A coordinator signature here means a live GitHub comment authenticated as
    this GitHub App AND bot ID, not a marker claiming to be a coordinator.
    """

    app_id: int
    bot_user_id: int


@dataclass(frozen=True)
class Evidence:
    viewer: AccessReads | None = None
    repository: GitHubRead | None = None
    issue: GitHubRead | None = None
    approvals: tuple[ApprovalEvidence, ...] = ()
    replay: ReplayState | None = None
    consent: Consent | None = None
    assignment: GitHubRead | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    role: Role
    reason: Reason
    request_sha256: str | None = None
    consume_keys: tuple[str, ...] = ()


class _Denied(ValueError):
    pass


def _positive(value: object) -> None:
    if type(value) is not int or value <= 0:
        raise _Denied()


def _digest(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _Denied()


def _time(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise _Denied()
    return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _fresh(when: object, now: int) -> None:
    if type(when) is not int or not 0 <= now - when <= MAX_EVIDENCE_AGE:
        raise _Denied()


def _json_value(value: object, depth: int = 0) -> None:
    # No floats (money uses integer minor units), non-string keys, custom
    # objects, NaNs, tuples or JSON key coercion/canonicalization collisions.
    if depth > 32:
        raise ValueError("invalid operation")
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError("invalid operation")


def _canonical_json(value: object) -> str:
    """Validated compact sorted-key ASCII JSON; rejects ambiguous values."""
    _json_value(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def operation_digest(arguments: dict[str, object]) -> str:
    """Stable exact JSON SHA256; include price, payor, head SHA, targets, etc.

    This digest is integrity binding, NEVER evidence of authorization.
    """
    if type(arguments) is not dict:
        raise ValueError("invalid operation")
    return hashlib.sha256(_canonical_json(arguments).encode("utf-8")).hexdigest()


def _validate_binding_request(request: Request) -> None:
    """Validation for display/binding records; executor may be absent.

    A standing task-level approval legitimately has no executor user ID, so
    request_binding cannot reuse the stricter one-shot authorize validation.
    This validates every field the binding exposes and grants no authority.
    """
    if not isinstance(request, Request) or not isinstance(request.action, Action):
        raise _Denied()
    request.repository.path
    _positive(request.issue_number)
    _digest(request.operation_sha256)
    _operation_arguments(request)
    _digest(request.nonce)
    if request.executor_user_id is not None:
        _positive(request.executor_user_id)


def _operation_arguments(request: Request) -> dict[str, object]:
    """Require the canonical arguments whose digest the request claims.

    Returns a detached deep copy so later mutation of caller-held JSON cannot
    change an approval binding built from this request.
    """
    arguments = request.operation_arguments
    if type(arguments) is not dict or type(request.operation_sha256) is not str:
        raise _Denied()
    try:
        wire = _canonical_json(arguments)
    except ValueError:
        raise _Denied() from None
    if hashlib.sha256(wire.encode("utf-8")).hexdigest() != request.operation_sha256:
        raise _Denied()
    return json.loads(wire)


def _read(receipt: GitHubRead | None, path: str, request: Request,
          reader_id: int | None, now: int) -> Mapping[str, object]:
    if not isinstance(receipt, GitHubRead):
        raise _Denied()
    if (receipt.origin != API_ORIGIN or receipt.path != path or receipt.status != 200
            or type(receipt.status) is not int or receipt.reader_id != reader_id
            or (reader_id is not None and type(receipt.reader_id) is not int)
            or receipt.boundary != request.nonce or not isinstance(receipt.payload, Mapping)):
        raise _Denied()
    _fresh(receipt.fetched_at, now)
    return receipt.payload


def _person(user: Mapping[str, object]) -> tuple[int, str]:
    ident, login = user["id"], user["login"]
    _positive(ident)
    if user.get("type") != "User" or not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]+", login):
        raise _Denied()
    return ident, login


def _role(access: AccessReads, request: Request, reader_id: int,
          now: int, *, viewer: bool, organization: bool) -> tuple[Role, int, str]:
    if not isinstance(access, AccessReads):
        raise _Denied()
    path = "/user" if viewer else access.user.path
    user = _read(access.user, path, request, reader_id, now)
    ident, login = _person(user)
    if (viewer and ident != reader_id) or (not viewer and path != f"/users/{login}"):
        raise _Denied()
    base = request.repository.path
    permission = _read(access.permission, f"{base}/collaborators/{login}/permission", request, reader_id, now)
    if _person(permission["user"]) != (ident, login):
        raise _Denied()
    effective = permission["permission"]
    if effective not in {"admin", "maintain", "write", "triage", "read", "none"}:
        raise _Denied()
    owner = effective == "admin"
    if access.membership is not None:
        if not organization:
            raise _Denied()
        member = _read(access.membership, f"/orgs/{request.repository.owner}/memberships/{login}",
                       request, reader_id, now)
        if (_person(member["user"]) != (ident, login)
                or type(member["organization"]["id"]) is not int
                or member["organization"]["id"] != request.repository.owner_id
                or member["organization"]["login"] != request.repository.owner
                or member["state"] != "active" or member["role"] not in {"admin", "member"}):
            raise _Denied()
        owner = owner or member["role"] == "admin"
    role = Role.OWNER if owner else (
        Role.CONTRIBUTOR if effective in {"maintain", "write", "triage"} else Role.OBSERVER)
    return role, ident, login


def _task_binding(request: Request, issue: Mapping[str, object]) -> dict[str, object]:
    _positive(issue["id"])
    if (type(issue["number"]) is not int or issue["number"] != request.issue_number
            or not isinstance(issue["title"], str) or not isinstance(issue["body"], str)
            or issue.get("state") != "open" or "pull_request" in issue):
        raise _Denied()
    # The receipt path proves where the transport *asked*; the payload must also
    # attest that it belongs to this exact repository. Without this, a mixed-up
    # or cross-repository collection could bind another repo's issue body while
    # the binding's repository_id was copied from the request.
    issue_endpoint = f"{API_ORIGIN}{request.repository.path}/issues/{request.issue_number}"
    if (issue.get("repository_url") != f"{API_ORIGIN}{request.repository.path}"
            or issue.get("url") != issue_endpoint):
        raise _Denied()
    # `updated_at` is activity metadata: GitHub bumps it for comments, label
    # and assignment changes as well as edits. It must never be part of the
    # authorization binding, or an unrelated comment invalidates a fresh
    # approval. Bind only immutable issue identity and the exact task content.
    _time(issue["updated_at"])
    _time(issue["created_at"])
    arguments = _operation_arguments(request)
    task = {key: issue[key] for key in ("id", "number", "title", "body", "created_at")}
    return {
        "version": POLICY_VERSION, "origin": API_ORIGIN,
        "repository_id": request.repository.id, "owner_id": request.repository.owner_id,
        "issue_id": issue["id"], "issue_number": request.issue_number,
        "task_spec_sha256": operation_digest(task), "action": request.action.value,
        "operation_sha256": request.operation_sha256,
        "operation_arguments": arguments, "nonce": request.nonce,
        "executor_user_id": request.executor_user_id,
    }


def request_binding(request: Request, issue: Mapping[str, object]) -> dict[str, object]:
    """Build the record binding for the human approval UI; grants no authority.

    Hashes immutable issue identity plus exact title/body. Metadata timestamps
    (updated_at) and comments are deliberately excluded: they are mutable
    activity, not task content, and must not invalidate a valid approval.
    Caller must obtain the issue through the trusted collector.
    """
    _validate_binding_request(request)
    return _task_binding(request, issue)


def _validate_request(request: Request) -> None:
    if not isinstance(request, Request) or not isinstance(request.action, Action):
        raise _Denied()
    request.repository.path
    _positive(request.issue_number)
    _digest(request.operation_sha256)
    _operation_arguments(request)
    _digest(request.nonce)
    if request.executor_user_id is not None:
        _positive(request.executor_user_id)
    elif request.action is not Action.READ_STATUS:
        raise _Denied()


def _record(comment: GitHubRead, request: Request, reader: int, now: int,
            binding: dict[str, object], *, kind: str) -> tuple[Mapping[str, object], dict[str, object]]:
    # Comment endpoint ID is authenticated by transport, not parsed from a URL
    # supplied in the comment's text.
    _positive(comment.payload["id"])
    item = _read(comment, f"{request.repository.path}/issues/comments/{comment.payload['id']}", request, reader, now)
    expected_issue = f"{API_ORIGIN}{request.repository.path}/issues/{request.issue_number}"
    if item["issue_url"] != expected_issue or item["created_at"] != item["updated_at"]:
        raise _Denied()
    body = item["body"]
    if not isinstance(body, str) or len(body) > 8192:
        raise _Denied()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _Denied()
            result[key] = value
        return result

    record = json.loads(body, object_pairs_hook=unique)
    extra = {"kind", "actor_id", "actor_login", "issued_at", "expires_at"}
    if kind == "nova3.assignment":
        extra |= {"assignee_id"}
    if not isinstance(record, dict) or record.keys() != binding.keys() | extra:
        raise _Denied()
    # Compare canonical JSON wire, not Python equality: bool must never equal
    # int, including inside nested argument payloads.
    for key, value in binding.items():
        try:
            same = _canonical_json(record[key]) == _canonical_json(value)
        except ValueError:
            raise _Denied() from None
        if not same:
            raise _Denied()
    if record["kind"] != kind:
        raise _Denied()
    issued, expires, created = _time(record["issued_at"]), _time(record["expires_at"]), _time(item["created_at"])
    if not (issued <= created <= now < expires
            and 0 < expires - issued <= MAX_APPROVAL_AGE
            and created - issued <= MAX_CONSENT_AGE):
        raise _Denied()
    _positive(record["actor_id"])
    if not isinstance(record["actor_login"], str) or not record["actor_login"]:
        raise _Denied()
    return item, record


def _consented(consent: Consent | None, digest: str, actor: int, now: int,
               *, local: bool) -> bool:
    return (isinstance(consent, Consent) and consent.request_sha256 == digest
            and type(consent.user_id) is int and consent.user_id == actor
            and type(consent.issued_at) is int and type(consent.expires_at) is int
            and consent.issued_at <= now < consent.expires_at
            and 0 < consent.expires_at - consent.issued_at <= MAX_CONSENT_AGE
            and type(consent.project_spending) is bool
            and (not local or not consent.project_spending))


def authorize(request: Request, evidence: Evidence, *, now: int,
              coordinator: CoordinatorIdentity | None = None) -> Decision:
    """Decide against freshly collected evidence; no execution is performed.

    Never serialize an allow result for later execution. Executor must re-fetch,
    re-decide and atomically consume returned keys immediately before acting.
    All steering requires bound human consent; work requires local consent.
    READ_STATUS/SUGGEST_IMPROVEMENT never authorize queue/label/task mutations.
    """
    role = Role.UNAUTHENTICATED

    def deny(reason: Reason) -> Decision:
        return Decision(False, role, reason)

    try:
        _validate_request(request)
        if type(now) is not int or now < 0 or not isinstance(evidence, Evidence):
            raise _Denied()
        base = request.repository.path
        # Anonymous status only from a fresh, explicitly anonymous PUBLIC read.
        if evidence.viewer is None:
            repo = _read(evidence.repository, base, request, None, now)
            _repository_matches(repo, request.repository)
            if repo["private"] is False and request.action is Action.READ_STATUS:
                return Decision(True, role, Reason.ALLOWED)
            return deny(Reason.INVALID_EVIDENCE)
        reader = evidence.viewer.user.reader_id
        _positive(reader)
        repo = _read(evidence.repository, base, request, reader, now)
        _repository_matches(repo, request.repository)
        organization = repo["owner"]["type"] == "Organization"
        role, actor, _ = _role(evidence.viewer, request, reader, now, viewer=True, organization=organization)
        if request.executor_user_id is not None and request.executor_user_id != actor:
            raise _Denied()
        if request.action in {Action.READ_STATUS, Action.SUGGEST_IMPROVEMENT}:
            return Decision(True, role, Reason.ALLOWED)
        if request.action in STEERING_ACTIONS and role is not Role.OWNER:
            return deny(Reason.OWNER_REQUIRED)
        if request.action is Action.RUN_ASSIGNED_WORK and role not in {Role.OWNER, Role.CONTRIBUTOR}:
            return deny(Reason.CONTRIBUTOR_REQUIRED)
        issue = _read(evidence.issue, f"{base}/issues/{request.issue_number}", request, reader, now)
        binding = _task_binding(request, issue)
        if _time(issue["updated_at"]) > now:
            raise _Denied()
        created = _time(issue["created_at"])
        digest = operation_digest(binding)
        if type(evidence.approvals) is not tuple or len(evidence.approvals) != 1:
            return deny(Reason.APPROVAL_REQUIRED)
        approval = evidence.approvals[0]
        comment, record = _record(approval.comment, request, reader, now, binding, kind="nova3.approval")
        author_role, author_id, author_login = _role(approval.author, request, reader, now,
                                                   viewer=False, organization=organization)
        if (author_role is not Role.OWNER or _person(comment["user"]) != (author_id, author_login)
                or record["actor_id"] != author_id or record["actor_login"] != author_login
                or _time(record["issued_at"]) < created):
            return deny(Reason.APPROVAL_REQUIRED)
        # A steering caller must explicitly approve its own action; a worker
        # may execute a different owner's approved assignment.
        if request.action in STEERING_ACTIONS and author_id != actor:
            return deny(Reason.APPROVAL_REQUIRED)
        replay = evidence.replay
        if not isinstance(replay, ReplayState) or replay.boundary != request.nonce:
            raise _Denied()
        _fresh(replay.fetched_at, now)
        if type(replay.consumed) is not frozenset or type(replay.revoked_comments) is not frozenset:
            raise _Denied()
        for key in replay.consumed:
            if not isinstance(key, str) or not re.fullmatch(
                    r"github:[1-9][0-9]*:(?:comment:[1-9][0-9]*|request:[0-9a-f]{64})", key):
                raise _Denied()
        for ident in replay.revoked_comments:
            _positive(ident)
        keys = [f"github:{request.repository.id}:comment:{comment['id']}",
                f"github:{request.repository.id}:request:{request.nonce}"]
        comment_ids = [comment["id"]]
        local = request.action is Action.RUN_ASSIGNED_WORK
        if not _consented(evidence.consent, digest, actor, now, local=local):
            return deny(Reason.CONSENT_REQUIRED)
        if local:
            if not isinstance(coordinator, CoordinatorIdentity) or evidence.assignment is None:
                return deny(Reason.ASSIGNMENT_REQUIRED)
            _positive(coordinator.app_id)
            _positive(coordinator.bot_user_id)
            assigned, grant = _record(evidence.assignment, request, reader, now, binding, kind="nova3.assignment")
            bot = assigned["user"]
            _positive(grant["assignee_id"])
            if (bot.get("type") != "Bot" or type(bot.get("id")) is not int
                    or bot["id"] != coordinator.bot_user_id
                    or assigned["performed_via_github_app"]["id"] != coordinator.app_id
                    or type(assigned["performed_via_github_app"]["id"]) is not int
                    or grant["actor_id"] != bot["id"] or grant["actor_login"] != bot["login"]
                    or grant["assignee_id"] != actor
                    or _time(grant["issued_at"]) < _time(record["issued_at"])):
                return deny(Reason.ASSIGNMENT_REQUIRED)
            keys.append(f"github:{request.repository.id}:comment:{assigned['id']}")
            comment_ids.append(assigned["id"])
        if any(key in replay.consumed for key in keys) or any(i in replay.revoked_comments for i in comment_ids):
            return deny(Reason.REPLAY)
        return Decision(True, role, Reason.ALLOWED, digest, tuple(keys))
    except (_Denied, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        # Fixed diagnostics only: never echo API payloads, task text or secrets.
        return deny(Reason.INVALID_EVIDENCE)


_BINDING_KEYS = frozenset({
    "version", "origin", "repository_id", "owner_id", "issue_id", "issue_number",
    "task_spec_sha256", "action", "operation_sha256", "operation_arguments",
    "nonce", "executor_user_id",
})


def _validate_binding(binding: object) -> None:
    """Validate a binding dict supplied by durable state, not by a prompt.

    The binding may come from the coordinator's lease, which is untrusted issue
    text, so every field is re-validated here; approval-record equality still
    supplies the cryptographic binding to the owner's comment.
    """
    if type(binding) is not dict or set(binding) != _BINDING_KEYS:
        raise _Denied()
    if type(binding["version"]) is not int or binding["version"] != POLICY_VERSION:
        raise _Denied()
    if binding["origin"] != API_ORIGIN:
        raise _Denied()
    for key in ("repository_id", "owner_id", "issue_id", "issue_number"):
        _positive(binding[key])
    _digest(binding["task_spec_sha256"])
    _digest(binding["operation_sha256"])
    _digest(binding["nonce"])
    if not isinstance(binding["action"], str) or binding["action"] not in {a.value for a in Action}:
        raise _Denied()
    arguments = binding["operation_arguments"]
    if type(arguments) is not dict:
        raise _Denied()
    if hashlib.sha256(_canonical_json(arguments).encode("utf-8")).hexdigest() != binding["operation_sha256"]:
        raise _Denied()
    if binding["executor_user_id"] is not None:
        _positive(binding["executor_user_id"])


def verify_approval_record(binding: Mapping[str, object], approval: ApprovalEvidence,
                           request: Request, *, reader: int, now: int,
                           organization: bool, replay: ReplayState | None) -> Decision:
    """Re-verify one owner/admin approval against a previously checked binding.

    Used by pre-publish worker revalidation, where the issue body has since been
    extended with the coordinator's lease block and can no longer be re-hashed
    as the approved task revision. The binding must be validated structurally
    and must agree with the caller's Request; the approval comment, its current
    author role and the replay ledger are re-read live.
    """
    role = Role.UNAUTHENTICATED

    def deny(reason: Reason) -> Decision:
        return Decision(False, role, reason)

    try:
        _validate_binding(binding)
        if not isinstance(request, Request) or not isinstance(approval, ApprovalEvidence):
            raise _Denied()
        request.repository.path
        _positive(request.issue_number)
        _digest(request.operation_sha256)
        _digest(request.nonce)
        if (binding["repository_id"] != request.repository.id
                or binding["owner_id"] != request.repository.owner_id
                or binding["issue_number"] != request.issue_number
                or binding["action"] != request.action.value
                or binding["operation_sha256"] != request.operation_sha256
                or binding["nonce"] != request.nonce
                or binding["executor_user_id"] != request.executor_user_id):
            raise _Denied()
        _positive(reader)
        if type(now) is not int or now < 0:
            raise _Denied()
        comment, record = _record(approval.comment, request, reader, now, dict(binding), kind="nova3.approval")
        author_role, author_id, author_login = _role(
            approval.author, request, reader, now, viewer=False, organization=organization)
        if (author_role is not Role.OWNER
                or _person(comment["user"]) != (author_id, author_login)
                or record["actor_id"] != author_id or record["actor_login"] != author_login):
            return deny(Reason.APPROVAL_REQUIRED)
        if replay is not None:
            if not isinstance(replay, ReplayState) or replay.boundary != request.nonce:
                raise _Denied()
            _fresh(replay.fetched_at, now)
            if type(replay.consumed) is not frozenset or type(replay.revoked_comments) is not frozenset:
                raise _Denied()
            for key in replay.consumed:
                if not isinstance(key, str) or not re.fullmatch(
                        r"github:[1-9][0-9]*:(?:comment:[1-9][0-9]*|request:[0-9a-f]{64})", key):
                    raise _Denied()
            for ident in replay.revoked_comments:
                _positive(ident)
            if (f"github:{request.repository.id}:comment:{comment['id']}" in replay.consumed
                    or f"github:{request.repository.id}:request:{request.nonce}" in replay.consumed
                    or comment["id"] in replay.revoked_comments):
                return deny(Reason.REPLAY)
        return Decision(True, Role.OWNER, Reason.ALLOWED, operation_digest(dict(binding)))
    except (_Denied, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return deny(Reason.INVALID_EVIDENCE)


def verify_owner_approval(request: Request, evidence: Evidence, *, now: int) -> Decision:
    """Verify a *standing* owner/admin approval for one exact task revision.

    Unlike authorize(), the evaluating process is not the approver and needs no
    repository role of its own: the GitHub comment written by a currently
    verified repository ADMIN or organization owner is the human act being
    checked. This is what a coordinator or worker uses to gate queue admission.

    The approval is bound to the immutable task-spec digest, action, repository,
    issue number and issue revision, but deliberately not to a worker's numeric
    ID (`executor_user_id` must be None). One approval therefore admits exactly
    this task revision; it is not transferable to an edited task or another
    repository. Callers MUST deduplicate *transitions* themselves (for example a
    `(repository, issue, task_spec_sha256, transition, token)` ledger) because
    this standing grant is intentionally reusable for a legitimate release and
    requeue of the same unchanged revision. Consumed/revoked approval comments
    still deny, so an owner can withdraw the grant.

    The local Consent object is not required here: it protects the owner's own
    one-shot execution path (authorize), not a third party's verification of an
    already-published approval. Never treat this result as permission for the
    verifier to steer the repository itself.
    """
    role = Role.UNAUTHENTICATED

    def deny(reason: Reason) -> Decision:
        return Decision(False, role, reason)

    try:
        if not isinstance(request, Request) or request.action not in STEERING_ACTIONS:
            raise _Denied()
        request.repository.path
        _positive(request.issue_number)
        _digest(request.operation_sha256)
        _operation_arguments(request)
        _digest(request.nonce)
        if request.executor_user_id is not None:
            raise _Denied()
        if type(now) is not int or now < 0 or not isinstance(evidence, Evidence):
            raise _Denied()
        base = request.repository.path
        if not isinstance(evidence.repository, GitHubRead):
            raise _Denied()
        reader = evidence.repository.reader_id
        _positive(reader)
        repo = _read(evidence.repository, base, request, reader, now)
        _repository_matches(repo, request.repository)
        organization = repo["owner"]["type"] == "Organization"
        issue = _read(evidence.issue, f"{base}/issues/{request.issue_number}", request, reader, now)
        binding = _task_binding(request, issue)
        if _time(issue["updated_at"]) > now:
            raise _Denied()
        if type(evidence.approvals) is not tuple or len(evidence.approvals) != 1:
            return deny(Reason.APPROVAL_REQUIRED)
        approval = evidence.approvals[0]
        if _time(approval.comment.payload.get("created_at")) < _time(issue["created_at"]):
            return deny(Reason.APPROVAL_REQUIRED)
        return verify_approval_record(
            binding, approval, request, reader=reader, now=now,
            organization=organization, replay=evidence.replay)
    except (_Denied, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return deny(Reason.INVALID_EVIDENCE)


def _repository_matches(repo: Mapping[str, object], target: Repository) -> None:
    if (type(repo["id"]) is not int or repo["id"] != target.id
            or type(repo["owner"]["id"]) is not int or repo["owner"]["id"] != target.owner_id
            or repo["owner"]["login"] != target.owner or repo["name"] != target.name
            or repo["owner"]["type"] not in {"Organization", "User"}
            or type(repo["private"]) is not bool):
        raise _Denied()
