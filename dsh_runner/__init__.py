"""Project-agnostic durable runner for DSH agent sessions.

Why this package exists
-----------------------
`nova3_team` grew a supervision layer that is excellent for N.O.V.A. 3 and
useless anywhere else: GitHub queue labels, owner approval gate, scope guard,
NOVA3 paths. The reusable parts are not the policy, they are the *mechanics*:

* where the project lives and which DSH workspace represents it,
* which squad/member definition is "the agent" for this project,
* which backend executes a session (a native DSH Host session, or an isolated
  headless child),
* how a session's outcome is observed and reported without parsing prose,
* which model route is chosen for a given kind of work.

`dsh_runner` owns exactly those mechanics and nothing project-specific. A
project supplies a profile (see `config/projects/*.yaml`); the project's own
queue, verification and publication policy stays in that project's code.

Backends
--------
``host``
    A real DSH Host session created through the native squad API. It is visible
    in the Agents tab, can be opened in the Web UI, and shows as a run. It
    executes in the workspace the squad pins, so two tasks must not run at once
    unless the project provides per-task workspaces.

``headless``
    A bounded ``dsh --profile headless`` child, the behaviour `nova3_team` has
    used in production. Invisible in the Agents tab, but it can run anywhere
    (any cwd, so per-task checkouts are possible) and needs no Host.

Neither backend decides whether work is *correct*; that is the caller's
verification. This package never writes to a project's git, queue or secrets.
"""
from __future__ import annotations

from .profile import Profile, load_profile, list_profiles, ProfileError
from .host import HostClient, HostUnavailable, SessionReport
from .backends import (BackendError, Backend, HostBackend, HeadlessBackend,
                       backend_for)

__all__ = [
    'Profile', 'load_profile', 'list_profiles', 'ProfileError',
    'HostClient', 'HostUnavailable', 'SessionReport',
    'BackendError', 'Backend', 'HostBackend', 'HeadlessBackend', 'backend_for',
]
SCHEMA = 'dsh-runner/1'
