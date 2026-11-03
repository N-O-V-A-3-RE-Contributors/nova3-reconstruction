"""Offline dependent-run tests plus a guarded live getInverse section.

The offline tests use source-owned operand dictionaries only: no reference
bytes, no compiler, no Capstone objects and no pyelftools.  The live test runs
the existing operands pilot (which decodes the local Premium ELF and compiles
the recovered source), feeds its already-parsed records to the new module and
is skipped with a named reason when the reference ELF, NDK r8e GCC 4.4.3 or
the recovered getInverse source is absent.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/compare/collada_inverse_arith_runs.py"
PILOT = ROOT / "tools/compare/collada_matrix4_inverse_operands.py"
FORBIDDEN_TOP_LEVEL = {"elftools", "capstone"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load("inverse_arith_runs", TOOL)


def _record(index: int, mnemonic: str, *registers: str) -> dict:
    """One operand record in the already-defined shape (registers only)."""
    return {
        "index": index,
        "address": f"0x{index * 4:08x}",
        "mnemonic": mnemonic,
        "operands": [{"kind": "register", "register": name}
                     for name in registers],
    }


def _side(result: dict, name: str = "reference") -> dict:
    section = result["arithmetic_runs"] if "arithmetic_runs" in result else result
    return section[name]


def _section(result: dict) -> dict:
    return result["arithmetic_runs"] if "arithmetic_runs" in result else result


# ---------------------------------------------------------------------------
# Offline, pure
# ---------------------------------------------------------------------------


def test_import_needs_no_capstone_or_pyelftools():
    """A fresh interpreter imports the module with neither decoder loaded."""
    script = (
        "import importlib.util, sys\n"
        "sys.dont_write_bytecode = True\n"
        f"spec = importlib.util.spec_from_file_location('m', r'{TOOL}')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "print(sorted(k for k in dir(module) if not k.startswith('_')))\n"
        "print([name for name in ('capstone', 'elftools') if name in sys.modules])\n"
        "print(callable(module.run))\n"
    )
    done = subprocess.run([sys.executable, "-B", "-c", script],
                          capture_output=True, text=True, check=False,
                          timeout=120)
    assert done.returncode == 0, done.stderr
    names, loaded, callable_run = done.stdout.strip().splitlines()
    assert "'run'" in names
    assert "capstone" not in names and "elftools" not in names
    assert loaded == "[]", "importing must not pull in a decoder"
    assert callable_run == "True"


def test_module_has_no_decoder_import_at_top_level():
    source = TOOL.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert not stripped.split()[1].split(".")[0].startswith(
                ("elftools", "capstone")), stripped
    assert not hasattr(tool, "capstone")
    assert not hasattr(tool, "ELFFile")


def test_dependent_fixture_versus_non_dependent_multiplies():
    """mul->dependent add on one side; the same multiply alone on the other."""
    multiply = _record(0, "vmul.f32", "s0", "s1", "s2")
    dependent_add = _record(1, "vadd.f32", "s0", "s0", "s3")
    reference = [multiply, dependent_add,
                 _record(2, "vmul.f32", "s4", "s5", "s6"),
                 _record(3, "vsub.f32", "s4", "s4", "s7")]
    candidate = [multiply,
                 _record(1, "vmul.f32", "s10", "s8", "s9")]
    result = _section(tool.run({"reference": reference,
                                "candidate": candidate}))
    tool.validate_section(result)
    assert result["matching"] == "NOT MATCHED"
    assert result["source_matching_verified"] is False
    assert result["schema"] == tool.SCHEMA

    ref, cand = result["reference"], result["candidate"]
    assert ref["arithmetic_records"] == 4
    assert ref["runs"] == 2
    assert ref["dependent_links"] == 2
    assert ref["run_length_histogram"] == {"2 instructions": 2}
    assert cand["arithmetic_records"] == 2
    assert cand["runs"] == 2
    assert cand["dependent_links"] == 0
    assert cand["run_length_histogram"] == {"1 instruction": 2}
    assert ref["run_length_histogram"] != cand["run_length_histogram"]

    # A multiply whose result is (separately) added where it was written is
    # the one shape that yields a 2-run; the same multiply with an add that
    # does not depend on it does not.  vmla is counted as a multiply record.
    mul = _record(0, "vmul.f32", "s0", "s1", "s2")
    unrelated_add = _record(1, "vadd.f32", "s4", "s5", "s6")
    dependent = tool.run({"reference": [mul, dependent_add],
                          "candidate": [mul, unrelated_add]})
    assert dependent["reference"]["run_length_histogram"] == {
        "2 instructions": 1}
    assert dependent["reference"][
        "multiplies_not_followed_by_dependent_add_sub"] == 0
    assert dependent["candidate"]["run_length_histogram"] == {
        "1 instruction": 2}
    assert dependent["candidate"][
        "multiplies_not_followed_by_dependent_add_sub"] == 1


def test_independent_multiplies_are_runs_of_length_one_and_dangling():
    records = [
        _record(0, "vmul.f32", "s0", "s1", "s2"),
        _record(1, "vmul.f32", "s4", "s5", "s6"),
        _record(2, "vmul.f32", "s8", "s9", "s10"),
    ]
    result = _section(tool.run({"reference": records, "candidate": []}))
    side = result["reference"]
    assert side["run_length_histogram"] == {"1 instruction": 3}
    assert side["runs"] == 3
    assert side["dependent_links"] == 0
    assert side["longest_run"] == 1
    assert side["multiply_records"] == 3
    assert side["multiplies_not_followed_by_dependent_add_sub"] == 3
    assert result["candidate"]["records"] == 0
    assert result["candidate"]["arithmetic_records"] == 0
    assert result["candidate"]["run_length_histogram"] == {}
    assert result["candidate"]["longest_run"] == 0
    # Histogram rows are the union of both sides with signed deltas.
    rows = {row["length"]: row for row in
            result["histogram_delta"]["run_length"]}
    assert rows["1 instruction"] == {"length": "1 instruction",
                                     "reference": 3, "candidate": 0,
                                     "delta": -3}


def test_multiply_followed_by_independent_add_is_dangling():
    records = [
        _record(0, "vmul.f32", "s0", "s1", "s2"),
        _record(1, "vadd.f32", "s4", "s5", "s6"),   # reads nothing s0
        _record(2, "vmul.f32", "s7", "s8", "s9"),
        _record(3, "vsub.f32", "s7", "s7", "s10"),  # dependent subtract
        _record(4, "vmul.f32", "s11", "s12", "s13"),
        _record(5, "vmul.f32", "s14", "s15", "s16"),  # not an add/subtract
    ]
    side = _section(tool.run({"reference": records,
                              "candidate": records}))["reference"]
    # Only (2,3) links: record 1 reads nothing of record 0's s0, and record 5
    # reads its own s14, which record 4 neither wrote nor named.
    assert side["runs"] == 5
    assert side["run_length_histogram"] == {"2 instructions": 1,
                                            "1 instruction": 4}
    assert side["dependent_links"] == 1
    # Records 0, 4 and 5 are each followed by a record that does not depend on
    # them (the stream end for record 5); record 2 is the one dependent pair.
    assert side["multiplies_not_followed_by_dependent_add_sub"] == 3
    assert side["multiply_records"] == 4
    assert side["add_subtract_records"] == 2


def test_accumulator_chain_is_a_single_run():
    """A carried accumulator stays data-connected and turns no multiply loose."""
    records = [
        _record(0, "vmul.f32", "s0", "s1", "s2"),
        _record(1, "vadd.f32", "s0", "s0", "s3"),
        _record(2, "vadd.f32", "s0", "s0", "s4"),
        _record(3, "vmla.f32", "s0", "s5", "s6"),  # reads s0, writes s0
        _record(4, "vmla.f32", "s8", "s0", "s1"),
    ]
    side = _section(tool.run({"reference": records,
                              "candidate": []}))["reference"]
    assert side["run_length_histogram"] == {"5 instructions": 1}
    assert side["dependent_links"] == 4
    assert side["multiply_records"] == 3
    assert side["add_subtract_records"] == 2
    # Each of the three multiplies folds into what follows: the vmul into the
    # dependent vadd, the first vmla into the vadd that carries s0, and the
    # last vmla writes s8 after reading the accumulating s0.
    assert side["multiplies_not_followed_by_dependent_add_sub"] == 0

    # vmla x2 does not link: the second writes s0, not s4, so only a shared
    # source register could connect them, and neither names the other's write.
    fused = [_record(0, "vmla.f32", "s0", "s1", "s2"),
             _record(1, "vmla.f32", "s4", "s5", "s6"),
             _record(2, "vmls.f32", "s4", "s5", "s6")]
    fused_side = _section(tool.run({"reference": fused,
                                    "candidate": []}))["reference"]
    assert fused_side["run_length_histogram"] == {"1 instruction": 1,
                                                  "2 instructions": 1}
    assert fused_side["accumulate_records"] == 3
    assert fused_side["multiply_records"] == 3


def test_non_arithmetic_and_destination_only_records_are_counted():
    records = [
        _record(0, "vmul.f32", "s0", "s1", "s2"),
        _record(1, "vldr", "s3", "r0"),          # unsupported, not arithmetic
        _record(2, "vdiv.f32", "s3", "s4", "s5"),  # divide: not in the table
        _record(3, "vmla.f32", "s0"),             # destination only
        _record(4, "vadd.f32", "s6"),             # destination only
        _record(5, "vadd.f32", "s0", "s0", "s1"),  # still depends on record 0
        {"index": 6},                              # missing mnemonic
        "not a record",                            # not a dict
        {"index": 7, "mnemonic": None, "operands": []},
    ]
    side = _section(tool.run({"reference": records,
                              "candidate": records}))["reference"]
    assert side["records"] == len(records)
    assert side["arithmetic_records"] == 2
    assert side["runs"] == 1
    assert side["run_length_histogram"] == {"2 instructions": 1}
    assert side["ignored_records"] == 7
    assert side["ignored_reasons"] == {
        "not_a_record": 1,
        "missing_mnemonic": 2,       # no mnemonic key, and a null mnemonic
        "unsupported_mnemonic": 2,   # vldr, vdiv
        "no_source_register": 2,     # destination only
    }
    assert sum(side["ignored_reasons"].values()) == side["ignored_records"]


def test_run_is_pure_over_plain_inputs():
    import copy

    reference = [_record(0, "vmul.f32", "s0", "s1", "s2"),
                 _record(1, "vadd.f32", "s0", "s0", "s3")]
    candidate = [_record(0, "vmla.f32", "s0", "s1", "s2")]
    data = {"reference": reference, "candidate": candidate}
    frozen = copy.deepcopy(data)
    first = tool.run(data)
    second = tool.run(data)
    assert first == second
    assert data == frozen, "run must not mutate its inputs"
    assert first is not second
    dumped = json.loads(json.dumps(first))
    assert dumped == first
    tool.validate_section(first)

    with pytest.raises(tool.ArithRunsError):
        tool.run({"reference": []})
    with pytest.raises(tool.ArithRunsError):
        tool.run({"reference": "vmul", "candidate": []})
    with pytest.raises(tool.ArithRunsError):
        tool.run({"reference": None, "candidate": []})

    # The validator would reject a fabricated match claim or byte content.
    forged = json.loads(json.dumps(first))
    forged["matching"] = "MATCHED_RAW"
    with pytest.raises(tool.ArithRunsError):
        tool.validate_section(forged)
    forged = json.loads(json.dumps(first))
    forged["reference"]["extra"] = b"\x00"
    with pytest.raises(tool.ArithRunsError):
        tool.validate_section(forged)


def test_cli_refuses_output_outside_cache(tmp_path):
    reference = tmp_path / "reference.json"
    candidate = tmp_path / "candidate.json"
    records = [_record(0, "vmul.f32", "s0", "s1", "s2")]
    reference.write_text(json.dumps(records), encoding="utf-8")
    candidate.write_text(json.dumps(records), encoding="utf-8")
    done = tool.main(["--reference", str(reference),
                      "--candidate", str(candidate),
                      "--out", str(tmp_path / "out.json")])
    assert done == 1
    assert not (tmp_path / "out.json").exists()


# ---------------------------------------------------------------------------
# Guarded live run through the existing operands pilot
# ---------------------------------------------------------------------------


def _first_file(candidates: list[Path]) -> Path | None:
    return next((path for path in candidates if path.is_file()), None)


def _local_paths() -> tuple[Path, Path, Path] | None:
    reference = _first_file([
        ROOT / "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so",
        Path("/home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/"
             "lib/armeabi-v7a/libNOVA3_neon.so"),
    ])
    gcc = _first_file([
        ROOT / ".cache/toolchains/android-ndk-r8e/toolchains/"
               "arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/"
               "arm-linux-androideabi-g++",
        Path("/home/jason/Projects/nova3_re/.cache/toolchains/android-ndk-r8e/"
             "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/"
             "arm-linux-androideabi-g++"),
    ])
    source = _first_file([
        ROOT / "reconstruction/recovered/libNOVA3_neon/"
               "collada_matrix4_inverse.cpp",
    ])
    missing = []
    if reference is None:
        missing.append("Premium reference ELF")
    if gcc is None:
        missing.append("NDK r8e GCC 4.4.3")
    if source is None:
        missing.append("recovered getInverse source")
    if missing:
        pytest.skip("live arithmetic-run section skipped: absent "
                    + ", ".join(missing))
    return reference, gcc, source


@pytest.fixture(scope="module")
def live_report() -> dict:
    paths = _local_paths()
    assert paths is not None
    pytest.importorskip("capstone", reason="live run needs capstone")
    pytest.importorskip("elftools", reason="live run needs pyelftools")
    reference, gcc, source = paths
    pilot = _load("inverse_operands_for_arith_runs", PILOT)
    return pilot.build_report(reference, gcc, source=source)


def _records(signatures: list[dict]) -> list[dict]:
    """Convert the pilot's signature rows back to the operand-record shape."""
    records = []
    for signature in signatures:
        registers = [signature["destination"], *signature["sources"]]
        records.append({
            "index": signature["index"],
            "address": "",
            "mnemonic": signature["opcode"],
            "operands": [{"kind": "register", "register": name}
                         for name in registers if name is not None],
        })
    return records


