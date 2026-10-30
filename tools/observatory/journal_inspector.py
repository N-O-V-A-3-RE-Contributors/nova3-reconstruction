"""Bounded, redacted local journal inspector for the NOVA3 Control Center.

The inspector is deliberately narrow:

* only files directly inside the protected per-repository worker state
  directory (``~/.cache/nova3-team/<owner_repo>/.nova-private``);
* only a ``round-<n>.log`` file from a validated checkout directory;
* no symlinks, hardlinks, FIFOs, directories or files over the byte cap;
* no path or filename from the UI is ever passed to a shell.

Contributors may inspect only their own local logs (the directory belongs to
the current OS user). The TUI additionally gates this view to a freshly
rechecked repository ADMIN; no project-owner memory is readable here.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

MAX_FILES = 64
MAX_BYTES = 16384
CHECKOUT_RE = re.compile(r'([1-9][0-9]{0,8})-([A-Za-z0-9-]{1,80})')
ROUND_RE = re.compile(r'round-([1-9][0-9]{0,5})\.log')
REPO_RE = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
_SECRET_PATTERNS = (
    re.compile(r'(?i)\b(token|password|secret|api[_-]?key|authorization)\b\s*[:=]\s*[^\n]+'),
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}'),
    re.compile(r'\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{10,}\b'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{12,}\b'),
)
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def journal_base(repo: str, *, home: Path | None = None) -> Path:
    """Return the protected state directory for one validated repository."""
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo) or any(
            part in ('.', '..') for part in repo.split('/')):
        raise ValueError('invalid repository identifier')
    return (home or Path.home()) / '.cache/nova3-team' / repo.replace('/', '_') / '.nova-private'


def redact(text: str) -> str:
    """Mask assignment-shaped secrets; strip control characters."""
    clean = _CONTROL.sub(' ', str(text).replace('\r\n', '\n').replace('\r', '\n'))
    assignment, *other = _SECRET_PATTERNS
    clean = assignment.sub(lambda m: m.group(0).split('=')[0].split(':')[0].strip() + '=<redacted>', clean)
    for pattern in other:
        clean = pattern.sub('<redacted-secret>', clean)
    return clean


def _open_dir(path: Path) -> int:
    if not path.is_absolute():
        raise ValueError('unsafe directory')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def candidates(repo: str, *, home: Path | None = None) -> list[dict]:
    """List bounded round-log files without opening any of them.

    Sorted newest checkout/round first. A missing feed is an empty list, never a
    fabricated log or a home-wide scan.
    """
    result: list[dict] = []
    base = journal_base(repo, home=home)
    try:
        base_fd = _open_dir(base)
    except (OSError, ValueError):
        return result
    entries = 0
    try:
        with os.scandir(base_fd) as checkouts:
            for checkout in checkouts:
                entries += 1
                if entries > MAX_FILES:
                    break
                match = CHECKOUT_RE.fullmatch(checkout.name)
                if not match:
                    continue
                try:
                    child = os.open(checkout.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=base_fd)
                except OSError:
                    continue
                try:
                    with os.scandir(child) as files:
                        for entry in files:
                            entries += 1
                            if entries > MAX_FILES:
                                break
                            round_match = ROUND_RE.fullmatch(entry.name)
                            if not round_match:
                                continue
                            try:
                                info = entry.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                    or info.st_size > MAX_BYTES):
                                continue
                            result.append({
                                'checkout': f'{match.group(1)}-{match.group(2)}',
                                'round': int(round_match.group(1)),
                                'name': f'{match.group(1)}-{match.group(2)}/round-{round_match.group(1)}.log',
                                'bytes': info.st_size,
                                'mtime': info.st_mtime,
                            })
                finally:
                    os.close(child)
    finally:
        os.close(base_fd)
    result.sort(key=lambda item: (-item['mtime'], item['name']), reverse=False)
    result.sort(key=lambda item: item['mtime'], reverse=True)
    return result[:MAX_FILES]


def read(repo: str, name: str, *, home: Path | None = None,
         max_bytes: int = MAX_BYTES) -> dict:
    """Read exactly one validated round log, redacted, with a byte cap.

    ``name`` must be ``<checkout>/round-<n>.log``. Traversal, absolute paths,
    symlinks and hardlinks fail closed. The returned text is local-only.
    """
    max_bytes = max(1, min(int(max_bytes), MAX_BYTES))
    if not isinstance(name, str) or not name or len(name) > 200:
        raise ValueError('invalid journal name')
    parts = name.split('/')
    if len(parts) != 2 or not CHECKOUT_RE.fullmatch(parts[0]) or not ROUND_RE.fullmatch(parts[1]):
        raise ValueError('journal file is outside the protected round layout')
    base = journal_base(repo, home=home)
    base_fd = _open_dir(base)
    try:
        child = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
        try:
            fd = os.open(parts[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=child)
        finally:
            os.close(child)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('unsafe journal file')
            if info.st_size > MAX_BYTES:
                raise ValueError('journal file exceeds the inspector limit')
            payload = source.read(max_bytes + 1)
    finally:
        os.close(base_fd)
    if len(payload) > max_bytes:
        raise ValueError('journal file exceeds the inspector limit')
    text = payload.decode('utf-8', 'replace')
    return {'name': name, 'bytes': len(payload), 'text': redact(text),
            'truncated': len(payload) > max_bytes,
            'source': 'local protected worker state'}
