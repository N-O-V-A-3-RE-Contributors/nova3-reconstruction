"""Bounded, independently scoped DSH model sessions on owner-configured routes.

No role can spawn agents, use shell/network tools, read references/credentials,
or approve/merge its own output. Python owns GitHub and deterministic verification.
"""
from __future__ import annotations
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from . import dsh_runtime as runtime, dsh_model_router as routing


class AgentUnavailable(RuntimeError):
    pass


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = json.dumps(value, sort_keys=True, indent=2) + '\n'
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(raw)
    os.replace(temporary, path)


def default_endpoint(project):
    home = runtime.discover_home()
    binary = runtime.discover_binary(Path(project), home=home)
    if not binary:
        raise AgentUnavailable('Installed DSH runtime unavailable')
    return runtime.DshEndpoint(binary, home)


def role_endpoint(base, *, role, cwd, folder, allowed=(), max_steps=16):
    if role not in ('reviewer', 'repairer', 'analysis', 'test_design'):
        raise ValueError('Unknown bounded role')
    folder = Path(folder)
    root = Path(cwd).resolve()
    paths = []
    for item in allowed:
        p = Path(item)
        if p.is_absolute() or '..' in p.parts or not item:
            raise ValueError('Unsafe task scope')
        paths.append(item)
    readonly = role != 'repairer'
    # File content must come from the tracked task checkout, not arbitrary
    # untracked local files whose names may not look like credentials.
    try:
        listed = subprocess.run(['git', 'ls-files', '-z'], cwd=root,
                                capture_output=True, text=True, timeout=10, check=True)
        readable = [p for p in listed.stdout.split('\0') if p]
    except (OSError, subprocess.SubprocessError):
        readable = []  # supplied task/diff remains available; unknown files don't
    if not readonly:
        readable += paths  # a newly written, explicitly approved source file
    mode = 'read-only' if readonly else 'workspace-write'
    guard = Path(__file__).with_name('agent_guard.mjs').resolve()
    patches = [
        {'id': 'sandbox-policy', 'config': {'mode': mode, 'workspaceRoot': str(root)}},
        {'id': 'approval', 'config': {'policy': 'never'}},
        {'id': 'permission', 'config': {'defaultPreset': mode,
            'presets': {mode: {'sandbox': mode, 'approval': 'never'}}}},
        {'id': 'tools', 'config': {'mode': 'native', 'maxParallelSubCalls': 1}},
        {'insert': [{'id': 'nova3-bounded-role', 'name': str(guard), 'config': {
            'root': str(root), 'role': role, 'allowedWrites': paths, 'allowedReads': readable,
            'maxToolCalls': 40 if role == 'repairer' else 20}}]},
    ]
    digest = hashlib.sha256(json.dumps(patches, sort_keys=True).encode()).hexdigest()[:20]
    path = folder / 'role-overlays' / (digest + '.json')
    private_json(path, patches)
    return replace(base, role_patch=path, max_steps=max_steps)


def final_text(outcome):
    for event in reversed(outcome.events):
        if event.get('type') == 'final':
            return str(event.get('text') or '')
    return ''


def run_agent(*, role, prompt, cwd, folder, task, allowed=(), base=None,
              timeout_s=180, max_steps=16, cancel=lambda: False, ledger_append=None):
    started = time.monotonic()
    folder, cwd = Path(folder), Path(cwd)
    base = role_endpoint(base or default_endpoint(cwd), role=role, cwd=cwd,
                         folder=folder, allowed=allowed, max_steps=max_steps)
    router = routing.Router(base, folder / 'router')
    selected = []
    def account(outcome, route=None):
        record = dict(outcome.summary(), task=task, role=role,
                      route=route or (selected[-1] if selected else {}))
        if ledger_append:
            ledger_append(record)
        private_json(folder / 'last-session.json', record)
    outcome = router.run(prompt, task=('Test design ' if role == 'test_design' else
                                      'Independent semantic review ' if role == 'reviewer' else
                                      'Source analysis ' if role == 'analysis' else 'Source repair ') + task,
                         cwd=cwd, log_path=folder / 'session.log',
                         timeout_s=max(0.05, timeout_s - (time.monotonic() - started)),
                         cancel=cancel, on_failure=account, on_selected=selected.append)
    account(outcome)
    if not outcome.ok:
        raise AgentUnavailable(f'{role}: {outcome.status} ({outcome.error_code or "no error code"})')
    if not outcome.session_id:
        raise AgentUnavailable('Role session returned no authoritative identity')
    return {'text': final_text(outcome), 'outcome': outcome.summary(),
            'execution': {'role': role, 'session_id': outcome.session_id,
                          'read_only': role != 'repairer', 'independent': True}}
