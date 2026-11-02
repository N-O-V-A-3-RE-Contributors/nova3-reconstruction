"""Who may control the NOVA3 automation.

Two independent admin sources, both fail-closed:

1. **GitHub**: a repository ADMIN, or an active owner of the organization that
   owns the repository. This is read live from GitHub by the caller; this module
   only interprets the result.
2. **DSH team allowlist**: an owner-maintained file in the repository
   (``coordination/admin_team.json``) naming additional trusted logins (the
   "DSH team plugin" admins). Because it lives in the repository and only an
   owner/admin can merge changes to it, a contributor cannot add themselves by
   opening a pull request.

Everything else is a contributor (slave) role: it may claim tasks, edit within
the task scope, run its own AI/provider and open PRs. It may not approve tasks,
merge, change policy, or edit the allowlist. A contributor may always tune its
own local AI: that is local configuration, not project authority.
"""
from __future__ import annotations

import json
import pathlib

ADMIN = 'admin'
CONTRIBUTOR = 'contributor'
UNKNOWN = 'unknown'
TEAM_FILE = pathlib.Path('coordination/admin_team.json')


def team_allowlist(path: pathlib.Path = TEAM_FILE) -> set[str]:
    """Logins the owner added to the DSH team; empty on any bad/missing file."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    logins = data.get('admins')
    if not isinstance(logins, list):
        return set()
    return {str(login).strip() for login in logins
            if isinstance(login, str) and login.strip()}


def resolve(login: str, *, github_permission: str | None = None,
            org_owner: bool = False, team: set[str] | None = None) -> dict:
    """Classify one authenticated login. Unknown input is never admin."""
    login = str(login or '').strip()
    if not login:
        return {'login': '', 'role': UNKNOWN, 'source': 'none',
                'reason': 'no authenticated login'}
    if str(github_permission or '').upper() == 'ADMIN':
        return {'login': login, 'role': ADMIN, 'source': 'github',
                'reason': 'repository ADMIN'}
    if org_owner:
        return {'login': login, 'role': ADMIN, 'source': 'github-org',
                'reason': 'active organization owner'}
    if login.lower() in {name.lower() for name in (team or set())}:
        return {'login': login, 'role': ADMIN, 'source': 'dsh-team',
                'reason': 'listed in coordination/admin_team.json'}
    if str(github_permission or '').upper() in ('MAINTAIN', 'WRITE', 'TRIAGE', 'READ'):
        return {'login': login, 'role': CONTRIBUTOR, 'source': 'github',
                'reason': 'repository contributor permission'}
    return {'login': login, 'role': UNKNOWN, 'source': 'unknown',
            'reason': 'role could not be verified'}


def require_admin(*, login: str, github_permission: str | None = None,
                  org_owner: bool = False, team: set[str] | None = None) -> dict:
    """Raise PermissionError unless the live role is admin. Never defaults open."""
    role = resolve(login, github_permission=github_permission,
                   org_owner=org_owner, team=team)
    if role['role'] != ADMIN:
        raise PermissionError(f"{role['login'] or 'anonymous'}: owner/admin only "
                              f"({role['reason']})")
    return role
