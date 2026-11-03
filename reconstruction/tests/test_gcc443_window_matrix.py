"""Offline freshness gate for the seven GCC 4.4.3 function groups.

The accessor group expands to seven members and getStream to two overloads;
CFPSCounter records the base constructor, not its duplicate complete constructor.
Unavailable historical flag reports/objects are NOT_MEASURED, never DIFFERENT.
To reproduce locally, run this file with pytest. NOVA3_WINDOW_INPUT_ROOT can
point to a read-only checkout containing analysis/ and .cache/toolchains/;
NOVA3_RETAIL_ELF and NOVA3_R8E_NDK override those individual inputs.
Precompiled candidate objects belong in this checkout's recorded .cache paths.
Fresh compilation goes into pytest's temporary directory. No original code is
executed or emitted. Matching here means only the existing checker's window
status, including its branch-displacement normalization; no whole-library or
source/behavioural equivalence is implied.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "reconstruction/recovered/libNOVA3_neon/gcc443_window_matrix.json"
DATA = json.loads(MATRIX.read_text())
ROWS = DATA["functions"]
EXPECTED = {
    # Member identity is independent of the row's editable symbol/address.
    "computePitch": "_ZN6glitch5video12pixel_format12computePitchENS0_14E_PIXEL_FORMATEj",
    "getTransposed": "_ZNK6glitch4core8CMatrix4IfE13getTransposedERS2_",
    "accessor_getVersion": "_ZNK6glitch7collada16CColladaDatabase10getVersionEv",
    "accessor_linkInstanceMaterial": "_ZNK6glitch7collada16CColladaDatabase20linkInstanceMaterialEPNS0_17SInstanceMaterialE",
    "accessor_getImage_index": "_ZNK6glitch7collada16CColladaDatabase8getImageEi",
    "accessor_getMaterial_index": "_ZNK6glitch7collada16CColladaDatabase11getMaterialEi",
    "accessor_getGeometry": "_ZNK6glitch7collada16CColladaDatabase11getGeometryEi",
    "accessor_getMaterial_name": "_ZNK6glitch7collada16CColladaDatabase11getMaterialEPKc",
    "accessor_getImage_name": "_ZNK6glitch7collada16CColladaDatabase8getImageEPKc",
    "getStream_const": "_ZNK6glitch5video14CVertexStreams9getStreamENS0_18E_VERTEX_ATTRIBUTEEPKNS0_13SVertexStreamES5_",
    "getStream": "_ZN6glitch5video14CVertexStreams9getStreamENS0_18E_VERTEX_ATTRIBUTEEPNS0_13SVertexStreamES4_",
    "addOffset": "_ZN6glitch5video14CVertexStreams9addOffsetEi",
    "getPrimitiveCount": "_ZN6glitch5video17getPrimitiveCountENS0_16E_PRIMITIVE_TYPEEj",
    "CFPSCounter": "_ZN6glitch5video11CFPSCounterC2Ev",
}
MATCHED = {"MATCHED_RAW", "MATCHED_NORMALIZED_CALLS"}
STATUSES = MATCHED | {"DIFFERENT", "NOT_MEASURED"}
ALLOWED_FLAGS = {
    "-std=gnu++98", "-marm", "-march=armv7-a", "-mfpu=vfpv3-d16",
    "-mfloat-abi=softfp", "-O2", "-O3", "-fno-pic", "-fno-rtti",
    "-fno-exceptions", "-fvisibility=hidden",
}


def _digest(path):
    """Eight ordered chunks avoid publishing long opaque hex strings."""
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    return [value[i:i + 8] for i in range(0, 64, 8)]


def _metadata_only(value, field=""):
    if isinstance(value, dict):
        for key, item in value.items():
            _metadata_only(key)
            _metadata_only(item, key)
    elif isinstance(value, list):
        for item in value:
            _metadata_only(item, field)
    elif isinstance(value, str):
        assert not re.search(r"(?i)(?<![a-z0-9])[a-f0-9]{17,}(?![a-z0-9])", value)
        if "0x" in value.lower():
            assert field == "address" and re.fullmatch(r"0x[0-9a-f]{1,8}", value)


def _cache_path(value):
    path = Path(value)
    assert not path.is_absolute() and path.parts[0] == ".cache"
    assert ".." not in path.parts
    assert (ROOT / path).resolve().is_relative_to(ROOT / ".cache")
    return ROOT / path


def _assert_status(row, report):
    assert report["status"] == row["status"], (
        f"{row['function']}: recorded {row['status']}, fresh {report['status']}"
    )
    assert report["original_bytes"] == row["size"]


def _assert_argv(row):
    argv = row["gcc_argv"]
    assert argv and all(isinstance(s, str) for s in argv)
    assert argv[0] == ROWS[0]["gcc_argv"][0], "compiler differs between rows"
    assert argv[-4:] == ["-c", row["source"], "-o", row["object"]]
    flags = argv[1:-4]
    assert set(flags) <= ALLOWED_FLAGS, "unrecorded compiler flag or input"
    assert len(flags) == len(set(flags)), "duplicate compiler flag"
    assert {"-std=gnu++98", "-marm", "-mfpu=vfpv3-d16",
            "-mfloat-abi=softfp"} <= set(flags)
    assert len(set(flags) & {"-O2", "-O3"}) == 1


def _assert_source_inputs(row):
    assert _digest(ROOT / row["source"]) == row["source_sha256"]
    # Include local headers transitively: changing a layout/inline definition
    # must stale the evidence even if the wrapper .cpp itself is untouched.
    seen = set()
    pending = [ROOT / row["source"]]
    while pending:
        path = pending.pop()
        for name in re.findall(r'^\s*#include\s*"([^"\n]+)"', path.read_text(), re.M):
            header = (path.parent / name).resolve()
            assert header.is_relative_to(ROOT / "reconstruction/recovered/libNOVA3_neon")
            if header not in seen:
                seen.add(header)
                pending.append(header)
    recorded = {entry["path"]: entry["sha256"] for entry in row["headers"]}
    assert len(recorded) == len(row["headers"]), "duplicate header input"
    assert recorded == {str(path.relative_to(ROOT)): _digest(path) for path in seen}, (
        f"{row['function']}: source header inputs changed"
    )


def _assert_symbol(row, symbols):
    assert row["symbol"] == EXPECTED[row["function"]], "wrong recovered member"
    assert symbols.get(row["symbol"]) == {(int(row["address"], 16), row["size"])}, (
        f"{row['function']}: address/size/symbol disagree with .symtab"
    )


def test_matrix_metadata():
    assert DATA["schema"] == "nova3.gcc443-window-matrix/1"
    assert len(ROWS) == len(EXPECTED)
    assert {row["function"] for row in ROWS} == set(EXPECTED)
    assert {row["group"] for row in ROWS} == set(DATA["groups"])
    assert len(DATA["groups"]) == 7
    assert any(row["status"] != "NOT_MEASURED" for row in ROWS)
    assert DATA["measurement_complete"] == all(row["status"] != "NOT_MEASURED" for row in ROWS)
    assert DATA["original_toolchain_identified"] is False
    assert DATA["normalization"] == "CHECKER_A32_BRANCH_DISPLACEMENTS"
    assert _digest(ROOT / DATA["checker"]) == DATA["checker_sha256"], "window checker changed"
    _metadata_only(DATA)
    for row in ROWS:
        assert int(row["address"], 16) > 0 and row["size"] > 0
        assert row["status"] in STATUSES
        assert row["symbol"] == EXPECTED[row["function"]]
        _assert_argv(row)
        for name in ("report", "object"):
            _cache_path(row[name])
        for name, status in (("matched_raw", "MATCHED_RAW"),
                             ("matched_normalized_calls", "MATCHED_NORMALIZED_CALLS"),
                             ("different", "DIFFERENT")):
            expected = None if row["status"] == "NOT_MEASURED" else row["status"] == status
            assert row[name] is expected
        _assert_source_inputs(row)
        if row["status"] == "NOT_MEASURED":
            assert row["flags_evidence"] == "PROBE_ONLY"
            assert row["compile_exit_code"] != 0
            assert row["blocker"]


@pytest.mark.parametrize("value", [
    {"symbol": "a" * 17},
    {"gcc_argv": ["a" * 64]},
    {"report": ".cache/" + "a" * 64 + ".json"},
    {"symbol": "0" + "x1234"},
    {"address": "0" + "x" + "1" * 9},
    {"a" * 64: True},
])
def test_metadata_rejects_forbidden_strings(value):
    with pytest.raises(AssertionError):
        _metadata_only(value)


@pytest.mark.parametrize("flag", ["-std=c++17", "-mthumb", "-mfpu=neon", "-O1", "-include"])
def test_conflicting_or_extra_compiler_input_is_rejected(flag):
    row = copy.deepcopy(ROWS[0])
    row["gcc_argv"].insert(1, flag)
    with pytest.raises(AssertionError, match="unrecorded compiler flag or input"):
        _assert_argv(row)


def test_changed_header_is_rejected():
    row = copy.deepcopy(ROWS[0])
    row["headers"][0]["sha256"] = ["00000000"] * 8
    with pytest.raises(AssertionError, match="source header inputs changed"):
        _assert_source_inputs(row)


@pytest.mark.parametrize("actual", sorted(STATUSES - {"NOT_MEASURED"}))
@pytest.mark.parametrize("claim", sorted(STATUSES - {"NOT_MEASURED"}))
def test_status_gate_preserves_differences_and_match_levels(actual, claim):
    row = dict(ROWS[0], status=claim)
    report = {"status": actual, "original_bytes": row["size"]}
    if claim == actual:
        _assert_status(row, report)
    else:
        with pytest.raises(AssertionError, match="recorded .*fresh"):
            _assert_status(row, report)


@pytest.fixture(scope="module")
def reference():
    inputs = Path(os.environ.get("NOVA3_WINDOW_INPUT_ROOT", Path.home() / "Projects/nova3_re"))
    local = ROOT / DATA["reference"]
    path = Path(os.environ.get("NOVA3_RETAIL_ELF", local if local.is_file() else inputs / DATA["reference"]))
    if not path.is_file():
        pytest.skip("Premium reference ELF absent; set NOVA3_RETAIL_ELF")
    assert _digest(path) == DATA["reference_sha256"], "wrong Premium reference SHA-256"
    return path


@pytest.fixture(scope="module")
def symtab(reference):
    if not shutil.which("readelf"):
        pytest.skip("readelf absent; cannot validate exact .symtab bounds")
    result = subprocess.run(["readelf", "-sW", str(reference)], capture_output=True,
                            text=True, check=True, timeout=60,
                            env={**os.environ, "LC_ALL": "C"})
    symbols = {}
    active = False
    for line in result.stdout.splitlines():
        if line.startswith("Symbol table "):
            active = "'.symtab'" in line
        fields = line.split()
        if active and len(fields) == 8 and fields[3] == "FUNC" and fields[6] != "UND":
            symbols.setdefault(fields[7], set()).add((int(fields[1], 16), int(fields[2])))
    assert symbols, "reference .symtab is missing or empty"
    return symbols


@pytest.fixture(scope="module")
def compiler():
    inputs = Path(os.environ.get("NOVA3_WINDOW_INPUT_ROOT", Path.home() / "Projects/nova3_re"))
    relative = Path(ROWS[0]["gcc_argv"][0])
    path = ROOT / relative
    if "NOVA3_R8E_NDK" in os.environ:
        path = Path(os.environ["NOVA3_R8E_NDK"]) / Path(*relative.parts[3:])
    elif not path.is_file():
        path = inputs / relative
    if not path.is_file():
        pytest.skip("NDK r8e GCC 4.4.3 toolchain absent; set NOVA3_R8E_NDK")
    try:
        result = subprocess.run([str(path), "--version"], capture_output=True,
                                text=True, timeout=30)
    except OSError:
        pytest.skip("NDK r8e GCC 4.4.3 is not executable on this host")
    assert result.returncode == 0 and result.stdout.splitlines()[0] == DATA["compiler_version"]
    assert _digest(path) == DATA["compiler_sha256"], "GCC executable changed"
    return path


def test_missing_reference_skips(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVA3_RETAIL_ELF", str(tmp_path / "absent.so"))
    with pytest.raises(pytest.skip.Exception, match="reference ELF absent"):
        reference.__wrapped__()


def test_missing_toolchain_skips(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVA3_R8E_NDK", str(tmp_path / "absent-ndk"))
    with pytest.raises(pytest.skip.Exception, match="toolchain absent"):
        compiler.__wrapped__()


@pytest.fixture(scope="module")
def checker():
    pytest.importorskip("elftools", reason="pyelftools absent; required by window checker")
    spec = importlib.util.spec_from_file_location(
        "gcc443_window_check", ROOT / "tools/compare/gcc443_window_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measured_rows(request):
    measured = []
    yield measured
    reporter = request.config.pluginmanager.getplugin("terminalreporter")
    capture = request.config.pluginmanager.getplugin("capturemanager")
    if reporter and capture:
        # Successful test output is otherwise hidden by pytest -q capture.
        with capture.global_and_fixture_disabled():
            reporter.write_line(f"GCC 4.4.3 fresh matrix windows: {len(measured)} measured; " +
                                (", ".join(measured) if measured else "inputs unavailable"))


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["function"])
def test_symtab_bounds(row, symtab):
    # This gate runs even for NOT_MEASURED and missing precompiled objects.
    _assert_symbol(row, symtab)


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row["function"])
def test_fresh_window(row, symtab, reference, compiler, checker, tmp_path, measured_rows):
    _assert_symbol(row, symtab)
    _assert_argv(row)
    _assert_source_inputs(row)
    obj = _cache_path(row["object"])
    if not obj.is_file():
        pytest.skip(f"{row['function']}: precompiled object absent; "
                    f"{row.get('blocker', 'restore the recorded cache object')}")
    assert row["status"] != "NOT_MEASURED", "available object needs an explicit measurement"
    assert _digest(obj) == row["object_sha256"], "precompiled object changed"
    report_path = _cache_path(row["report"])
    assert report_path.is_file(), "recorded comparison report absent"
    recorded = json.loads(report_path.read_text())
    _assert_status(row, recorded)
    assert recorded["reference_sha256"] == "".join(DATA["reference_sha256"])
    assert recorded["symbol"] == row["candidate_symbol"]
    assert recorded["address"] == int(row["address"], 16)
    assert recorded["gcc_argv"] == row["gcc_argv"]
    assert recorded["source_sha256"] == row["source_sha256"]
    cached = checker.analyse(reference, obj, int(row["address"], 16), row["candidate_symbol"])
    _assert_status(row, cached)
    argv = [str(compiler), *row["gcc_argv"][1:]]
    argv[-1] = str(tmp_path / "fresh.o")
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"{row['function']}: fresh GCC compile failed"
    fresh = checker.analyse(reference, Path(argv[-1]), int(row["address"], 16), row["candidate_symbol"])
    _assert_status(row, fresh)
    assert fresh["candidate_bytes"] == cached["candidate_bytes"]
    assert fresh["candidate_window_sha256"] == cached["candidate_window_sha256"]
    measured_rows.append(f"{row['function']}={fresh['status']}")


def test_missing_precompiled_object_skips(monkeypatch, symtab, reference,
                                         compiler, checker, tmp_path):
    # Exercise the same gate without moving or deleting the user's cache.
    monkeypatch.setitem(test_fresh_window.__globals__, "_cache_path",
                        lambda value: tmp_path / "absent.o")
    with pytest.raises(pytest.skip.Exception, match="precompiled object absent"):
        test_fresh_window(ROWS[0], symtab, reference, compiler, checker, tmp_path, [])


def test_size_mutation_is_rejected(symtab):
    changed = copy.deepcopy(ROWS[0])
    changed["size"] += 4
    with pytest.raises(AssertionError, match="disagree with .symtab"):
        _assert_symbol(changed, symtab)


def test_member_substitution_is_rejected(symtab):
    # Even another real, correctly bounded .symtab function is not this member.
    changed = dict(ROWS[0])
    other = next(row for row in ROWS if row["function"] == "getTransposed")
    for field in ("symbol", "size", "address"):
        changed[field] = other[field]
    with pytest.raises(AssertionError, match="wrong recovered member"):
        _assert_symbol(changed, symtab)


@pytest.mark.parametrize("row", [row for row in ROWS if row["status"] == "NOT_MEASURED"],
                         ids=lambda row: row["function"])
def test_unmeasured_compile_blocker(row, compiler, tmp_path):
    report_path = _cache_path(row["report"])
    if not report_path.is_file():
        pytest.skip(f"{row['function']}: private unavailable-input report absent")
    report = json.loads(report_path.read_text())
    assert report["status"] == "NOT_MEASURED"
    assert report["function"] == row["function"]
    assert report["compile_exit_code"] == row["compile_exit_code"]
    assert report["blocker"] == row["blocker"]
    assert report["gcc_argv"] == row["gcc_argv"]
    argv = [str(compiler), *row["gcc_argv"][1:]]
    argv[-1] = str(tmp_path / "probe.o")
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == row["compile_exit_code"], (
        f"{row['function']}: stale compilation blocker; remeasure this candidate"
    )


@pytest.mark.parametrize("claim", sorted(MATCHED))
def test_false_match_is_rejected(claim, reference, compiler, checker, tmp_path):
    # Real compiler negative control: O2 rolls the transpose instead of O3's
    # unrolled form. Test both false promotions against a recomputed report.
    row = next(row for row in ROWS if row["function"] == "getTransposed")
    if not _cache_path(row["object"]).is_file():
        pytest.skip("transpose precompiled object absent; cannot run compiler negative control")
    argv = [str(compiler), *row["gcc_argv"][1:]]
    argv[argv.index("-O3")] = "-O2"
    argv[-1] = str(tmp_path / "negative.o")
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, "transpose negative-control compilation failed"
    fresh = checker.analyse(reference, Path(argv[-1]), int(row["address"], 16), row["candidate_symbol"])
    assert fresh["status"] == "DIFFERENT"
    changed = dict(row, status=claim)
    with pytest.raises(AssertionError, match="fresh DIFFERENT"):
        _assert_status(changed, fresh)
