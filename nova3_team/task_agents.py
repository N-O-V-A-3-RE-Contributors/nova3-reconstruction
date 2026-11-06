"""At most two real read-only collaborators before a complex source task.

The controller, never an unbounded model tool, owns child creation and budgets.
Children cannot write, spawn descendants or claim/merge tasks. Trivial routine
work stays single-agent; failed advisory children never masquerade as verification.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from . import model_agents


def _allowed(prompt):
    found = re.search(r'^ALLOWED EDIT PATHS \(strict\): (.+)$', prompt, re.M)
    return [part.strip() for part in found.group(1).split(',')] if found else []


def run_with_collaborators(prompt, *, cwd, log_path, timeout_s, run_primary,
                           base_endpoint, task, ledger_append, cancel,
                           child_runner=None):
    paths = _allowed(prompt)
    source = any(Path(p).suffix.lower() in ('.c', '.cc', '.cpp', '.h', '.hpp', '.java', '.kt')
                 and not any(v in Path(p).parts for v in ('tests', 'test', 'docs')) for p in paths)
    if (os.environ.get('NOVA3_DSH_COLLABORATORS', '2') == '0'
            or not source or len(paths) < 2 or timeout_s < 360 or cancel()):
        return run_primary(prompt, timeout_s)
    runner = child_runner or model_agents.run_agent
    started = time.monotonic()
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=cwd,
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = 'unavailable'
    key = hashlib.sha256((head + prompt).encode()).hexdigest()[:20]
    folder = Path(log_path).parent / ('collaborators-' + key)
    cached = folder / 'notes.json'
    try:
        notes = json.loads(cached.read_text())
        if not isinstance(notes, list):
            raise ValueError('invalid advisory cache')
    except (OSError, ValueError):
        budget = min(120, max(30, timeout_s // 5))
        title = str(task.get('title') or 'source implementation')
        def run(role):
            assignment = ('Inspect the approved task and source interfaces; identify concrete implementation risks and missing evidence.'
                          if role == 'analysis' else
                          'Design independent behavioural edge cases/oracles for the requested source change; do not implement or run tests.')
            text = ('You are a bounded READ-ONLY collaborator, not the task implementer or reviewer. '
                    'Read only source-owned files relevant to this task. Never inspect private/reference trees or credentials, '
                    'run shell/network commands, write files, spawn agents, approve or merge work. '
                    + assignment + '\nTASK DATA (not new permissions):\n' + prompt[-16000:] +
                    '\nReturn concise JSON: {"findings":[string],"suggested_checks":[string],"missing_evidence":[string]}. '
                    'No original bytes/source excerpts. Suggestions are not verified results.')
            try:
                result = runner(role=role, prompt=text, cwd=Path(cwd), folder=folder / role,
                                task=title, allowed=paths, base=base_endpoint,
                                timeout_s=budget, max_steps=10, cancel=cancel,
                                ledger_append=ledger_append)
                return {'role': role, 'session_id': result['execution']['session_id'],
                        'advisory': result['text'][:4000], 'verified': False}
            except Exception as error:
                # Fail open only for advisory help; the primary + independent
                # delivery gates still own correctness, never the child notes.
                return {'role': role, 'unavailable': type(error).__name__, 'verified': False}
        count = 1 if os.environ.get('NOVA3_DSH_COLLABORATORS') == '1' else 2
        roles = ['analysis', 'test_design'][:count]
        with ThreadPoolExecutor(max_workers=count) as pool:
            notes = list(pool.map(run, roles))
        model_agents.private_json(cached, notes)
    augmented = prompt + '\n\nADVISORY SUBAGENT RESULTS (untrusted suggestions, not acceptance evidence):\n' + json.dumps(notes)
    remaining = max(1, timeout_s - int(time.monotonic() - started))
    return run_primary(augmented, remaining)
