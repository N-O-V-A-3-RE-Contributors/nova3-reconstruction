"""Source-owned synthetic wrappers only; no reference source or game inputs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


TOOL = Path(__file__).resolve().parents[2] / "tools/compare/unity_include_inventory.py"
SPEC = importlib.util.spec_from_file_location("unity_include_inventory", TOOL)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def _wrapper(tmp_path, text, name="UB_synthetic.cpp"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _rows(path):
    return tool.inventory([path])["includes"]


def _cli(*paths):
    return subprocess.run(
        [sys.executable, "-B", str(TOOL), *map(str, paths)],
        capture_output=True, text=True, check=False,
    )


def test_literals_duplicates_and_metadata_only_schema(tmp_path):
    path = _wrapper(tmp_path, '\n#include "part.cpp"\n#include <other.c>\n'
                    '#include "part.cpp"\n#include "skip.hpp"\n'
                    '#include <skip.h>\n#include "skip.cc"\n#include "skip.CPP"\n')
    report = tool.inventory([path])
    assert set(report) == {
        "schema", "grouping", "original_build", "scope", "wrappers", "includes",
    }
    assert report["schema"] == "nova3.unity-include-inventory/1"
    assert report["grouping"] == "HYPOTHESIS"
    assert report["original_build"] == "UNKNOWN"
    assert report["scope"] == "lexical-direct-includes-only"
    assert report["wrappers"] == [str(path)]
    assert report["includes"] == [
        {"wrapper": str(path), "line": line, "include_operand": operand,
         "occurrence_order": order, "conditional_depth": 0, "kind": "literal"}
        for order, (line, operand) in enumerate([
            (2, '"part.cpp"'), (3, '<other.c>'), (4, '"part.cpp"'),
        ], 1)
    ]


def test_no_whitespace_required_before_literal_operands(tmp_path):
    path = _wrapper(tmp_path, '#include"part.cpp"\n# include<other.c>\n'
                    '#include/**/"third.cpp"\n#include_next "ignored.cpp"\n')
    assert [r["include_operand"] for r in _rows(path)] == [
        '"part.cpp"', '<other.c>', '"third.cpp"',
    ]


def test_comments_and_non_directives_are_ignored(tmp_path):
    path = _wrapper(tmp_path, '''// #include "comment.cpp"
/* #include "block.c"
#include "still_block.cpp"
*/ /* prefix */ # /* gap */ include /* operand */ "real.cpp" // tail
const char *s = "#include \\"string.cpp\\"";
char quote = '\\'';
int code; #include "not_a_directive.c"
// #if COMMENTED_CONDITION
# include <real.c> /* #endif */
''')
    rows = _rows(path)
    assert [(r["line"], r["include_operand"], r["conditional_depth"]) for r in rows] == [
        (4, '"real.cpp"', 0), (9, '<real.c>', 0),
    ]


def test_raw_strings_cannot_supply_directives_or_comment_markers(tmp_path):
    path = _wrapper(tmp_path, '''const char *a = u8R"tag(
#include "fake.cpp"
/* // #if FAKE
)tag";
const char *b = LR"(
#include <also_fake.c>
)"; #include "same_line_fake.cpp"
#include "real.cpp"
''')
    rows = _rows(path)
    assert [(r["line"], r["include_operand"]) for r in rows] == [(8, '"real.cpp"')]


def test_nested_conditionals_all_branches_and_unresolved_macros(tmp_path):
    path = _wrapper(tmp_path, '''#define SELECTED "do_not_guess.cpp"
#if 0
#include "inactive.cpp"
#ifdef INNER
#include SELECTED
#elif OTHER
#include PICK_SOURCE(foo, bar)
#else
#include "alternative.c"
#endif
#else
#ifndef THIRD
#include <third.cpp>
#endif
#endif
#include "last.cpp"
''')
    rows = _rows(path)
    assert [(r["line"], r["include_operand"], r["conditional_depth"], r["kind"])
            for r in rows] == [
        (3, '"inactive.cpp"', 1, "literal"),
        (5, 'SELECTED', 2, "unresolved"),
        (7, 'PICK_SOURCE(foo, bar)', 2, "unresolved"),
        (9, '"alternative.c"', 2, "literal"),
        (13, '<third.cpp>', 2, "literal"),
        (16, '"last.cpp"', 0, "literal"),
    ]
    assert [r["occurrence_order"] for r in rows] == list(range(1, 7))
    # Macro definitions and conditional expressions are not published.
    assert "do_not_guess" not in json.dumps(tool.inventory([path]))
    assert "THIRD" not in json.dumps(tool.inventory([path]))


def test_line_splicing_before_comments_and_physical_line_numbers(tmp_path):
    path = _wrapper(tmp_path, ''.join([
        '#inc', '\\\n', 'lude ', '\\\n', '"con', '\\\n', 'tinued.cpp"\n',
        '// comment ', '\\\n', '#include "hidden.cpp"\n',
        '/\\\n* #include "hidden_block.cpp" */\n',
        '\\\n', ' #include "visible.c"\n',
    ]))
    rows = _rows(path)
    assert [(r["line"], r["include_operand"]) for r in rows] == [
        (1, '"continued.cpp"'), (10, '"visible.c"'),
    ]


def test_header_names_preserve_comment_like_characters_and_backslashes(tmp_path):
    path = _wrapper(tmp_path, '#include "folder//part.cpp"\n'
                    '#include <folder/*literal*/part.c>\n'
                    '#include "..\\nested\\part.cpp"\n')
    assert [r["include_operand"] for r in _rows(path)] == [
        '"folder//part.cpp"', '<folder/*literal*/part.c>', '"..\\nested\\part.cpp"',
    ]


def test_only_explicit_wrappers_are_opened_read_only(tmp_path, monkeypatch):
    path = _wrapper(tmp_path, '#include "missing.cpp"\n#include <missing.c>\n'
                    '#include DYNAMIC_SOURCE\n')
    _wrapper(tmp_path, '#include "unrequested.cpp"\n', "UB_unrequested.cpp")
    original_open = Path.open
    opened = []

    def guard(self, mode="r", *args, **kwargs):
        assert self == path, "scanner must not open any include or discover wrappers"
        assert mode == "r", "scanner must never write inputs or outputs"
        opened.append(self)
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    report = tool.inventory([path])
    assert opened == [path]
    assert len(report["includes"]) == 3


def test_cli_is_identical_across_runs_and_preserves_explicit_order(tmp_path):
    first = _wrapper(tmp_path, '#include "z.cpp"\n#include "z.cpp"\n', "UB_z.cpp")
    second = _wrapper(tmp_path, '#if 0\n#include MACRO\n#endif\n', "UB_a.cpp")
    before = {p: p.read_bytes() for p in (first, second)}
    results = [_cli(first, second, first) for _ in range(2)]
    for result in results:
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
    assert results[0].stdout == results[1].stdout
    report = json.loads(results[0].stdout)
    assert report == tool.inventory([first, second, first])
    assert report["wrappers"] == [str(first), str(second), str(first)]
    assert [r["occurrence_order"] for r in report["includes"]] == [1, 2, 1, 1, 2]
    assert {p: p.read_bytes() for p in before} == before


def test_bom_crlf_and_line_remaps(tmp_path):
    path = tmp_path / "UB_newlines.cpp"
    path.write_bytes(b'\xef\xbb\xbf#line 1000 "not_a_wrapper.cpp"\r\n'
                     b'#include \\\r\n"part.cpp"\r\n#include <other.c>\r\n')
    assert [(r["line"], r["include_operand"]) for r in _rows(path)] == [
        (2, '"part.cpp"'), (4, '<other.c>'),
    ]


def test_empty_wrapper_reports_no_includes(tmp_path):
    path = _wrapper(tmp_path, "// just a comment\n")
    assert tool.inventory([path])["includes"] == []


@pytest.mark.parametrize("text", [
    '#endif\n', '#else\n', '#elif FOO\n', '#if X\n',
    '#if X\n#else\n#else\n#endif\n',
    '#if X\n#else\n#elif Y\n#endif\n',
    '/* unclosed\n', 'const char *x = R"tag(unclosed\n',
    '#include\n', '#include "unclosed.cpp\n',
    '#include <unclosed.c\n', '#include "part.cpp" junk\n',
])
def test_ambiguous_or_malformed_input_is_refused_without_partial_json(tmp_path, text):
    path = _wrapper(tmp_path, '#include "earlier.cpp"\n' + text)
    with pytest.raises(tool.InventoryError):
        tool.inventory([path])
    result = _cli(path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "unity include inventory:" in result.stderr
    assert "earlier.cpp" not in result.stderr


@pytest.mark.parametrize("name", ["ordinary.cpp", "UB_bad.c", "UB_*.cpp", "missing/UB_no.cpp"])
def test_cli_refuses_wrong_names_unexpanded_globs_and_missing_files(tmp_path, name):
    result = _cli(tmp_path / name)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_requires_explicit_paths():
    with pytest.raises(tool.InventoryError, match="at least one"):
        tool.inventory([])
    result = _cli()
    assert result.returncode == 2
    assert result.stdout == ""


def test_name_validation_precedes_any_file_open(tmp_path, monkeypatch):
    def unexpected_open(*args, **kwargs):
        pytest.fail("invalid inputs must be rejected before opening wrappers")

    monkeypatch.setattr(Path, "open", unexpected_open)
    with pytest.raises(tool.InventoryError):
        tool.inventory([tmp_path / "UB_valid.cpp", tmp_path / "not_wrapper.cpp"])


def test_invalid_utf8_fails_without_source_dump(tmp_path):
    path = tmp_path / "UB_encoding.cpp"
    path.write_bytes(b'#include "private_name.cpp"\n\xff')
    result = _cli(path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "private_name" not in result.stderr
    assert "UnicodeDecodeError" in result.stderr
