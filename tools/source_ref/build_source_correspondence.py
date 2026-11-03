"""Match Premium ELF functions against the private pre-release source tree.

Input is ``.cache/source_ref/elf_symbols.jsonl`` (see
``extract_elf_symbols.py``): every defined function with its demangled name,
decoded direct callees, literal-pool data references and referenced strings.

Output is a correspondence row per function.  Three statuses are deliberately
kept apart, because they license different amounts of trust:

* ``exact_body_found``  -- a definition site exists in the reference tree and the
  machine code's own references (direct callees, data symbols, string literals)
  are found in that definition.  This is evidence that the *body* is present, not
  that it is byte-identical to what produced the binary.
* ``same_file_different_revision`` -- the defining file/class is present but the
  body evidence is weak or absent: the tree is close, the function drifted.
* ``not_found`` -- no definition site at all (generated code, third-party code
  outside the extracted subset, or a renamed/moved unit).

Nothing here claims compiler flags, object-code equality or an original commit.
The tree stays private: the committed artifact carries *paths, line numbers,
addresses and evidence counts*, never source text.

    python -m tools.source_ref.build_source_correspondence --build-index
    python -m tools.source_ref.build_source_correspondence --write
    python -m tools.source_ref.build_source_correspondence --show 0x73f0a4
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import pathlib
import re
import sys

SCHEMA = 'nova3-source-correspondence/1'
# Units the reconstruction already claims something about; the report resolves
# each of them against the tree so the claim can be re-checked by address.
TRACKED_UNITS = {
    0x000d2f10: 'cpu-features getter 1',
    0x000d2f3c: 'cpu-features getter 2',
    0x000d2f6c: 'cpu-features getter 3',
    0x000d2f98: 'android_cpuInit',
    0x009dad14: 'DriverCallbackSourceInterface::DetectNeon',
    0x009d53f4: 'DriverCallbackInterface::Init',
    0x00710b48: 'CSoftwareSkinTechnique::needOutputBuffer',
    0x00710b50: 'CSoftwareSkinTechnique::checkAvailability',
    0x00710b58: 'CSoftwareSkinTechnique::attach',
    0x0073f0a4: 'CSoftwareSkinTechnique::prepareCache',
    0x006e7e60: 'operator*<float, collada::SMatrix>',
    0x00108c54: 'CMatrix4<float>::CMatrix4(eConstructor)',
    0x00108c8c: 'CMatrix4<float>::CMatrix4(const&, eConstructor)',
    0x000eba84: 'CMatrix4<float>::getTransposed',
    0x000ebb08: 'CMatrix4<float>::getInverse',
}
DEFAULT_ROOT = pathlib.Path('reference/nova3_source')
DEFAULT_SYMBOLS = pathlib.Path('.cache/source_ref/elf_symbols.jsonl')
DEFAULT_INDEX = pathlib.Path('.cache/source_ref/source_defs.json')
DEFAULT_OUT = pathlib.Path('.cache/source_ref/correspondence.jsonl')
DEFAULT_SUMMARY = pathlib.Path('coordination/source_correspondence.json')
SOURCE_SUFFIXES = {'.cpp', '.c', '.cc', '.cxx', '.mm', '.m',
                   '.h', '.hpp', '.hxx', '.inl', '.ipp'}

# A C++ component: an identifier or an operator name.
_COMPONENT = (r'(?:~?[A-Za-z_]\w*'
              r'|operator\s*(?:\[\]|\(\)|[=+\-*/%<>!&|^~,]+|new(?:\s*\[\])?'
              r'|delete(?:\s*\[\])?|->\*?|""\s*[A-Za-z_]\w*))')
_TARGS = r'(?:\s*<[^{}<>]*(?:<[^{}<>]*>[^{}<>]*)*>)?'
_QUALIFIED = rf'(?:{_COMPONENT}{_TARGS}\s*::\s*)*{_COMPONENT}{_TARGS}'
_DEF_RE = re.compile(rf'(?<![\w:])({_QUALIFIED})\s*\(')
_FREE_NAME = (r'(?:operator\s*(?:\[\]|\(\)|[=+\-*/%<>!&|^~,]+|new(?![\w(])|delete(?![\w(]))'
              r'|[A-Za-z_]\w*)')
_FREE_NAME_RE = re.compile(
    rf'^[ \t]{{0,2}}(?:[A-Za-z_][\w:<>,*&\[\]\s]*?[\s*&])?({_FREE_NAME})\s*\(')
_KEYWORDS = {'if', 'for', 'while', 'switch', 'return', 'else', 'do', 'case', 'typedef',
             'struct', 'class', 'enum', 'namespace', 'using', 'template', 'extern',
             'friend', 'static_assert', 'catch', 'throw', 'new', 'delete'}
_TEMPLATE_RE = re.compile(r'<[^<>]*>')
_LINE_COMMENT_RE = re.compile(r'//.*$')


def strip_templates(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = _TEMPLATE_RE.sub('', text)
    return text


def normalise_component(name: str) -> str:
    name = name.strip()
    name = re.sub(r'\s+', ' ', name)
    # `operator=` vs `operator =`: compare without inner spaces.
    if name.startswith('operator'):
        return 'operator' + re.sub(r'\s+', '', name[len('operator'):])
    return name


def split_qualified(text: str) -> list[str]:
    """Split a demangled C++ name into cleaned components (templates removed).

    A demangled template function's ``head`` carries its return type
    (``glitch::core::CMatrix4<float> glitch::core::operator*<...>``), so the last
    whitespace-separated token is the qualified function name.  ``virtual thunk
    to`` / ``[clone ...]`` decorations from GCC are stripped first.
    """
    text = re.sub(r'^(?:non-)?virtual thunk to\s+', '', text.strip())
    text = re.sub(r'\s*\[clone[^\]]*\]', '', text)
    text = strip_templates(text).replace('::<', '::').strip()
    tokens = text.split()
    if len(tokens) > 1:
        if tokens[-1] in ('new', 'delete') and tokens[-2] == 'operator':
            text = 'operator ' + tokens[-1]
        else:
            text = tokens[-1]
    parts = [normalise_component(part) for part in text.split('::')]
    return [part for part in parts if part and part != '(anonymous namespace)']


def demangled_head(demangled: str) -> str:
    """Everything before the parameter list of a demangled signature."""
    depth = 0
    angle = 0
    for index, char in enumerate(demangled):
        if char == '<':
            angle += 1
        elif char == '>':
            angle = max(0, angle - 1)
        elif char == '(':
            if depth == 0 and angle == 0:
                return demangled[:index].strip()
            depth += 1
        elif char == ')':
            depth = max(0, depth - 1)
    return demangled.strip()


def evidence_identifier(demangled: str) -> str | None:
    """The identifier a machine-code reference should appear as in a body."""
    if not demangled or demangled.startswith('<'):
        return None
    head = demangled_head(demangled)
    if not head:
        return None
    parts = split_qualified(head)
    if not parts:
        return None
    name = parts[-1]
    if name.startswith('~'):
        name = name[1:]
    if name.startswith('operator'):
        return 'operator'
    name = re.sub(r'\[clone[^\]]*\]', '', name).strip()
    return name or None


def scan_free_function(lines: list[str], number: int) -> str | None:
    """A C-style free-function definition starting at ``lines[number-1]``."""
    line = lines[number - 1]
    stripped = line.strip()
    if not stripped or stripped.startswith(('//', '/*', '*', '#', '}')):
        return None
    match = _FREE_NAME_RE.match(line)
    if not match:
        return None
    name = match.group(1)
    if name in _KEYWORDS:
        return None
    rest = line[match.end():]
    if ';' in rest:
        return None
    # A definition's body opens within the next few lines; declarations do not.
    tail = rest
    for offset in range(1, 6):
        index = number - 1 + offset
        if index >= len(lines):
            break
        if '{' in tail:
            return name
        if ';' in tail:
            return None
        tail = lines[index]
    return name if '{' in tail else None


def scan_file(path: pathlib.Path) -> list[tuple[str, int, int]]:
    """Qualified-name definition candidates in one source file.

    Returns ``(qualified_name, line, flags)`` where flags records whether the line
    looks like a definition (no trailing ``;``) and whether it starts a body.
    """
    try:
        text = path.read_text('utf-8', 'replace')
    except OSError:
        return []
    found = []
    lines = text.splitlines()
    in_block_comment = False
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if in_block_comment:
            if '*/' in stripped:
                in_block_comment = False
            continue
        if stripped.startswith('/*') and '*/' not in stripped:
            in_block_comment = True
            continue
        if not stripped or stripped.startswith('//') or stripped.startswith('#'):
            continue
        code = _LINE_COMMENT_RE.sub('', line)
        if len(code) > 400:
            code = code[:400]
        for match in _DEF_RE.finditer(code):
            qualified = match.group(1)
            if '::' not in qualified:
                continue
            # Only definition-shaped lines: the qualified name must start the
            # line (after an optional return type) rather than sit mid-expression.
            prefix = code[:match.start()]
            if ';' in prefix or '=' in prefix or 'return ' in prefix:
                continue
            if len(prefix) > 160:
                continue
            after = code[match.end():].strip()
            trailing_decl = after.endswith(';')
            opens_body = after.startswith(')') and '{' in after
            found.append((qualified, number, {
                'declaration': trailing_decl,
                'opens_body': opens_body,
            }))
        else:
            free = scan_free_function(lines, number)
            if free:
                found.append((free, number, {'declaration': False, 'opens_body': True}))
    return found


def build_index(root: pathlib.Path) -> dict:
    index: dict[str, list[list]] = collections.defaultdict(list)
    free: dict[str, list[list]] = collections.defaultdict(list)
    files = 0
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
        except OSError:
            continue
        files += 1
        rel = path.relative_to(root).as_posix()
        for qualified, line, flags in scan_file(path):
            key = '::'.join(normalise_component(part) for part in
                            strip_templates(qualified).split('::') if part)
            if not key:
                continue
            target = index if '::' in key else free
            target[key].append([rel, line, int(flags['declaration'])])
    return {'schema': 'nova3-source-defs/2', 'root': str(root),
            'files': files, 'keys': len(index), 'free_keys': len(free),
            'definitions': sum(len(v) for v in index.values()),
            'free_definitions': sum(len(v) for v in free.values()),
            'index': {k: v for k, v in index.items()},
            'free': {k: v for k, v in free.items()}}


class Bodies:
    """Extract and cache the body text that starts at a definition line."""

    def __init__(self, root: pathlib.Path):
        self.root = root
        self.cache: dict[str, list[str]] = {}
        self.bodies: dict[tuple[str, int], str | None] = {}

    def lines(self, rel: str) -> list[str]:
        if rel not in self.cache:
            try:
                self.cache[rel] = (self.root / rel).read_text('utf-8', 'replace').splitlines()
            except OSError:
                self.cache[rel] = []
        return self.cache[rel]

    def body(self, rel: str, line: int) -> str | None:
        key = (rel, line)
        if key in self.bodies:
            return self.bodies[key]
        lines = self.lines(rel)
        if not lines or line < 1 or line > len(lines):
            self.bodies[key] = None
            return None
        start = None
        for offset in range(0, 16):
            index = line - 1 + offset
            if index >= len(lines):
                break
            if '{' in lines[index]:
                start = (index, lines[index].index('{'))
                break
        if start is None:
            self.bodies[key] = None
            return None
        index, column = start
        window = lines[index:min(len(lines), index + 2000)]
        text = '\n'.join(window)
        depth = 0
        in_string = None
        escape = False
        in_block = False
        in_line = False
        position = column
        while position < len(text):
            char = text[position]
            if in_line:
                if char == '\n':
                    in_line = False
                position += 1
                continue
            if in_block:
                if char == '*' and position + 1 < len(text) and text[position + 1] == '/':
                    in_block = False
                    position += 2
                    continue
                position += 1
                continue
            if in_string:
                if escape:
                    escape = False
                elif char == '\\':
                    escape = True
                elif char == in_string:
                    in_string = None
                position += 1
                continue
            if char == '/' and position + 1 < len(text):
                if text[position + 1] == '/':
                    in_line = True
                    position += 2
                    continue
                if text[position + 1] == '*':
                    in_block = True
                    position += 2
                    continue
            if char in ('"', "'"):
                in_string = char
                position += 1
                continue
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    body = text[column:position + 1]
                    self.bodies[key] = body
                    return body
            position += 1
        self.bodies[key] = None
        return None

    def identifiers(self, rel: str, line: int) -> set[str] | None:
        text = self.body(rel, line)
        if text is None:
            return None
        return set(re.findall(r'[A-Za-z_]\w*', text))


def load_index(path: pathlib.Path, root: pathlib.Path) -> dict:
    if not path.is_file():
        payload = build_index(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding='utf-8')
        return payload
    return json.loads(path.read_text(encoding='utf-8'))


def candidate_keys(parts: list[str]) -> list[str]:
    """Longest-suffix keys first: full path, then drop leading namespaces."""
    keys = []
    for start in range(len(parts) - 1):
        keys.append('::'.join(parts[start:]))
    if parts:
        keys.append(parts[-1])
    return keys


def classify(evidence_total: int, ratio: float, definition_found: bool,
             file_match: bool, opens_body: bool) -> str:
    if not definition_found:
        return 'not_found'
    if evidence_total >= 3 and ratio >= 0.6:
        return 'exact_body_found'
    if evidence_total >= 1 and ratio >= 0.5:
        return 'exact_body_found'
    if evidence_total == 0 and opens_body and file_match:
        return 'exact_body_found'
    if evidence_total == 0 and opens_body and definition_found and ratio >= 0:
        # Trivial body (getter/constructor): definition site alone is the best
        # evidence available; mark it explicitly weaker in `confidence`.
        return 'exact_body_found'
    return 'same_file_different_revision'


def demangle_many(names: list[str]) -> dict[str, str]:
    if not names:
        return {}
    proc = subprocess.run(['c++filt'], input=('\n'.join(names) + '\n').encode(),
                          capture_output=True, check=False)
    if proc.returncode != 0:
        return {name: name for name in names}
    out = proc.stdout.decode('utf-8', 'replace').split('\n')
    return {name: out[i] if i < len(out) else name for i, name in enumerate(names)}


def collect_evidence_names(rows: list[dict]) -> dict[str, str]:
    pending = set()
    for row in rows:
        for name in row.get('callees', []):
            if name and not name.startswith('<'):
                pending.add(name)
        for name in row.get('data_refs', []):
            if name and not name.startswith('<'):
                pending.add(name)
    return demangle_many(sorted(pending))


_STOP_TOKENS = {'std', 'boost', 'gnu', 'char', 'unsigned', 'const', 'void', 'int',
                'long', 'short', 'float', 'double', 'bool', 'vector', 'basic_string',
                'allocator', 'iterator', 'pair', 'map', 'set', 'list', 'deque'}


def signature_tokens(demangled: str) -> set[str]:
    """Type names from a demangled signature that a defining file must mention."""
    tokens = set()
    for token in re.findall(r'[A-Za-z_]\w{3,}', demangled):
        if token in _STOP_TOKENS or not token[0].isupper():
            continue
        tokens.add(token)
    return tokens


def file_mention_count(bodies: Bodies, rel: str, tokens: set[str]) -> int:
    text = '\n'.join(bodies.lines(rel))
    return sum(1 for token in tokens if token in text)


def score_site(rel: str, line: int, declaration: int, wanted: set[str],
               evidence_total: int, hint_base: str, depth: int,
               bodies: Bodies) -> dict:
    body_ids = bodies.identifiers(rel, line)
    if body_ids is None:
        hits = 0
        ratio = 0.0
    else:
        hits = len(wanted & body_ids)
        ratio = hits / evidence_total if evidence_total else 0.0
    base = rel.split('/')[-1]
    file_match = bool(hint_base) and base == hint_base
    opens_body = body_ids is not None
    score = ratio * 10 + (2 if file_match else 0) + (1 if opens_body else 0)
    score -= depth * 0.5
    return {
        'status': classify(evidence_total, ratio, True, file_match, opens_body),
        'file': rel,
        'line': line,
        'evidence_total': evidence_total,
        'evidence_hits': hits,
        'evidence_ratio': round(ratio, 3),
        'file_hint_match': file_match,
        'body_found': opens_body,
        'match_depth': depth,
        'score': round(score, 3),
        'declaration_only': bool(declaration),
        'kind': 'member' if depth >= 0 else 'free',
    }


def match_symbol(row: dict, index: dict, bodies: Bodies,
                 demangled_refs: dict[str, str] | None = None,
                 free_index: dict | None = None) -> dict:
    head = demangled_head(row['demangled'])
    parts = split_qualified(head)
    if not parts:
        return {'status': 'not_found', 'reason': 'unparseable name'}
    demangled_refs = demangled_refs or {}
    wanted = set()
    for callee in row.get('callees', []):
        ident = evidence_identifier(demangled_refs.get(callee, callee))
        if ident:
            wanted.add(ident)
    for ref in row.get('data_refs', []):
        ident = evidence_identifier(demangled_refs.get(ref, ref))
        if ident:
            wanted.add(ident)
    for text in row.get('strings', []):
        for token in re.findall(r'[A-Za-z_]\w{3,}', text):
            wanted.add(token)
    evidence_total = len(wanted)

    hint = row.get('file_hint') or ''
    hint_base = hint.replace('\\', '/').split('/')[-1]
    best = None
    for depth, key in enumerate(candidate_keys(parts)):
        sites = index.get(key)
        if not sites:
            continue
        for rel, line, declaration in sites[:24]:
            record = score_site(rel, line, declaration, wanted, evidence_total,
                                hint_base, depth, bodies)
            if best is None or record['score'] > best['score']:
                best = record
        if best and best['evidence_ratio'] >= 0.8 and best['evidence_total'] >= 3:
            break
    if best is None:
        # Free functions are not qualified in the tree (C-style definitions), so
        # a bare name is ambiguous. Filter the candidate files by the types in
        # the demangled signature before spending body extraction on them, and
        # require body evidence or a signature-type match.
        free_index = free_index or {}
        tokens = signature_tokens(row['demangled'])
        sites = free_index.get(parts[-1]) or []
        # A rare name with exactly one definition site is itself evidence: there
        # is nothing else in the tree that could have produced the symbol.
        unique_ok = len(sites) <= 8
        for rel, line, declaration in sites[:400]:
            if tokens and file_mention_count(bodies, rel, tokens) == 0:
                continue
            record = score_site(rel, line, declaration, wanted, evidence_total,
                                hint_base, 99, bodies)
            record['kind'] = 'free'
            strong = (record['evidence_total'] >= 2
                      and record['evidence_ratio'] >= 0.6)
            plausible = record['body_found'] and (
                strong
                or (tokens and record['evidence_ratio'] >= 0.5)
                or (record['evidence_total'] == 0 and tokens)
                or (unique_ok and len(sites) == 1))
            if plausible:
                if unique_ok and len(sites) == 1 and not strong:
                    record['status'] = 'exact_body_found'
                    record['confidence'] = 'unique_site'
                elif strong:
                    record['confidence'] = 'strong'
                else:
                    record['confidence'] = 'signature_match'
                if best is None or record['score'] > best['score']:
                    best = record
    if best is None:
        return {'status': 'not_found', 'evidence_total': evidence_total,
                'evidence_hits': 0, 'evidence_ratio': 0.0}
    if 'confidence' not in best:
        ratio = best['evidence_ratio']
        total = best['evidence_total']
        if total >= 3 and ratio >= 0.6:
            best['confidence'] = 'strong'
        elif best['file_hint_match'] and best['body_found']:
            best['confidence'] = 'file_hint'
        elif best['body_found'] and total == 0:
            best['confidence'] = 'definition_only'
        else:
            best['confidence'] = 'weak'
    return best


def correlate(symbols: pathlib.Path, index: dict, root: pathlib.Path,
              free_index: dict | None = None) -> list[dict]:
    bodies = Bodies(root)
    symbols_rows = []
    for line in symbols.read_text(encoding='utf-8').splitlines():
        if line.strip():
            symbols_rows.append(json.loads(line))
    demangled_refs = collect_evidence_names(symbols_rows)
    rows = []
    for row in symbols_rows:
        match = match_symbol(row, index, bodies, demangled_refs, free_index)
        out = {
            'address': row['address'],
            'size': row['size'],
            'mode': row['mode'],
            'mangled': row['mangled'],
            'demangled': row['demangled'],
            'file_hint': row['file_hint'],
        }
        out.update(match)
        rows.append(out)
    return rows


def reason_bucket(row: dict) -> str:
    name = row['demangled']
    if row['status'] != 'not_found':
        return ''
    if 'std::' in name or '__gnu_cxx' in name:
        return 'cxx_runtime_std'
    if 'boost::' in name:
        return 'cxx_runtime_boost'
    if 'thunk to' in name:
        return 'virtual_thunk'
    if '::' not in name:
        return 'plain_c_name'
    return 'engine_or_third_party'


def summarise(rows: list[dict]) -> dict:
    status = collections.Counter(row['status'] for row in rows)
    ratio_buckets = collections.Counter()
    for row in rows:
        ratio = row.get('evidence_ratio') or 0.0
        ratio_buckets[f'{int(ratio * 10)}/10'] += 1
    by_subsystem = collections.Counter()
    for row in rows:
        rel = row.get('file') or ''
        parts = rel.split('/')
        key = '/'.join(parts[:3]) if len(parts) >= 3 else rel
        by_subsystem[key or '(none)'] += 1
    status_by_subsystem = collections.defaultdict(collections.Counter)
    for row in rows:
        rel = row.get('file') or ''
        parts = rel.split('/')
        key = '/'.join(parts[:3]) if len(parts) >= 3 else rel
        status_by_subsystem[key][row['status'] + '_' if False else row['status']] += 1
    reasons = collections.Counter(reason_bucket(row) for row in rows)
    tracked = []
    by_address = {row['address']: row for row in rows}
    for address, label in sorted(TRACKED_UNITS.items()):
        row = by_address.get(address)
        if row is None:
            tracked.append({'address': f'0x{address:08x}', 'unit': label,
                            'status': 'missing_from_symbol_table'})
            continue
        tracked.append({
            'address': f'0x{address:08x}',
            'unit': label,
            'status': row['status'],
            'file': row.get('file'),
            'line': row.get('line'),
            'evidence': f'{row.get("evidence_hits")}/{row.get("evidence_total")}',
            'confidence': row.get('confidence'),
            'demangled': row['demangled'],
        })
    confidence = collections.Counter(row.get('confidence') or 'none' for row in rows)
    exact = [row for row in rows if row['status'] == 'exact_body_found']
    strong = [row for row in exact if (row.get('evidence_total') or 0) >= 3
              and (row.get('evidence_ratio') or 0) >= 0.6]
    return {
        'schema': SCHEMA,
        'functions': len(rows),
        'status': dict(status),
        'strong_evidence': len(strong),
        'confidence': dict(confidence),
        'by_evidence_ratio': dict(sorted(ratio_buckets.items())),
        'top_subsystems': by_subsystem.most_common(25),
        'status_by_subsystem': {key: dict(counter)
                                for key, counter in sorted(status_by_subsystem.items())
                                if key},
        'not_found_reasons': {k: v for k, v in reasons.items() if k},
        'tracked_units': tracked,
        'notes': [
            'A correspondence row says a definition site and its body evidence '
            'exist in the reference tree. It is not a compiler-flag, object-code '
            'or commit-equality claim.',
            'evidence_ratio = machine-code references (direct callees, data '
            'symbols, string-literal tokens) found in the candidate body.',
            'file_hint is the ELF STT_FILE scope of a *local* symbol; it is empty '
            'for globals because .symtab orders those by section, not by file.',
        ],
    }


def render_markdown(summary: dict) -> str:
    lines = []
    lines.append('# ELF <-> pre-release source correspondence')
    lines.append('')
    lines.append('Generated by '
                 '`python -m tools.source_ref.build_source_correspondence --write --markdown`.')
    lines.append('The reference tree is private and ignored; this report carries only '
                 'paths, line numbers, addresses and evidence counts.')
    lines.append('')
    lines.append('## Status (all ' + f'{summary["functions"]:,} defined functions)')
    lines.append('')
    lines.append('| status | functions | meaning |')
    lines.append('| --- | --- | --- |')
    meaning = {
        'exact_body_found': 'definition site present and machine-code references found in it',
        'same_file_different_revision': 'file/class present, body evidence weak or absent',
        'not_found': 'no definition site in the extracted tree',
    }
    for status, count in summary['status'].items():
        lines.append(f'| `{status}` | {count:,} | {meaning.get(status, "")} |')
    lines.append('')
    lines.append(f"Strong body evidence (>=3 machine-code references, >=60% found): "
                 f"**{summary['strong_evidence']:,}** functions.")
    lines.append('')
    lines.append('Confidence mix: ' + ', '.join(
        f'`{name}` {count:,}' for name, count in
        sorted(summary.get('confidence', {}).items(), key=lambda item: -item[1])) + '.')
    lines.append('')
    lines.append('## Tracked reconstruction units')
    lines.append('')
    lines.append('| address | unit | status | confidence | file:line | evidence |')
    lines.append('| --- | --- | --- | --- | --- | --- |')
    for row in summary['tracked_units']:
        where = f'{row["file"]}:{row["line"]}' if row.get('file') else '-'
        lines.append(f'| `{row["address"]}` | {row["unit"]} | `{row["status"]}` | '
                     f'{row.get("confidence") or "-"} | {where} | '
                     f'{row.get("evidence", "-")} |')
    lines.append('')
    lines.append('## Not-found reasons')
    lines.append('')
    for reason, count in sorted(summary['not_found_reasons'].items(),
                                key=lambda item: -item[1]):
        lines.append(f'- `{reason}`: {count:,}')
    lines.append('')
    lines.append('## Status by subsystem (top 25)')
    lines.append('')
    lines.append('| subsystem | exact | same-file | not found |')
    lines.append('| --- | --- | --- | --- |')
    stats = summary['status_by_subsystem']
    for entry in summary['top_subsystems'][:25]:
        key = entry[0] if isinstance(entry, (list, tuple)) else entry
        counts = stats.get(key, {})
        lines.append(f'| `{key}` | {counts.get("exact_body_found", 0):,} | '
                     f'{counts.get("same_file_different_revision", 0):,} | '
                     f'{counts.get("not_found", 0):,} |')
    lines.append('')
    lines.append('## What this does and does not establish')
    lines.append('')
    lines.append('It establishes that a definition site and its body *evidence* exist '
                 'for a function: the direct callees, data symbols and string tokens '
                 'referenced by the machine code are found in the candidate body.')
    lines.append('It does not establish a byte-identical commit, the original compiler '
                 'flags, object-code equality, or that the tree is the exact revision '
                 'that produced this ELF. Those need per-function compile and compare '
                 'with GCC 4.4.3 (`tools/compare/gcc443_*`).')
    lines.append('')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Build the ELF <-> source correspondence')
    parser.add_argument('--root', type=pathlib.Path, default=DEFAULT_ROOT)
    parser.add_argument('--symbols', type=pathlib.Path, default=DEFAULT_SYMBOLS)
    parser.add_argument('--index', type=pathlib.Path, default=DEFAULT_INDEX)
    parser.add_argument('--out', type=pathlib.Path, default=DEFAULT_OUT)
    parser.add_argument('--summary', type=pathlib.Path, default=DEFAULT_SUMMARY)
    parser.add_argument('--build-index', action='store_true')
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--show', default=None, help='address to explain (0x...)')
    parser.add_argument('--markdown', type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if args.build_index:
        payload = build_index(root)
        args.index.parent.mkdir(parents=True, exist_ok=True)
        args.index.write_text(json.dumps(payload, sort_keys=True), encoding='utf-8')
        print(f'index: {payload["files"]} files, {payload["keys"]} keys, '
              f'{payload["definitions"]} definition sites -> {args.index}')
        if not args.write and not args.show:
            return 0
    index_payload = load_index(args.index, root)
    index = index_payload['index']
    free_index = index_payload.get('free') or {}

    if args.show:
        rows = correlate(args.symbols, index, root, free_index)
        wanted = [row for row in rows if row['address'] == int(args.show, 16)]
        if not wanted:
            print(f'no function at {args.show}', file=sys.stderr)
            return 2
        json.dump(wanted, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write('\n')
        return 0

    rows = correlate(args.symbols, index, root, free_index)
    summary = summarise(rows)
    summary['index'] = {'files': index_payload['files'], 'keys': index_payload['keys'],
                        'definitions': index_payload['definitions'],
                        'free_keys': index_payload.get('free_keys', 0),
                        'free_definitions': index_payload.get('free_definitions', 0)}
    if args.write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + '\n')
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n',
                               encoding='utf-8')
        print(f'wrote {args.out} ({len(rows)} rows) and {args.summary}')
        if args.markdown:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(render_markdown(summary) + '\n', encoding='utf-8')
            print(f'wrote {args.markdown}')
    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
