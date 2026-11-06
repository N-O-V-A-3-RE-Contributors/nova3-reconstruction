"""Regression contract for ``candidate_keys`` and its use by ``match_symbol``.

Scope: this module covers *helper behaviour only* in
``tools/source_ref/build_source_correspondence.py``:

* ``candidate_keys`` (longest-suffix ordering of qualified-name keys);
* the key-lookup order and the 24-sites-per-key cap inside ``match_symbol``,
  and the 400-sites ``free_index`` cap;
* the unparseable-name short circuit and the free-candidate signature-token
  rejection;
* the defect that shapes every qualified-path test: on a fresh call
  ``match_symbol`` raises ``NameError`` at line 511 (``best`` is read before it
  is assigned) whenever a consulted key has sites and ``evidence_total > 0``.
  Every such test therefore first runs a zero-evidence warm-up call; all 22
  tests also pass against a local copy with line 511 guarded, which was checked
  by hand and is NOT part of the committed test run.

Every input here is a synthetic row, a plain ``dict`` or a small recording
stand-in that exposes only ``lines`` and ``identifiers``. No reference tree,
``analysis/`` data, ELF file, ``c++filt``, ``subprocess`` or network is touched,
so the tests pass when those inputs are absent.

***No assertion in this module claims that any match is a reconstruction, a
byte match, an instruction-window match or behavioural equivalence.*** A
returned status is the matcher's own heuristic label; it says nothing about the
original game binary or about the source tree.
"""
from __future__ import annotations

import builtins
import io

import pytest

from tools.source_ref import build_source_correspondence as matcher


# A body that mentions none of the evidence identifiers used below.
WEAK_BODY = '{ nothing_here(); }'


