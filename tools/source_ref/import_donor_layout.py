"""Import donor function layout as metadata hints, never as reconstructed code.

Reads the user-supplied REvanguard3 TSV and local ELF files. All source/object
attribution is INFERRED even when the donor symbol matches exactly. Transfer to
Premium uses unique mangled names, NEVER donor addresses or behavioural claims.
No upstream scripts are executed and no original source/bytes are copied.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath

from nova3_team.indexer import build_ledger

SCHEMA = 'nova3-donor-layout/1'
DONOR_SHA256 = 'd6175cb6d1f1122ce4c5ba03088888f9750b5c9b484c7d73274d0badfa3e8167'


def has_code(text: str) -> bool:
    """True at the first token outside whitespace/comments; strings are code."""
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i + 2)
            i = len(text) if end < 0 else end + 1
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('unterminated block comment')
            i = end + 2
        else:
            return True
    return False


def relative_source(value: str) -> str | None:
    value = value.replace('\\', '/').strip()
    if not value or value.lower() in {'(unattributed)', '(unknown)', 'unknown', '?'}:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or '\x00' in value:
        raise ValueError('unsafe source attribution path')
    return path.as_posix()


def symbol_index(ledger: dict):
    index = defaultdict(list)
    for function in ledger['functions']:
        for symbol in function['symbols']:
            if symbol['name']:
                index[symbol['name']].append((function['id'], function['address'], symbol['size']))
    return {key: sorted(set(value)) for key, value in index.items()}


def validate_rows(rows, donor: dict, primary: dict) -> tuple[list, dict]:
    donor_symbols, primary_symbols = symbol_index(donor), symbol_index(primary)
    accepted, counts, seen = [], Counter(), set()
    for row in rows:
        counts['input_rows'] += 1
        try:
            address, size = int(row['addr'], 16), int(row['size'])
            if not 0 <= address < 2**32 or not 0 < size < 2**32:
                raise ValueError('range')
            name = row['mangled']
            source = relative_source(row.get('source', ''))
            # Keep object basenames only. Never publish a local absolute path.
            obj = relative_source(row.get('object', ''))
            if obj and '/' in obj:
                raise ValueError('object is not a basename')
        except (KeyError, TypeError, ValueError):
            counts['invalid_rows'] += 1
            continue
        matches = [item for item in donor_symbols.get(name, [])
                   if item[1:] == (address & ~1, size)]
        if len(matches) != 1:
            counts['donor_symbol_mismatch'] += 1
            continue
        identity = (address, name, size)
        if identity in seen:
            counts['duplicate_rows'] += 1
            continue
        seen.add(identity)
        destinations = primary_symbols.get(name, [])
        ids = sorted({item[0] for item in destinations})
        status = 'unique_mangled_name' if len(ids) == 1 else ('ambiguous' if ids else 'not_found')
        counts['primary_' + status] += 1
        accepted.append({'donor_function_id': matches[0][0], 'mangled': name,
            'source_hint': source, 'object_hint': obj,
            'attribution': 'INFERRED', 'donor_reported_confidence': row.get('confidence', ''),
            'primary_function_id': ids[0] if len(ids) == 1 else None,
            'transfer_evidence': status, 'state': 'INVENTORIED',
            'implementation_claim': False, 'behaviour_verified': False, 'byte_match': False})
    return accepted, dict(counts)


def audit(root: Path, primary_elf: Path) -> tuple[dict, dict]:
    donor_elf = root / 'libNOVA3_neon.so'
    data = donor_elf.read_bytes()
    if hashlib.sha256(data).hexdigest() != DONOR_SHA256:
        raise ValueError('donor ELF does not match the reviewed REvanguard3 identity')
    donor = build_ledger(data)
    primary = build_ledger(primary_elf.read_bytes())
    layout = root / 'src_layout'
    if not layout.is_dir():
        raise ValueError('missing src_layout directory')
    source_counts = Counter({'comment_only_files': 0, 'code_candidates': 0})
    for file in sorted(layout.rglob('*')):
        if file.suffix.lower() not in ('.c', '.cpp', '.h', '.hpp', '.cc', '.cxx'):
            continue
        if file.is_symlink():
            raise ValueError('symlinked source candidate requires manual review')
        source_counts['code_candidates' if has_code(file.read_text(encoding='utf-8'))
                      else 'comment_only_files'] += 1
    table = root / '_out/units/functions.tsv'
    with table.open(encoding='utf-8', newline='') as stream:
        rows, counts = validate_rows(csv.DictReader(stream, delimiter='\t'), donor, primary)
    summary = {'schema': SCHEMA, 'reference_only': True,
        'donor_sha256': donor['binary']['sha256'],
        'primary_sha256': primary['binary']['sha256'],
        'input_table_sha256': hashlib.sha256(table.read_bytes()).hexdigest(),
        'source_audit': dict(source_counts), 'validation': counts,
        'accepted_rows': len(rows), 'source_hints': len({r['source_hint'] for r in rows if r['source_hint']}),
        'implemented_functions_imported': 0, 'original_bytes_imported': 0,
        'notes': ['Source/object attribution remains INFERRED, not a recovered original object.',
                  'Unique mangled-name transfer is a research hint, not behavioural or byte equivalence.',
                  'Code candidates, if any, require separate provenance, licensing and build review.',
                  'No donor scripts, source comments, assembly, assets or executable binaries are imported.']}
    return dict(summary, functions=rows), summary


def lookup_primary(index: dict, primary_sha256: str, address: int) -> list[dict]:
    if index.get('schema') != SCHEMA or index.get('primary_sha256') != primary_sha256:
        raise ValueError('cached layout does not belong to this primary ELF')
    if not 0 <= address < 2**32:
        raise ValueError('address out of range')
    prefix = f'{primary_sha256}:{address & ~1:08x}:'
    return [row for row in index['functions']
            if (row.get('primary_function_id') or '').startswith(prefix)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--donor', type=Path)
    parser.add_argument('--primary', type=Path, required=True)
    parser.add_argument('--lookup-primary', type=lambda value: int(value, 0),
                        help='Query cached source hints by Premium virtual address; no re-import')
    parser.add_argument('--out', type=Path, default=Path('.cache/source_ref/revanguard3-layout.json'))
    parser.add_argument('--summary', type=Path)
    args = parser.parse_args(argv)
    if args.lookup_primary is not None:
        index = json.loads(args.out.read_text())
        digest = hashlib.sha256(args.primary.read_bytes()).hexdigest()
        print(json.dumps(lookup_primary(index, digest, args.lookup_primary), indent=2))
        return 0
    if args.donor is None:
        parser.error('--donor is required for import (not for cached lookup)')
    full, summary = audit(args.donor, args.primary)
    for path, value in ((args.out, full), (args.summary, summary)):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
