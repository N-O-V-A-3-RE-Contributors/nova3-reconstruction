"""Project profiles: the only project-specific input `dsh_runner` accepts.

A profile is YAML/JSON data, validated before anything runs. It answers:

* which repository and which DSH workspace this project is,
* which squad/member definitions represent its agents,
* which backend executes sessions and under what isolation guarantee,
* how long a session may run and how many model steps it may take,
* which model routes are preferred for semantic versus routine work,
* which paths a session may touch and which commands decide success.

Everything else (the queue, approvals, publishing, domain verification) belongs
to the project. A second project is a second profile file, not a code fork.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field

SCHEMA = 'dsh-runner-project/1'
NAME_RE = re.compile(r'[a-z0-9][a-z0-9._-]{1,40}\Z')
IDENTIFIER = re.compile(r'[A-Za-z0-9_./:@-]+\Z')
PERMISSIONS = ('read-only', 'workspace-write', 'danger-full-access')
BACKENDS = ('host', 'headless')
ISOLATION = ('per-task-checkout', 'shared-workspace')


class ProfileError(ValueError):
    """A profile that would run something unsafe or ambiguous."""


@dataclass(frozen=True)
class Member:
    name: str
    preset: str = 'standard'
    permission: str = 'workspace-write'
    model: str = ''
    effort: str = ''
    brief: str = ''


@dataclass(frozen=True)
class Execution:
    backend: str = 'headless'
    isolation: str = 'per-task-checkout'
    timeout_s: int = 1200
    max_steps: int = 240
    squad: str = ''
    worker_member: str = ''    # which member a task session runs as


@dataclass(frozen=True)
class Routing:
    semantic: tuple = ()
    routine: tuple = ()
    routine_tasks: tuple = ('test', 'docs', 'metadata', 'inventory', 'lint')


@dataclass(frozen=True)
class Safety:
    safe_paths: tuple = ()
    denied_paths: tuple = ()
    verify: tuple = ()


@dataclass(frozen=True)
class Profile:
    name: str
    repo: str
    workspace: str
    execution: Execution
    members: dict[str, Member]
    routing: Routing
    safety: Safety
    path: str = ''
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def worker(self) -> Member:
        """The member a task session runs as.

        Resolution order: the profile's explicit ``execution.worker_member``,
        then a member literally named ``worker``, then the first member. Several
        members without either of the first two is ambiguous and refused at parse
        time, so a project cannot silently run as the wrong agent.
        """
        wanted = self.execution.worker_member
        if wanted:
            return self.members[wanted]
        return self.members.get('worker') or next(iter(self.members.values()))

    def purpose(self, task: str) -> str:
        text = (task or '').lower()
        return 'routine' if any(token in text for token in self.routing.routine_tasks) \
            else 'semantic'

    def route_for(self, task: str) -> str:
        """Preferred model route for this task, from the profile's own policy."""
        wanted = self.routing.routine if self.purpose(task) == 'routine' \
            else self.routing.semantic
        return wanted[0] if wanted else ''

    def summary(self) -> dict:
        return {
            'name': self.name, 'repo': self.repo, 'workspace': self.workspace,
            'backend': self.execution.backend,
            'isolation': self.execution.isolation,
            'squad': self.execution.squad,
            'members': sorted(self.members),
            'verify': list(self.safety.verify),
        }


def _text(value, field, *, required=False, limit=400):
    if value is None:
        if required:
            raise ProfileError(f'{field} is required')
        return ''
    if not isinstance(value, str):
        raise ProfileError(f'{field} must be a string')
    value = value.strip()
    if required and not value:
        raise ProfileError(f'{field} is required')
    if len(value) > limit:
        raise ProfileError(f'{field} is too long')
    return value


def _strings(value, field, *, limit=40):
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProfileError(f'{field} must be a list')
    out = []
    for item in value:
        out.append(_text(item, f'{field}[]', required=True))
        if len(out) > limit:
            raise ProfileError(f'{field} has too many entries')
    return tuple(out)


def _member(name, body) -> Member:
    if not isinstance(body, dict):
        raise ProfileError(f'members.{name} must be a mapping')
    permission = _text(body.get('permission'), f'members.{name}.permission') or 'workspace-write'
    if permission not in PERMISSIONS:
        raise ProfileError(f'members.{name}.permission must be one of {PERMISSIONS}')
    model = _text(body.get('model'), f'members.{name}.model')
    if model and not IDENTIFIER.fullmatch(model):
        raise ProfileError(f'members.{name}.model is not a safe route value')
    effort = _text(body.get('effort'), f'members.{name}.effort')
    if effort and not IDENTIFIER.fullmatch(effort):
        raise ProfileError(f'members.{name}.effort is not a safe value')
    preset = _text(body.get('preset'), f'members.{name}.preset') or 'standard'
    brief = _text(body.get('brief'), f'members.{name}.brief', limit=4000)
    return Member(name=name, preset=preset, permission=permission,
                  model=model, effort=effort, brief=brief)


