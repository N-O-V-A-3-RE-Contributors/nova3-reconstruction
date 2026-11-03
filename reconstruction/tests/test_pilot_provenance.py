"""Tests for tools/compare/pilot_provenance.py.

Pure format tests on synthetic inputs: no original ELF or game binary is read,
no compiler is invoked, and the only filesystem use is a pytest ``tmp_path``
that proves JSON text round-trips.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools/compare/pilot_provenance.py"
SPEC = importlib.util.spec_from_file_location("pilot_provenance", MODULE_PATH)
prov = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prov)

SOURCE = b"// synthetic collada_matrix_multiply.cpp experiment\n"
OTHER_SOURCE = b"// committed review round\n"
OBJECT = b"\x7fELF synthetic armv7 object bytes"
OTHER_OBJECT = b"\x7fELF synthetic armv7 object bytes, rebuilt\n"
GCC = "arm-linux-androideabi-gcc (GCC) 4.4.3 20111209 (prerelease)"
FLAGS = ["-O2", "-fvisibility=hidden", "-Iinclude", "-Iinclude", "-std=gnu++98"]


def make_record(**overrides):
    kwargs = dict(
        pilot="collada_matrix_multiply",
        source_path=("reconstruction/recovered/libNOVA3_neon/"
                     "collada_matrix_multiply.cpp"),
        source_bytes=SOURCE,
        compiler_version=GCC,
        argv=[FLAGS[:3], FLAGS[3:]],
        object_bytes=OBJECT,
    )
    kwargs.update(overrides)
    return prov.build_record(**kwargs)


def test_build_record_fields_and_hashes():
    record = make_record()
    assert tuple(record) == prov.REQUIRED_FIELDS == prov.FIELDS
    assert record["schema"] == prov.SCHEMA
    assert record["source_sha256"] == hashlib.sha256(SOURCE).hexdigest()
    assert record["object_sha256"] == hashlib.sha256(OBJECT).hexdigest()
    assert record["argv"] == [FLAGS[:3], FLAGS[3:]]
    assert prov.record_problems(record) == {}


def test_build_record_is_pure():
    a, b = make_record(), make_record()
    assert a == b
    assert a["argv"] == b["argv"]
    assert prov.record_to_json(a) == prov.record_to_json(b)


def test_flag_order_and_duplicates_preserved_verbatim():
    record = make_record(argv=[["-O2", "-O3", "-O2"], ["-DFOO=1", "-DFOO=1"]])
    assert record["argv"] == [["-O2", "-O3", "-O2"], ["-DFOO=1", "-DFOO=1"]]
    reordered = make_record(argv=[["-O2", "-O2", "-O3"], ["-DFOO=1", "-DFOO=1"]])
    result = prov.compare_records(reordered, record)
    assert result["verdict"] == prov.STALE
    assert result["differing_fields"] == ["argv"]
    assert result["differing_args"]["recorded"] == ["-O2", "-O2", "-O3",
                                                    "-DFOO=1", "-DFOO=1"]
    assert result["differing_args"]["expected"] == ["-O2", "-O3", "-O2",
                                                    "-DFOO=1", "-DFOO=1"]


def test_evaluation_order_tracks_the_flag_sequence():
    # The flattening compares the flag sequence on purpose: how the pilot
    # groups steps is cosmetic, the order handed to the compiler is not.
    record = make_record(argv=[["-c", "-O2"], ["-S"]])
    regrouped = make_record(argv=[["-c"], ["-O2", "-S"]])
    assert prov.compare_records(record, regrouped)["verdict"] == prov.CLEAN
    reordered = make_record(argv=[["-S"], ["-c", "-O2"]])
    verdict = prov.compare_records(reordered, record)["verdict"]
    assert verdict == prov.STALE


def test_trailing_flag_change_is_detected():
    base = make_record(argv=[["-march=armv7-a", "-O2"]])
    replay = make_record(argv=[["-march=armv7-a", "-O3"]])
    result = prov.compare_records(replay, base)
    assert result["verdict"] == prov.STALE
    assert result["differing_fields"] == ["argv"]
    assert result["differing_args"]["recorded"] == ["-march=armv7-a", "-O3"]


def test_identical_records_are_clean():
    result = prov.compare_records(make_record(), make_record())
    assert result["verdict"] == prov.CLEAN
    assert result["differing_fields"] == []
    assert result["unknown_fields"] == []


def test_replayed_with_other_source_sha_is_stale_and_named():
    replayed = make_record(source_bytes=OTHER_SOURCE)
    base = make_record()
    assert replayed["object_sha256"] == base["object_sha256"]
    result = prov.compare_records(replayed, base)
    assert result["verdict"] == prov.STALE
    assert result["differing_fields"] == ["source_sha256"]
    reverse = prov.compare_records(base, replayed)
    assert reverse["verdict"] == prov.STALE
    assert reverse["differing_fields"] == ["source_sha256"]


def _declared_background(**overrides):
    kwargs = dict(source_dirty=False, git_head="0123abc", compiler_path="/cc",
                  object_path="candidate.o")
    kwargs.update(overrides)
    return make_record(**kwargs)


def test_stale_names_every_differing_field_in_canonical_order():
    replayed = _declared_background(source_bytes=OTHER_SOURCE,
                                    object_bytes=OTHER_OBJECT,
                                    compiler_version="GCC 4.6.3")
    result = prov.compare_records(replayed, _declared_background())
    assert result["verdict"] == prov.STALE
    assert result["differing_fields"] == ["source_sha256",
                                          "compiler_version",
                                          "object_sha256"]
    assert result["unknown_fields"] == []


def test_declared_background_keys_compare_exactly():
    base = _declared_background()
    moved = _declared_background(git_head="0123abd")
    result = prov.compare_records(moved, base)
    assert result["verdict"] == prov.STALE
    assert result["differing_fields"] == ["git_head"]
    flagged = {"source_dirty": True}
    dirty = _declared_background(**flagged)
    result = prov.compare_records(dirty, base)
    assert result["verdict"] == prov.STALE
    assert result["differing_fields"] == ["source_dirty"]
    against_plain = prov.compare_records(dirty, make_record())
    assert against_plain["verdict"] == prov.CLEAN
    marked = prov.compare_records(dirty, make_record())
    assert marked["source_state"] == "unknown"


@pytest.mark.parametrize("bad_hash", ["", "not-a-hash", "0" * 63, "0" * 65,
                                      "g" * 64, None])
def test_empty_or_non_hex_object_hash_is_unknown(bad_hash):
    recorded = make_record()
    recorded["object_sha256"] = bad_hash
    result = prov.compare_records(recorded, make_record())
    assert result["verdict"] == prov.UNKNOWN
    assert "object_sha256" in result["unknown_fields"]
    assert result["differing_fields"] == []


def test_missing_object_hash_field_is_unknown():
    recorded = make_record()
    del recorded["object_sha256"]
    result = prov.compare_records(recorded, make_record())
    assert result["verdict"] == prov.UNKNOWN
    assert result["unknown_fields"] == ["object_sha256"]


def test_blank_object_hash_provenance_says_unknown():
    record = make_record(object_bytes=None)
    assert record["object_sha256"] == ""
    result = prov.compare_records(record, dict(record))
    assert result["verdict"] == prov.UNKNOWN
    assert result["unknown_fields"] == ["object_sha256"]
    assert result["differing_fields"] == []
    assert prov.compare_records(record, make_record(source_dirty=False))[
        "verdict"] == prov.UNKNOWN


def test_unknown_never_masks_a_differing_source():
    recorded = make_record(source_bytes=OTHER_SOURCE, object_bytes=None)
    result = prov.compare_records(recorded, make_record())
    assert result["verdict"] == prov.UNKNOWN
    assert result["unknown_fields"] == ["object_sha256"]


@pytest.mark.parametrize("bad_version", ["", "   ", None])
def test_missing_compiler_version_is_unknown(bad_version):
    recorded = make_record()
    recorded["compiler_version"] = bad_version
    result = prov.compare_records(recorded, make_record())
    assert result["verdict"] == prov.UNKNOWN
    assert result["unknown_fields"] == ["compiler_version"]


def test_empty_expected_compiler_version_is_unknown():
    expected = make_record(compiler_version="")
    assert prov.compare_records(make_record(), expected)["verdict"] == prov.UNKNOWN


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(schema="nova3.pilot-provenance/2"),
    lambda r: r.pop("schema"),
    lambda r: r.update(argv=["-O2"]),
    lambda r: r.update(argv=[["-O2"], [b"-O3"]]),
    lambda r: r.update(argv=[["-O2"], "-O3"]),
    lambda r: r.update(pilot=None),
    lambda r: r.update(source_sha256="zz"),
])
def test_malformed_record_is_unknown(mutation):
    recorded = make_record()
    mutation(recorded)
    expected = make_record()
    mutation(expected)
    result = prov.compare_records(recorded, expected)
    assert result["verdict"] == prov.UNKNOWN
    assert result["unknown_fields"]


def test_expected_omitting_required_field_is_unknown():
    expected = make_record()
    del expected["source_sha256"]
    result = prov.compare_records(make_record(), expected)
    assert result["verdict"] == prov.UNKNOWN
    assert result["unknown_fields"] == ["source_sha256"]


def test_unparseable_json_is_unknown():
    result = prov.compare_records("[1, 2]", prov.record_to_json(make_record()))
    assert result["verdict"] == prov.UNKNOWN
    with pytest.raises(ValueError):
        prov.parse_record("{not json")


def test_parse_record_canonicalises_key_order():
    record = make_record()
    shuffled = json.dumps({k: record[k] for k in reversed(prov.REQUIRED_FIELDS)})
    assert prov.parse_record(shuffled) == record
    assert prov.compare_records(shuffled, record)["verdict"] == prov.CLEAN


def test_json_round_trip_inside_tmp_path(tmp_path):
    record = make_record()
    path = tmp_path / "provenance.json"
    path.write_text(prov.record_to_json(record), encoding="utf-8")
    assert prov.compare_records(path.read_bytes(), record)["verdict"] == prov.CLEAN
    assert sorted(p.name for p in tmp_path.iterdir()) == ["provenance.json"]


def test_optional_background_keys_compare_only_when_expected_states_them():
    base = make_record()
    assert "source_dirty" not in base
    pristine = make_record(source_dirty=False, git_head="abc1234")
    assert prov.source_state(pristine) == "pristine"
    assert prov.compare_records(pristine, base)["verdict"] == prov.CLEAN
    dirty = make_record(source_dirty=True)
    assert prov.source_state(dirty) == "dirty"
    assert prov.compare_records(dirty, base)["verdict"] == prov.CLEAN
    strict = prov.compare_records(dirty, make_record(source_dirty=False))
    named = strict["differing_fields"]
    assert named == ["source_dirty"]


def test_module_imports_only_stdlib():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "hashlib", "json", "re", "sys", "pathlib",
                        "typing"}


def test_import_needs_no_analysis_dependencies():
    code = (
        "import sys, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('pp', {str(MODULE_PATH)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "assert not {'elftools', 'capstone', 'pyelftools'} & set(sys.modules)\n"
        "print(mod.SCHEMA)\n"
    )
    run = subprocess.run([sys.executable, "-I", "-B", "-c", code],
                         capture_output=True, text=True,
                         cwd=tempfile.gettempdir())
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == prov.SCHEMA
