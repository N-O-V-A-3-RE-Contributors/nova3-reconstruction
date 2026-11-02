"""Parser-checked facts block for the five settled size/instruction residuals.

``docs/progress/NEXT.md`` states its residual sizes only as prose numbers (a
load gap, a byte gap, two candidate-vs-reference sizes and identical-word
counts). This module pins them into one fenced JSON block
(``schema: nova3-settled-residuals/1``) and checks it on three sides:

* original sizes against the Premium ``.symtab`` via ``readelf -sW``: each
  entry's address resolves to exactly one 32-bit ``STT_FUNC`` whose
  ``st_value`` is the block address and whose ``st_size`` is the block
  ``original_size`` (the reference is ARM/A32, so ``st_value`` bit 0 is 0);
* the *first* citation of each differential in the prose equals the block
  value, and no other size differential is stated in prose;
* no prose line raises a block number to "matched".

Only what the block claims (sizes and integer differentials) is checked. This
is not a code or a behaviour comparison, and passing it upgrades no function's
completion state.

The check is read-only: besides NEXT.md it reads only the reference ELF under
``analysis/`` through ``readelf``, never the APK, the OBB archives or anything
outside ``.cache``, and it imports nothing from ``analysis/``. When the
reference is absent the ELF-side test skips with a named reason and the prose
checks still run. The mutation self-checks edit in-memory copies only.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NEXT_MD = ROOT / "docs/progress/NEXT.md"
REFERENCE = (ROOT / "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/"
                    "libNOVA3_neon.so")
TEST_FILE = "reconstruction/tests/test_next_settled_residuals.py"

SCHEMA = "nova3-settled-residuals/1"
FENCE_OPEN = "```json"
BLOCK_TAG = '"schema": "nova3-settled-residuals/1"'
NON_NUMERIC = ("id", "symbol", "address", "evidence", "note", "status")
REQUIRED_FIELDS = ("id", "symbol", "address", "original_size", "candidate_size",
                   "evidence")

# Every differential NEXT.md cites: the block fields of both sides and the
# label each side carries, in the order the prose writes the cited numbers.
PAIRS = {
    "size_candidate_first": (("candidate_size", "original_size"),
                             ("candidate", "original")),
    "vldr_candidate_first": (("vldr_candidate", "vldr_original"),
                             ("candidate", "original")),
    "body_vs_reference": (("candidate_body", "original_size"),
                          ("body", "original")),
    "words": (("identical_words", "identical_words"), ("a", "b")),
    "candidate_size": (("candidate_size", "candidate_size"), ("candidate",)),
    "size_gap": (("size_gap", "size_gap"), ("gap",)),
    "load_gap": (("load_gap", "load_gap"), ("gap",)),
    "size": (("original_size", "candidate_size"), ("original", "candidate")),
    # "224 B candidate, reference 228 B": the groups are candidate, original.
    "size_candidate_first": (("candidate_size", "original_size"),
                             ("candidate", "original")),
    "reference_size": (("original_size", "original_size"), ("reference",)),
}
SIZE_PAIRS = ("size_candidate_first", "body_vs_reference", "size",
              "reference_size", "vldr_candidate_first")

# The five settled residuals. ``prose`` tells, for each differential, its
# block pair and its patterns in NEXT.md; the first occurrence over the whole
# prose is the decisive one. A pattern with two groups cites both sides, with
# one group (or none) cites a single number for both.
EXPECTED = {
    "operator_star": {
        "prose": (
            (r"(?<![0-9.])6-load gap", "load_gap"),
            (r"([0-9]+) vs ([0-9]+) B", "size_candidate_first"),
            (r"vldr ([0-9]+) vs ([0-9]+)", "vldr_candidate_first"),
        ),
    },
    "prepare_cache": {
        "prose": (
            (r"([0-9]+) B candidate, reference ([0-9]+) B",
             "size_candidate_first"),
            (r"(?<![0-9.])4-byte gap", "size_gap"),
        ),
    },
    "android_cpu_init": {
        "prose": (
            # Sized three ways in the prose: "2648-vs-2632 B", the folded
            # "2648\n   vs 2632 B" (no "B" after the first number), and the
            # body form "candidate 2600 B vs reference 2632 B".
            (r"(?<![0-9])([0-9]+)(?:-vs-| vs )([0-9]+)", "size_candidate_first"),
            (r"candidate ([0-9]+) B vs reference ([0-9]+) B", "body_vs_reference"),
            (r"its ([0-9]+) B body against the ([0-9]+) B reference",
             "body_vs_reference"),
            (r"(?<![0-9])([0-9]+) B, reference ([0-9]+) B", "size_candidate_first"),
        ),
    },
    "copy_ctor": {
        "prose": (
            (r"41/41 non-branch", "words"),
            (r"candidate ([0-9]+) B, reference ([0-9]+) B", "size"),
        ),
    },
    "ctor_econstructor": {
        "prose": (
            (r"56/56 B, candidate ([0-9]+) B, reference ([0-9]+) B",
             "size_candidate_first"),
            (r"56/56 B, reference ([0-9]+) B", "reference_size"),
            (r"56/56 B", "words"),
        ),
    },
}

# Any other size differential in prose: "<n>-vs-<m> B", "<n> vs <m> B",
# "<n>-byte gap", "<n>-load gap", "<a>/<b> non-branch".
CITED_PHRASE = re.compile(
    r"(?<![\w.])[0-9]+-vs-[0-9]+ B"
    r"|(?<![\w.])[0-9]+ vs [0-9]+ B"
    r"|(?<![\w.])[0-9]+-byte gap"
    r"|(?<![\w.])[0-9]+-load gap"
    r"|(?<![\w.])[0-9]+/[0-9]+ non-branch"
    # "candidate 2600 B vs reference 2632 B", "224 B candidate, reference 228 B"
    r"|(?<![\w.])[0-9]+ B candidate"
    r"|(?<![\w.])candidate [0-9]+ B"
    r"|(?<![\w.])reference [0-9]+ B")


# -------------------------------------------------------------- block parse

def _block_span(text: str) -> tuple:
    start = text.index(FENCE_OPEN)
    body_start = text.index("\n", start) + 1
    end = text.index("```", body_start)
    return body_start, end


def _no_duplicates(pairs):
    keys = [key for key, _value in pairs]
    assert len(keys) == len(set(keys)), (
        "duplicate key(s) in block: %r"
        % sorted({key for key in keys if keys.count(key) > 1}),)
    return dict(pairs)


def parse_block(text: str) -> dict:
    """Return the parsed block; fails on anything but exactly one block."""
    assert text.count(FENCE_OPEN) == 1, "expected exactly one ```json block"
    assert text.count(BLOCK_TAG) == 1, "schema tag must appear exactly once"
    body_start, end = _block_span(text)
    return json.loads(text[body_start:end], object_pairs_hook=_no_duplicates)


def _entry(block: dict, residual_id: str) -> dict:
    for entry in block["residuals"]:
        if entry["id"] == residual_id:
            return entry
    raise AssertionError(residual_id)


def check_block_shape(block: dict) -> None:
    assert block.get("schema") == SCHEMA
    residuals = block.get("residuals")
    assert isinstance(residuals, list), "residuals must be a list"
    ids = [entry.get("id") for entry in residuals]
    assert len(ids) == len(set(ids)), "duplicate residual id: %r" % ids
    assert set(ids) == set(EXPECTED), (
        "residual ids differ: missing %r, unexpected %r"
        % (sorted(set(EXPECTED) - set(ids)), sorted(set(ids) - set(EXPECTED))))
    for entry in residuals:
        for field in REQUIRED_FIELDS:
            assert field in entry, "%s lacks %r" % (entry.get("id"), field)
        assert isinstance(entry["address"], str), "%s address is a string" % (
            entry["id"],)
        int(entry["address"], 0)
        assert isinstance(entry["evidence"], str) and entry["evidence"].strip()
        for key, value in entry.items():
            if key in NON_NUMERIC:
                continue
            assert isinstance(value, int), "%s.%s must be an int" % (
                entry["id"], key)
        for _pattern, pair_key in EXPECTED[entry["id"]]["prose"]:
            for field in PAIRS[pair_key][0]:
                assert isinstance(entry.get(field), int), (entry["id"], field)
    reference = block.get("reference")
    assert isinstance(reference, dict) and re.fullmatch(
        r"[0-9a-f]{64}", reference.get("sha256", "")), "reference.sha256"
    assert reference.get("path") == REFERENCE.relative_to(ROOT).as_posix()


# -------------------------------------------------------------- prose view

def prose_text(text: str) -> str:
    """``text`` with fenced blocks blanked and line folds joined.

    Blanking keeps the block's own evidence strings out of the prose scan;
    joining folds lets one sentence cite its numbers across a line break.
    """
    kept = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            kept.append("")
        elif in_fence:
            kept.append("")
        else:
            kept.append(line)
    return re.sub(r"[ \t\n]+", " ", " ".join(kept))


def first_citation(prose: str, residual_id: str) -> tuple:
    """``(match, pair_key)`` of the earliest citation; asserts one exists."""
    best = None
    for pattern, pair_key in EXPECTED[residual_id]["prose"]:
        for match in re.finditer(pattern, prose):
            if best is None or match.start() < best[0].start():
                best = (match, pair_key)
    # Two patterns can share one text ("540 vs 564 B"); the earliest, then the
    # first listed, wins.
    if best is not None:
        for pattern, pair_key in EXPECTED[residual_id]["prose"]:
            for match in re.finditer(pattern, prose):
                if match.start() == best[0].start() and match.group(0) != "":
                    best = (match, pair_key)
                break
    assert best is not None, "%s: no prose citation of %r" % (
        residual_id, [pattern for pattern, _ in EXPECTED[residual_id]["prose"]])
    return best


def cited_numbers(match) -> list:
    groups = match.groups()
    if not groups:  # "540 vs 564 B": the literal's two numbers
        numbers = [int(token) for token in re.findall(r"[0-9]+", match.group(0))]
        return (numbers[:2] if len(numbers) > 1 else [numbers[0]] * 2)
    if len(groups) == 1:
        return [int(groups[0])] * 2
    return [int(group) for group in groups]


def citations(prose: str, block: dict, residual_id: str) -> list:
    """``(label, expected, found, text)`` per side at the first citation."""
    match, pair_key = first_citation(prose, residual_id)
    (field_a, field_b), labels = PAIRS[pair_key]
    entry = _entry(block, residual_id)
    if len(labels) == 1:
        return [(labels[0], entry[field_a], cited_numbers(match)[0],
                 match.group(0))]
    numbers = cited_numbers(match)
    assert len(numbers) == 2, (residual_id, match.group(0))
    return [(labels[0], entry[field_a], numbers[0], match.group(0)),
            (labels[1], entry[field_b], numbers[1], match.group(0))]


def _cited_sizes(prose: str, block: dict, residual_id: str) -> list:
    """Every size citation of a residual ("540 vs 564 B", "vldr 70 vs 76")."""
    out = []
    for pattern, pair_key in EXPECTED[residual_id]["prose"]:
        if pair_key not in SIZE_PAIRS:
            continue
        match = re.search(pattern, prose)
        if match is None:
            continue
        out.extend((label, expected, found, match.group(0))
                   for label, expected, found in
                   _pair_values(match, block, residual_id, pair_key))
    return out


def _pair_values(match, block: dict, residual_id: str, pair_key: str) -> list:
    """One ``(label, expected, found)`` per side named by the pair."""
    (field_a, field_b), labels = PAIRS[pair_key]
    entry = _entry(block, residual_id)
    numbers = cited_numbers(match)
    if len(labels) == 1:
        return [(labels[0], entry[field_a], numbers[0])]
    return [(labels[0], entry[field_a], numbers[0]),
            (labels[1], entry[field_b], numbers[1])]


def check_prose(prose: str, block: dict) -> None:
    problems = []
    for residual_id in EXPECTED:
        for label, expected, found, text in _cited_sizes(prose, block,
                                                         residual_id):
            if found != expected:
                problems.append("%s: %r gives %s %d, block says %d"
                                % (residual_id, text, label, found, expected))
    assert not problems, problems


def check_only_known_phrases(prose: str, block: dict) -> None:
    """Every size differential stated in prose must be one the block cites."""
    def cited_numbers_in(text):
        numbers = tuple(sorted(re.findall(r"[0-9]+", text)))
        return (re.sub(r"[0-9]+", "#", text), numbers)

    stated = set()
    for residual_id in EXPECTED:
        for pattern, _pair in EXPECTED[residual_id]["prose"]:
            for found in re.finditer(pattern, prose):
                stated.add(cited_numbers_in(found.group(0)))
    extra = [text for text in CITED_PHRASE.findall(prose)
             if cited_numbers_in(text) not in stated
             and not is_scoped_gap(prose, text)]
    assert not extra, (
        "prose cites differentials absent from the block: %r" % (extra,))


SCOPED_GAP = re.compile(r"[0-9]+-byte gap")


def is_scoped_gap(prose: str, text: str) -> bool:
    """A byte gap that belongs to a function outside the five residuals.

    Only the five settled residuals are frozen here; another function's open
    gap (the cpu-features TU had its own) is listed under its own name and is
    not a statement about this block.
    """
    if not SCOPED_GAP.fullmatch(text):
        return False
    for match in re.finditer(re.escape(text), prose):
        window = prose[max(0, match.start() - 160):match.end() + 40]
        if not any(name in window for name in EXPECTED):
            return True
    return False


# --------------------------------------------------------- raised-to-matched

MATCHED = re.compile(r"(?<![A-Za-z])(?:(?i:not ))?(?i:matched)(?![A-Za-z])")
HEX = re.compile(r"0x0*([0-9a-fA-F]+)")


def _numbers_in(line: str) -> set:
    found = {int(token) for token in re.findall(r"(?<![\w.])[0-9]+(?![\w.])",
                                                line)}
    found |= {int(token, 16) for token in HEX.findall(line)}
    return found


def block_quantities(block: dict) -> set:
    quantities = set()
    for entry in block["residuals"]:
        quantities.add(int(entry["address"], 0))
        quantities.update(value for value in entry.values()
                          if isinstance(value, int))
        for key, value in entry.items():
            if isinstance(value, list) and key != "cited_in":
                quantities.update(v for v in value if isinstance(v, int))
    return quantities


def raised_as_matched(text: str, block: dict) -> list:
    quantities = block_quantities(block)
    problems = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not MATCHED.search(line):
            continue
        if re.search(r"not matched|nothing matched", line, re.IGNORECASE):
            continue
        hits = sorted(_numbers_in(line) & quantities)
        if hits:
            problems.append("line %d raises %s as matched: %r"
                            % (number, hits, line.strip()))
    return problems


# ---------------------------------------------------------------- ELF side

def reference_tool() -> tuple:
    """``(readelf path, None)`` or ``(None, named reason)``."""
    tool = shutil.which("readelf")
    if tool is None:
        return None, "readelf not found on PATH"
    if not REFERENCE.is_file():
        return None, "reference ELF absent at %s" % REFERENCE.relative_to(ROOT)
    return tool, None


def read_symtab(tool: str) -> list:
    result = subprocess.run([tool, "-sW", str(REFERENCE)],
                            capture_output=True, text=True, timeout=300,
                            check=False)
    assert result.returncode == 0, result.stderr[-500:]
    pattern = re.compile(
        r"^\x20*[0-9]+:\x20+([0-9a-fA-F]+)\x20+([0-9a-fA-F]+)\x20+(\w+)\x20+\w+\x20+\w+\x20+"
        r"([0-9]+)\x20+(\S+)\x20*$")
    rows = []
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match:
            rows.append((int(match.group(1), 16), int(match.group(2), 16),
                         match.group(3), match.group(5)))
    return rows


def check_against_symtab(rows: list, block: dict) -> None:
    for entry in block["residuals"]:
        address = int(entry["address"], 0)
        funcs = [row for row in rows
                 if row[0] == address and row[1] > 0 and row[2] == "FUNC"]
        assert len(funcs) == 1, "%s: %#x resolves to %d STT_FUNC rows: %r" % (
            entry["id"], address, len(funcs), [row[3] for row in funcs])
        value, size, _kind, name = funcs[0]
        assert value & 1 == 0, "%s is Thumb; the reference is A32" % entry["id"]
        assert name == entry["symbol"], (
            "%s: .symtab names %#x %r, block says %r"
            % (entry["id"], address, name, entry["symbol"]))
        inside = [row[3] for row in rows
                  if row[0] < address < row[0] + row[1] and row[2] == "FUNC"
                  and not row[3].startswith("$")]
        assert not inside, "%s: %#x falls inside %r" % (entry["id"], address,
                                                        inside)
        assert size == entry["original_size"], (
            "%s: st_size %d, block original_size %d"
            % (entry["id"], size, entry["original_size"]))


# ------------------------------------------------------------------- tests

def _current_text() -> str:
    return NEXT_MD.read_text(encoding="utf-8")


def test_block_is_valid_json_with_exactly_the_five_residuals():
    check_block_shape(parse_block(_current_text()))


def test_block_original_sizes_match_premium_symtab():
    tool, reason = reference_tool()
    if tool is None:
        pytest.skip("reference ELF check skipped: " + reason)
    block = parse_block(_current_text())
    check_block_shape(block)
    rows = read_symtab(tool)
    assert sum(1 for row in rows if row[2] == "FUNC") > 1000, "thin symtab"
    check_against_symtab(rows, block)


def test_prose_citations_agree_with_block():
    text = _current_text()
    block = parse_block(text)
    check_block_shape(block)
    check_prose(prose_text(text), block)


def test_prose_states_no_differential_outside_the_block():
    text = _current_text()
    check_only_known_phrases(prose_text(text), parse_block(text))


def test_block_numbers_are_not_raised_as_matched():
    text = _current_text()
    assert not raised_as_matched(text, parse_block(text))


def test_pointer_to_this_test_is_present():
    command = "python -m pytest %s" % TEST_FILE
    pointers = [line for line in _current_text().splitlines()
                if command in line and line.startswith("Re-run")]
    assert len(pointers) == 1, pointers


def test_scope_excludes_private_trees_and_network():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {"__future__", "ast", "json", "re", "shutil",
                        "subprocess", "sys", "pathlib", "pytest"}, imported


# --------------------------------------------------------- mutation self-check

def mutate(text: str, residual_id: str, field: str, delta: int) -> str:
    block = parse_block(text)
    entry = _entry(block, residual_id)
    assert field in entry, (residual_id, field)
    entry[field] += delta
    body_start, end = _block_span(text)
    return text[:body_start] + json.dumps(block, indent=2) + "\n" + text[end:]


def _prose_fields(residual_id: str) -> list:
    fields = []
    for _pattern, pair_key in EXPECTED[residual_id]["prose"]:
        fields.extend(PAIRS[pair_key][0])
    return sorted(set(fields))


@pytest.mark.parametrize("residual_id", sorted(EXPECTED))
def test_mutation_of_original_size_is_detected(residual_id):
    text = _current_text()
    check_prose(prose_text(text), parse_block(text))
    tool, _reason = reference_tool()
    rows = read_symtab(tool) if tool is not None else None
    for delta in (-4, 4):
        # Each field the prose cites must catch an edit; operator* names its
        # sizes only as "540 vs 564 B", and its 540 B rows are already 24 B
        # short of the 564 B original, so the vldr counts pin that side.
        sizes = ("original_size", "candidate_size")
        if residual_id == "operator_star":
            sizes = ("original_size", "candidate_size", "vldr_original",
                     "vldr_candidate")
        for field in sizes:
            mutated = mutate(text, residual_id, field, delta)
            block = parse_block(mutated)
            assert _entry(block, residual_id)[field] != \
                _entry(parse_block(text), residual_id)[field]
            with pytest.raises(AssertionError):
                check_prose(prose_text(mutated), block)
        mutated = mutate(text, residual_id, "original_size", delta)
        if rows is not None:
            with pytest.raises(AssertionError):
                check_against_symtab(rows, parse_block(mutated))


def test_mutation_of_stated_differential_is_detected():
    text = _current_text()
    cases = [
        ("operator_star", "candidate_size", 4),
        ("operator_star", "vldr_candidate", 6),
        ("prepare_cache", "candidate_size", 4),
        ("android_cpu_init", "candidate_size", 12),
        ("android_cpu_init", "candidate_body", 48),
        ("copy_ctor", "identical_words", -1),
        ("ctor_econstructor", "identical_words", -1),
    ]
    for residual_id, field, delta in cases:
        mutated = mutate(text, residual_id, field, delta)
        with pytest.raises(AssertionError):
            check_prose(prose_text(mutated), parse_block(mutated))


def test_mutation_raising_a_number_to_matched_is_detected():
    text = _current_text()
    block = parse_block(text)
    assert not raised_as_matched(text, block)
    line = "  (`0x006e7e60`, 540 vs 564 B) is now MATCHED"
    assert raised_as_matched(text + "\n" + line + "\n", block)
    assert raised_as_matched(line + "\n\n" + text, block)


def test_mutation_of_prose_citation_is_detected():
    text = _current_text()
    block = parse_block(text)
    for old, new in (("540 vs 564 B", "544 vs 564 B"),
                     ("2648-vs-2632 B", "2644-vs-2632 B"),
                     ("2648 vs 2632 B", "2644 vs 2632 B"),
                     ("41/41 non-branch", "42/41 non-branch")):
        assert text.count(old) >= 1, old
        with pytest.raises(AssertionError):
            check_prose(prose_text(text.replace(old, new, 1)), block)


def test_new_unlisted_differential_in_prose_is_detected():
    text = _current_text()
    for addition in (" New: 600 vs 564 B in the product.\n",
                     " A new 8-byte gap remains.\n"):
        prose = prose_text(text) + addition
        with pytest.raises(AssertionError):
            check_only_known_phrases(prose, parse_block(text))


def test_block_rejects_wrong_residual_set():
    block = parse_block(_current_text())
    with pytest.raises(AssertionError):
        check_block_shape(dict(block, residuals=block["residuals"][:-1]))
    renamed = json.loads(json.dumps(block))
    renamed["residuals"][0]["id"] = "not_a_residual"
    with pytest.raises(AssertionError):
        check_block_shape(renamed)
    with pytest.raises(AssertionError):
        check_block_shape(dict(block, schema="nova3-settled-residuals/2"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