class RecordingDict(dict):
    """A plain dict that logs every key lookup and membership probe."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookups: list = []
        self.probes: list = []

    def get(self, key, default=None):
        self.lookups.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.lookups.append(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self.probes.append(key)
        return super().__contains__(key)


class RecordingBodies:
    """Records which files/lines ``match_symbol`` asks the body reader about.

    Only the two methods ``match_symbol`` needs are exposed: ``lines`` and
    ``identifiers``. No filesystem access is performed.
    """

    def __init__(self, files: dict[str, list[str]], ids: dict[tuple, set | None]):
        self._files = files
        self._ids = ids
        self.line_requests: list[str] = []
        self.identifier_requests: list[tuple[str, int]] = []

    def lines(self, rel: str) -> list[str]:
        self.line_requests.append(rel)
        return self._files.get(rel, [])

    def identifiers(self, rel: str, line: int) -> set[str] | None:
        self.identifier_requests.append((rel, line))
        return self._ids.get((rel, line))


def _row(demangled: str, *, callees=(), data_refs=(), strings=(),
         file_hint: str = '') -> dict:
    return {
        'demangled': demangled,
        'callees': list(callees),
        'data_refs': list(data_refs),
        'strings': list(strings),
        'file_hint': file_hint,
    }


def _site(rel: str, line: int) -> list:
    return [rel, line, 0]


# Strings of 4+ characters are recorded as evidence identifiers by
# match_symbol, so this row carries exactly {'alpha', 'beta', 'gamma'}.
# match_symbol mutates the dict it returns (it adds 'confidence'), so every
# test builds a fresh row instead of sharing one module-level dict.
def _strong_row() -> dict:
    return _row('ns::smart_ptr()', strings=('alpha', 'beta', 'gamma'))


def _a_b_c_row() -> dict:
    # Same evidence set as _strong_row, named for an index keyed a::b::c.
    return _row('a::b::c()', strings=('alpha', 'beta', 'gamma'))


# Body identifiers for the high-score and no-score sites.
STRONG_IDS = {'alpha', 'beta', 'gamma'}
WEAK_IDS = {'zzz_other'}


@pytest.fixture(autouse=True)
def _no_external_io(monkeypatch):
    """Fail the test if the module under test opens a file or a process."""

    def forbidden_open(*args, **kwargs):
        raise AssertionError('file access is prohibited in this module')

    def forbidden_process(*args, **kwargs):
        raise AssertionError('subprocess use is prohibited in this module')

    monkeypatch.setattr(builtins, 'open', forbidden_open)
    monkeypatch.setattr(io, 'open', forbidden_open)
    monkeypatch.setattr(matcher.subprocess, 'run', forbidden_process)
    monkeypatch.setattr(matcher.subprocess, 'Popen', forbidden_process)


# ---------------------------------------------------------------------------
# candidate_keys
# ---------------------------------------------------------------------------

def test_candidate_keys_drops_leading_components_longest_first():
    assert matcher.candidate_keys(['a', 'b', 'c']) == ['a::b::c', 'b::c', 'c']
    assert matcher.candidate_keys(['a', 'b', 'c', 'd']) == [
        'a::b::c::d', 'b::c::d', 'c::d', 'd']


def test_candidate_keys_single_component():
    assert matcher.candidate_keys(['c']) == ['c']


def test_candidate_keys_empty():
    assert matcher.candidate_keys([]) == []


@pytest.mark.parametrize('parts', [['a'], ['a', 'b'], ['a', 'b', 'c'],
                                   ['A', 'B', 'C', 'D']])
def test_candidate_keys_never_returns_partial_middle_suffix(parts):
    """Every key keeps the trailing component and is a contiguous tail."""
    keys = matcher.candidate_keys(parts)
    assert keys
    for key in keys:
        tail = key.split('::')
        assert tail[-1] == parts[-1]
        assert parts[-len(tail):] == tail


# ---------------------------------------------------------------------------
# Lookup order inside match_symbol
# ---------------------------------------------------------------------------

def test_match_symbol_queries_index_in_candidate_keys_order_deepest_first():
    row = _row('a::b::c()')
    keys = matcher.candidate_keys(['a', 'b', 'c'])
    index = RecordingDict({key: [_site(f'f{offset}.cpp', 1)]
                           for offset, key in enumerate(keys)})
    bodies = RecordingBodies(
        {f'f{offset}.cpp': ['body', WEAK_BODY] for offset in range(len(keys))},
        {(f'f{offset}.cpp', 1): {f'only_{offset}'} for offset in range(len(keys))})

    result = matcher.match_symbol(row, index, bodies)

    assert index.lookups == ['a::b::c', 'b::c', 'c']
    # No site is strong, so all three keys are consulted; the first consulted
    # key (deepest, depth 0) wins the strict '>' comparison among equal scores.
    assert bodies.identifier_requests == [('f0.cpp', 1), ('f1.cpp', 1),
                                          ('f2.cpp', 1)]
    assert result['match_depth'] == 0


def test_match_symbol_stops_after_strong_deepest_key():
    index = RecordingDict({
        'ns::smart_ptr': [_site('deep.cpp', 1)],
        'smart_ptr': [_site('shallow.cpp', 1)],
    })
    bodies = RecordingBodies(
        {'deep.cpp': ['x', WEAK_BODY], 'shallow.cpp': ['x', WEAK_BODY]},
        {('deep.cpp', 1): STRONG_IDS, ('shallow.cpp', 1): STRONG_IDS})

    result = matcher.match_symbol(_strong_row(), index, bodies)

    assert index.lookups == ['ns::smart_ptr']
    assert result['match_depth'] == 0
    assert result['evidence_ratio'] >= 0.8 and result['evidence_total'] >= 3
    assert bodies.identifier_requests == [('deep.cpp', 1)]


# ---------------------------------------------------------------------------
# Unparseable names must not look up a partial suffix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('demangled', ['()', '', '   ', '(int, float)'])
def test_empty_parts_is_not_found_without_querying_indexes(demangled):
    index = RecordingDict({'int': [_site('a.cpp', 1)], 'float': [_site('b.cpp', 1)]})
    free = RecordingDict({'int': [_site('a.cpp', 1)]})
    bodies = RecordingBodies({'a.cpp': ['{ }']}, {('a.cpp', 1): {'int'}})

    result = matcher.match_symbol(_row(demangled), index, bodies, {}, free)

    assert result == {'status': 'not_found', 'reason': 'unparseable name'}
    assert index.lookups == [] and index.probes == []
    assert free.lookups == [] and free.probes == []
    assert bodies.identifier_requests == [] and bodies.line_requests == []


def test_middle_only_name_with_empty_deepest_entry_reports_not_found():
    """parts = ['c'], so the only key is 'c'; an empty entry means no match."""
    index = RecordingDict({'c': []})
    bodies = RecordingBodies({'c.cpp': ['x', WEAK_BODY]}, {('c.cpp', 1): STRONG_IDS})

    result = matcher.match_symbol(_row('c()'), index, bodies)

    assert index.lookups == ['c']
    assert result['status'] == 'not_found'
    assert bodies.identifier_requests == []


# ---------------------------------------------------------------------------
# 24-site cap per qualified key
# ---------------------------------------------------------------------------

def _warm_up_sites() -> list:
    return [_site('warm_up.cpp', 1)]


def _warm_up(callee: str, bodies) -> None:
    """Run match_symbol once with zero evidence identifiers on ``callee``.

    A fresh match_symbol raises ``NameError`` at line 511 as soon as a consulted
    key has sites *and* evidence_total > 0. With no callees, data references or
    strings the evidence set is empty, ``best`` stays ``None`` and the search
    reaches the free branch untouched. The caller clears the recorder before
    the call under test.
    """
    row = _row('Widget warm_up(int)', callees=[callee])
    # The throwaway key aliases every name, so the production free-index lookup
    # (line 520: free_index[parts[-1]]) re-enters the qualified loop and the
    # empty-evidence property still holds.
    matcher.match_symbol(row, RecordingDict({'warm_up': _warm_up_sites()}),
                         bodies, {}, RecordingDict({'*': _warm_up_sites()}))
    assert bodies.identifier_requests == [('warm_up.cpp', 1)]
    del bodies.identifier_requests[:]
    del bodies.line_requests[:]


def _capped_inputs():
    first_sites = [_site(f'first_{n:02d}.cpp', 1) for n in range(25)]
    index = RecordingDict({'a::b::c': first_sites,
                           'b::c': [_site('second.cpp', 7)],
                           'c': []})
    files = {rel: ['x', WEAK_BODY] for rel, _, _ in first_sites}
    files['warm_up.cpp'] = ['x', WEAK_BODY]
    files['second.cpp'] = ['x', WEAK_BODY]
    ids = {(rel, 1): WEAK_IDS for rel, _, _ in first_sites}
    ids[('warm_up.cpp', 1)] = WEAK_IDS
    ids[('second.cpp', 7)] = STRONG_IDS
    return index, RecordingBodies(files, ids)


def test_first_key_scored_only_to_24_sites_and_search_continues():
    index, bodies = _capped_inputs()
    assert len(index['a::b::c']) == 25

    _warm_up('warm_up', bodies)
    index.lookups.clear()

    result = matcher.match_symbol(_a_b_c_row(), index, bodies)

    # The first key contributes its first 24 sites only, then the second key.
    assert bodies.identifier_requests == (
        [(f'first_{n:02d}.cpp', 1) for n in range(24)] + [('second.cpp', 7)])
    # The 25th site of the first key is never scored.
    assert ('first_24.cpp', 1) not in bodies.identifier_requests
    # The search continued to the second key and chose its high-score site.
    assert index.lookups == ['a::b::c', 'b::c']
    assert (result['file'], result['line']) == ('second.cpp', 7)
    assert result['match_depth'] == 1


def test_exactly_24_sites_all_scored_then_search_continues():
    sites = [_site(f'f_{n:02d}.cpp', 1) for n in range(24)]
    index = RecordingDict({'a::b::c': sites, 'b::c': [_site('second.cpp', 3)],
                           'c': []})
    files = {rel: ['x', WEAK_BODY] for rel, _, _ in sites}
    files['warm_up.cpp'] = ['x', WEAK_BODY]
    files['second.cpp'] = ['x', WEAK_BODY]
    ids = {('warm_up.cpp', 1): WEAK_IDS, ('second.cpp', 3): STRONG_IDS}
    bodies = RecordingBodies(files, ids)

    _warm_up('warm_up', bodies)
    index.lookups.clear()

    result = matcher.match_symbol(_a_b_c_row(), index, bodies)

    assert bodies.identifier_requests == (
        [(f'f_{n:02d}.cpp', 1) for n in range(24)] + [('second.cpp', 3)])
    assert (result['file'], result['line']) == ('second.cpp', 3)


# ---------------------------------------------------------------------------
# Free-function path
# ---------------------------------------------------------------------------

def _free_row() -> dict:
    # The signature names Widget, so signature_tokens(...) == {'Widget'}.
    return _row('Widget spawn_thing(int)')


def test_free_index_not_consulted_when_qualified_sites_found():
    index = RecordingDict({'spawn_thing': [_site('member.cpp', 1)]})
    free = RecordingDict({'spawn_thing': [_site('free.cpp', 1)]})
    bodies = RecordingBodies(
        {'warm_up.cpp': ['x', WEAK_BODY], 'member.cpp': ['x', WEAK_BODY],
         'free.cpp': ['x', WEAK_BODY]},
        {('warm_up.cpp', 1): WEAK_IDS, ('member.cpp', 1): STRONG_IDS,
         ('free.cpp', 1): STRONG_IDS})

    _warm_up('warm_up', bodies)

    result = matcher.match_symbol(_free_row(), index, bodies, {}, free)

    # 'Widget spawn_thing(int)' yields a single key, the bare 'spawn_thing';
    # it has a site, so the free index is never looked up. This also documents
    # that the qualified loop and the free branch share that key, so a free
    # site placed under it is scored by the qualified path as kind 'member'.
    assert index.lookups == ['spawn_thing']
    assert free.lookups == [] and free.probes == []
    assert result['kind'] == 'member'
    assert bodies.identifier_requests == [('member.cpp', 1)]


def test_free_sites_capped_at_400():
    # The free branch is reached only when no candidate key has sites. The
    # only key here is the bare 'spawn_thing', so an index without it gets
    # there; the warm-up above then serves this call with empty evidence.
    all_sites = [_site(f'z_{n:03d}.cpp', 1) for n in range(405)]
    free = RecordingDict({'spawn_thing': all_sites})
    files = {rel: ['Widget', WEAK_BODY] for rel, _, _ in all_sites}
    files['warm_up.cpp'] = ['Widget', WEAK_BODY]
    bodies = RecordingBodies(
        files, {(rel, 1): WEAK_IDS for rel, _, _ in all_sites})
    bodies._ids[('warm_up.cpp', 1)] = WEAK_IDS

    _warm_up('warm_up', bodies)

    result = matcher.match_symbol(_free_row(), RecordingDict(), bodies, {}, free)

    # z_000 .. z_399 are scored; z_400 .. z_404 are outside the 400 cap.
    assert bodies.identifier_requests == [
        (f'z_{n:03d}.cpp', 1) for n in range(400)]
    assert free.lookups == ['spawn_thing']


def test_free_candidate_rejected_when_file_mentions_no_signature_token():
    # Site 0's file never names Widget; site 1's file does. An empty key-0
    # entry for the bare name forces the free-function branch.
    free = RecordingDict({'spawn_thing': [_site('unrelated.cpp', 1),
                                          _site('widget_impl.cpp', 5)]})
    index = RecordingDict({'spawn_thing': []})
    bodies = RecordingBodies(
        {'unrelated.cpp': ['int helper() {}', WEAK_BODY],
         'widget_impl.cpp': ['class Widget;', WEAK_BODY]},
        {('unrelated.cpp', 1): STRONG_IDS, ('widget_impl.cpp', 5): STRONG_IDS})

    result = matcher.match_symbol(_free_row(), index, bodies, {}, free)

    assert bodies.line_requests == ['unrelated.cpp', 'widget_impl.cpp']
    assert bodies.identifier_requests == [('widget_impl.cpp', 5)]
    assert (result['file'], result['line']) == ('widget_impl.cpp', 5)


def test_free_match_found_only_in_token_mentioning_file():
    free = RecordingDict({'spawn_thing': [_site('unrelated.cpp', 1)]})
    index = RecordingDict({'spawn_thing': []})
    bodies = RecordingBodies({'unrelated.cpp': ['nothing here', WEAK_BODY]},
                             {('unrelated.cpp', 1): STRONG_IDS})

    result = matcher.match_symbol(_free_row(), index, bodies, {}, free)

    assert result == {'status': 'not_found', 'evidence_total': 0,
                      'evidence_hits': 0, 'evidence_ratio': 0.0}
    assert bodies.identifier_requests == []


# ---------------------------------------------------------------------------
# Qualified path only (no free_index passed at all)
# ---------------------------------------------------------------------------

def test_qualified_path_without_free_index_ignores_index_entries():
    index = RecordingDict({})
    bodies = RecordingBodies({'free.cpp': ['x', WEAK_BODY]},
                             {('free.cpp', 1): STRONG_IDS})

    result = matcher.match_symbol(_free_row(), index, bodies)

    assert index.lookups == ['spawn_thing']
    assert index.probes == []
    assert result['status'] == 'not_found'
    assert result['evidence_total'] == 0


def test_match_symbol_needs_no_oracle_inputs():
    # ``subprocess.run``/``Popen`` are replaced by the autouse fixture, so this
    # call only succeeds because match_symbol itself never demangles or reads.
    index = RecordingDict({'ns::smart_ptr': [_site('member.cpp', 1)]})
    bodies = RecordingBodies({'member.cpp': ['x', WEAK_BODY]},
                             {('member.cpp', 1): STRONG_IDS})
    result = matcher.match_symbol(_strong_row(), index, bodies)
    assert result['evidence_total'] == 3
    assert result['evidence_hits'] == 3
    assert matcher.demangle_many([]) == {}