def parse(data: dict, *, path: str = '') -> Profile:
    if not isinstance(data, dict):
        raise ProfileError('profile must be a mapping')
    if data.get('schema') != SCHEMA:
        raise ProfileError(f'schema must be {SCHEMA}')
    name = _text(data.get('name'), 'name', required=True)
    if not NAME_RE.fullmatch(name):
        raise ProfileError('name must be a lowercase identifier')
    repo = _text(data.get('repo'), 'repo', required=True)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ProfileError('repo must be owner/name')
    workspace = _text(data.get('workspace'), 'workspace', required=True)
    if not workspace.startswith('/'):
        raise ProfileError('workspace must be an absolute path')

    raw_exec = data.get('execution') or {}
    if not isinstance(raw_exec, dict):
        raise ProfileError('execution must be a mapping')
    backend = _text(raw_exec.get('backend'), 'execution.backend') or 'headless'
    if backend not in BACKENDS:
        raise ProfileError(f'execution.backend must be one of {BACKENDS}')
    isolation = _text(raw_exec.get('isolation'), 'execution.isolation') or 'per-task-checkout'
    if isolation not in ISOLATION:
        raise ProfileError(f'execution.isolation must be one of {ISOLATION}')
    if backend == 'host' and isolation == 'per-task-checkout' and not raw_exec.get('squad'):
        raise ProfileError('host backend needs execution.squad or shared-workspace isolation')
    timeout_s = raw_exec.get('timeout_s', 1200)
    max_steps = raw_exec.get('max_steps', 240)
    if not isinstance(timeout_s, int) or not 300 <= timeout_s <= 3600:
        raise ProfileError('execution.timeout_s must be 300..3600')
    if not isinstance(max_steps, int) or not 10 <= max_steps <= 400:
        raise ProfileError('execution.max_steps must be 10..400')
    execution = Execution(backend=backend, isolation=isolation, timeout_s=timeout_s,
                          max_steps=max_steps,
                          squad=_text(raw_exec.get('squad'), 'execution.squad'),
                          worker_member=_text(raw_exec.get('worker_member'),
                                              'execution.worker_member'))

    raw_members = data.get('members') or {}
    if not isinstance(raw_members, dict) or not raw_members:
        raise ProfileError('members must be a non-empty mapping')
    members = {name_: _member(name_, body) for name_, body in raw_members.items()
               if NAME_RE.fullmatch(name_)}
    if execution.worker_member and execution.worker_member not in members:
        raise ProfileError('execution.worker_member names no member')
    if not execution.worker_member and 'worker' not in members and len(members) != 1:
        # A single member is unambiguous; several need an explicit choice.
        raise ProfileError('set execution.worker_member when there are several members')

    raw_routing = data.get('routing') or {}
    if not isinstance(raw_routing, dict):
        raise ProfileError('routing must be a mapping')
    routing = Routing(semantic=_strings(raw_routing.get('semantic'), 'routing.semantic'),
                      routine=_strings(raw_routing.get('routine'), 'routing.routine'),
                      routine_tasks=_strings(raw_routing.get('routine_tasks'),
                                             'routing.routine_tasks') or Routing.routine_tasks)
    for route in routing.semantic + routing.routine:
        if not IDENTIFIER.fullmatch(route):
            raise ProfileError(f'routing route {route!r} is not a safe value')

    raw_safety = data.get('safety') or {}
    if not isinstance(raw_safety, dict):
        raise ProfileError('safety must be a mapping')
    safety = Safety(safe_paths=_strings(raw_safety.get('safe_paths'), 'safety.safe_paths'),
                    denied_paths=_strings(raw_safety.get('denied_paths'),
                                          'safety.denied_paths'),
                    verify=_strings(raw_safety.get('verify'), 'safety.verify'))
    for path_ in safety.safe_paths + safety.denied_paths:
        if path_.startswith('/') or '..' in path_.split('/'):
            raise ProfileError(f'safety path {path_!r} must be workspace-relative')
    for command in safety.verify:
        if not command.strip() or '\n' in command:
            raise ProfileError('safety.verify entries must be single commands')

    return Profile(name=name, repo=repo, workspace=workspace, execution=execution,
                   members=members, routing=routing, safety=safety, path=path,
                   raw=dict(data))


def loads(text: str, *, path: str = '') -> Profile:
    """Parse YAML (through the DSH-bundled parser) or JSON."""
    text = text.strip()
    if text.startswith('{'):
        return parse(json.loads(text), path=path)
    from . import yaml_bridge
    try:
        data = yaml_bridge.loads(text)
    except yaml_bridge.YamlUnavailable as exc:
        raise ProfileError(f'cannot parse YAML profile: {exc}') from None
    return parse(data, path=path)


def load_profile(path: pathlib.Path) -> Profile:
    path = pathlib.Path(path)
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise ProfileError(f'cannot read profile {path}') from exc
    return loads(text, path=str(path))


def list_profiles(root: pathlib.Path) -> list[Profile]:
    folder = pathlib.Path(root) / 'config' / 'projects'
    out = []
    for path in sorted(folder.glob('*.y*ml')) + sorted(folder.glob('*.json')):
        out.append(load_profile(path))
    return out