def test_live_arithmetic_runs_section(live_report):
    reported = live_report["signatures"]
    result = _section(tool.run({
        "reference": _records(reported["reference"]),
        "candidate": _records(reported["candidate"]),
    }))
    # Stand-in for the odr and an independent section: exact keys, metadata
    # only, and never a match claim.
    tool.validate_section(result)
    assert result["matching"] == "NOT MATCHED"
    assert result["source_matching_verified"] is False
    assert live_report["matching"] == "NOT MATCHED"
    assert live_report["source_matching_verified"] is False

    for side in ("reference", "candidate"):
        summary = result[side]
        assert isinstance(summary, dict) and summary
        for value in summary.values():
            assert isinstance(value, (int, list, dict)), value
        assert summary["arithmetic_records"] == len(reported[side])
        assert summary["arithmetic_records"] == (
            live_report["vfp_arithmetic_counts"][side])
        assert sum(summary["run_length_histogram"].values()) == summary["runs"]
        assert sum(summary["family_counts"].values()) == \
            summary["arithmetic_records"]
        assert summary["multiply_records"] == (
            summary["family_counts"].get("vmul", 0)
            + summary["family_counts"].get("vnmul", 0)
            + summary["family_counts"].get("vmla", 0)
            + summary["family_counts"].get("vmls", 0)
            + summary["family_counts"].get("vnmla", 0)
            + summary["family_counts"].get("vnmls", 0))

    # Operand-split motivation: the candidate's fused multiplies leave more
    # loosely grouped arithmetic than the reference's separate multiplies.
    reference_runs = result["reference"]["runs"]
    candidate_runs = result["candidate"]["runs"]
    assert isinstance(reference_runs, int) and reference_runs > 0
    assert reference_runs + result["delta"]["runs"] == candidate_runs
    assert result["delta"]["dependent_links"] == (
        result["candidate"]["dependent_links"]
        - result["reference"]["dependent_links"])
