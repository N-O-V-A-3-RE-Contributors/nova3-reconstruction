"""Git changed-*file* inventory for scope validation; no directory collapsing.

Never enumerates ignored untracked game dumps, never follows symlinks, and
fails closed on deletions, malformed status or paths outside the checkout.
"""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import subprocess


class UnsafeChange(ValueError):
    pass


def parse_porcelain(raw: bytes) -> list[tuple[str, str]]:
    """Parse Git porcelain v1 -z --untracked-files=all. Detect rename secondary paths."""
    if not isinstance(raw, bytes):
        raise TypeError('Git porcelain output must be bytes')
    if not raw:
        return []
    if not raw.endswith(b'\0'):
        raise UnsafeChange('Truncated git status output')
    records = raw[:-1].split(b'\0')
    result = []
    i = 0
    while i < len(records):
        item = records[i]
        i += 1
        if len(item) < 4 or item[2:3] != b' ':
            raise UnsafeChange('Unexpected git status encoding')
        status = item[:2].decode('ascii', errors='replace')
        path = item[3:].decode('utf-8', errors='surrogateescape')
        if status == '!!':
            continue
        # Python invoked by the worker can leave bytecode caches in older
        # saved checkouts without a suitable .gitignore. Ignore ONLY untracked
        # interpreter caches (not staged/tracked files). They are never staged.
        if status == '??' and (path.endswith(('.pyc','.pyo')) or
               '__pycache__' in PurePosixPath(path).parts or
               '.pytest_cache' in PurePosixPath(path).parts):
            continue
        if 'D' in status or 'U' in status or status in ('AA','DD','AU','UA','DU','UD'):
            raise UnsafeChange(f'Deletion/conflict requires manual review: {path}')
        result.append((status,path))
        if 'R' in status or 'C' in status:
            if i >= len(records):
                raise UnsafeChange('Missing previous path for rename/copy')
            prior = records[i].decode('utf-8', errors='surrogateescape')
            i += 1
            result.append((status,prior))
    return result


def changed_files(work: str | Path) -> list[str]:
    root = Path(work).resolve(strict=True)
    cp = subprocess.run(
        ['git', '-C', str(root), 'status', '--porcelain=v1', '-z', '--untracked-files=all'],
        capture_output=True, check=True)
    results = set()
    for status, value in parse_porcelain(cp.stdout):
        if '\\' in value or '\0' in value:
            raise UnsafeChange(f'Unsupported filename encoding: {value!r}')
        p = PurePosixPath(value)
        if not value or p.is_absolute() or '..' in p.parts or p.as_posix() != value:
            raise UnsafeChange(f'Unsafe changed-file path: {value!r}')
        # A symlink in any parent can route ostensibly in-repo paths outside.
        full = root
        for part in p.parts:
            full = full / part
            if full.is_symlink():
                raise UnsafeChange(f'Symlink in modified file path: {value}')
        if not full.is_file():
            raise UnsafeChange(f'Changed path is not a regular file: {value}')
        results.add(value)
    return sorted(results)
