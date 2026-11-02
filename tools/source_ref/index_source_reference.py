"""Index the pre-release source reference and prove it matches the Premium ELF.

The owner supplied `NOVA3_DEV_SOURCECODE.zip`: six 7z volumes containing a 2012
`NOVA3/trunk` + `NOVA3/extern` tree (engine `glitch`, driver `vox`, the Android
JNI port and the game code). This tool makes the reference *usable and checkable*
without committing a byte of it:

* counts files/bytes by extension and top directory;
* computes a deterministic manifest hash (paths + sizes) for the extracted tree;
* extracts source paths from the Premium ELF (DWARF line/string tables) and
  resolves them against the tree, so "this is the right source branch" is a
  measured claim rather than a filename guess;
* locates the classes/functions the reconstruction has already touched, which is
  the shortest path from a verified source file to an independently compiled
  object.

Output defaults to ``coordination/source_index.json`` (committed: metadata only,
no proprietary text). The tree itself stays under the ignored ``reference/``.

    python -m tools.source_ref.index_source_reference --write
    python -m tools.source_ref.index_source_reference --json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import posixpath
import re
import subprocess
import sys

SCHEMA = 'nova3-source-index/1'
DEFAULT_ROOT = pathlib.Path('reference/nova3_source')
DEFAULT_ELF = pathlib.Path('analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so')
DEFAULT_OUT = pathlib.Path('coordination/source_index.json')
SOURCE_SUFFIXES = {'.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.inl', '.ipp', '.inc',
                   '.java', '.cs', '.mm', '.m', '.glsl', '.fx', '.hlsl', '.cg'}
SYMBOLS = (
    'CSoftwareSkinTechnique', 'prepareCache', 'checkAvailability', 'needOutputBuffer',
    'DriverCallbackSourceInterface', 'DetectNeon', 'DriverCallbackInterface',
    'CMatrix4', 'getTransposed', 'getInverse', 'cpuFeatures', 'CNavigationMesh',
    'CVehicle', 'CPlayer', 'IrrlichtDevice',
)


def _iter_files(root: pathlib.Path):
    for current, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            yield pathlib.Path(current) / name


def tree_stats(root: pathlib.Path) -> dict:
    by_ext = collections.Counter()
    by_size = collections.Counter()
    top = collections.Counter()
    top_size = collections.Counter()
    digest = hashlib.sha256()
    files = 0
    total = 0
    for path in _iter_files(root):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files += 1
        total += size
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower() or '(none)'
        by_ext[ext] += 1
        by_size[ext] += size
        parts = rel.split('/')
        key = '/'.join(parts[:3]) if len(parts) >= 3 else rel
        top[key] += 1
        top_size[key] += size
        digest.update(f'{rel}\0{size}\n'.encode('utf-8', 'surrogateescape'))
    return {
        'files': files,
        'bytes': total,
        'manifest_sha256': digest.hexdigest(),
        'by_extension': {ext: {'files': by_ext[ext], 'bytes': by_size[ext]}
                         for ext in sorted(by_size, key=by_size.get, reverse=True)},
        'top_directories': [{'path': key, 'files': top[key], 'bytes': top_size[key]}
                            for key in sorted(top_size, key=top_size.get, reverse=True)[:25]],
    }


def elf_source_paths(elf: pathlib.Path) -> list[str]:
    """Source-looking paths in the ELF's strings (DWARF str/line tables live there)."""
    try:
        raw = subprocess.run(['strings', '-a', '-n', '8', str(elf)],
                             capture_output=True, timeout=120, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in raw.decode('utf-8', 'replace').splitlines():
        if '\\' not in line and '/' not in line:
            continue
        if not re.search(r'\.(?:c|cc|cpp|cxx|h|hpp|inl|ipp|inc|java|mm|m)$', line, re.I):
            continue
        found.append(line)
    return sorted(set(found))


def normalise_source_path(raw: str) -> str | None:
    """Map a Windows build path from DWARF onto the tree's relative layout."""
    text = raw.strip().replace('\\', '/')
    lowered = text.lower()
    for marker in ('/nova3data/', 'nova3data/'):
        at = lowered.find(marker)
        if at >= 0:
            text = text[at + len(marker):]
            break
    else:
        if not (text.startswith('trunk/') or text.startswith('extern/')
                or '/trunk/' in text or '/extern/' in text):
            return None
        # Strip any absolute prefix before the repository root.
        for marker in ('/trunk/', '/extern/'):
            at = text.find(marker)
            if at >= 0:
                text = text[at + 1:]
                break
    text = posixpath.normpath(text)
    return text if text and not text.startswith('..') else None


def cross_check(root: pathlib.Path, elf: pathlib.Path) -> dict:
    paths = elf_source_paths(elf)
    normalised = []
    for raw in paths:
        rel = normalise_source_path(raw)
        if rel:
            normalised.append((raw, rel))
    def exists(rel: str) -> bool:
        return (root / rel).is_file() or (root / 'NOVA3' / rel).is_file()

    present = [rel for _, rel in normalised if exists(rel)]
    missing = sorted({rel for _, rel in normalised if not exists(rel)})
    by_ext = collections.Counter(posixpath.splitext(rel)[1] for rel in
                                 (rel for _, rel in normalised))
    return {
        'elf': str(elf),
        'path_strings': len(paths),
        'resolved': len(normalised),
        'present_in_tree': len(present),
        'missing_in_tree': len(missing),
        'present_ratio': (len(present) / len(normalised)) if normalised else None,
        'by_extension': dict(by_ext.most_common()),
        'sample_present': sorted(set(present))[:12],
        'sample_missing': missing[:20],
    }


def locate_symbols(root: pathlib.Path, symbols: tuple[str, ...]) -> dict:
    """Find which extracted source files mention the already-touched symbols."""
    wanted = {symbol: [] for symbol in symbols}
    for path in _iter_files(root):
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            text = path.read_text('utf-8', 'replace')
        except OSError:
            continue
        for symbol in symbols:
            if symbol in text:
                rel = path.relative_to(root).as_posix()
                if len(wanted[symbol]) < 8:
                    wanted[symbol].append(rel)
    return {symbol: files for symbol, files in wanted.items() if files}


def build(root: pathlib.Path, elf: pathlib.Path) -> dict:
    stats = tree_stats(root)
    return {
        'schema': SCHEMA,
        'root': str(root),
        'reference_only': True,
        'stats': stats,
        'dwarf_cross_check': cross_check(root, elf) if elf.is_file() else None,
        'symbol_locations': locate_symbols(root, SYMBOLS),
        'notes': [
            'The pre-release tree is proprietary reference material: never commit it, '
            'never make it a production build input, never ship it.',
            'DWARF paths in the Premium ELF point at Nova3data\\trunk\\src and '
            'Nova3data\\extern; a high present ratio is evidence of the same branch, '
            'not proof of a byte-identical commit.',
            'Verify each recovered function against its source file and the ELF bytes '
            'before promoting a claim.',
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Index the pre-release source reference')
    parser.add_argument('--root', type=pathlib.Path, default=DEFAULT_ROOT)
    parser.add_argument('--elf', type=pathlib.Path, default=DEFAULT_ELF)
    parser.add_argument('--out', type=pathlib.Path, default=DEFAULT_OUT)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print(f'no source reference at {root}', file=sys.stderr)
        return 2
    payload = build(root, args.elf)
    if args.write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n',
                            encoding='utf-8')
        print(f'wrote {args.out}')
    if args.json or not args.write:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
