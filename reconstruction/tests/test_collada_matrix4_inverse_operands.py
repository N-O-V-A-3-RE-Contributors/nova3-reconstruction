"""Offline signature tests plus a guarded live GCC 4.4.3/ELF comparison.

The in-memory tests use source-owned operand dictionaries only: no reference
bytes, no compiler and no Capstone objects.  The live test is skipped with a
named reason when the local read-only Premium ELF, the NDK r8e compiler or the
recovered getInverse source is absent.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/compare/collada_matrix4_inverse_operands.py"
spec = importlib.util.spec_from_file_location("inverse_operands", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def _record(index: int, mnemonic: str, *operands: dict) -> dict:
    return {
        "index": index,
        "address": f"0x{index * 4:08x}",
        "mnemonic": mnemonic,
        "operands": list(operands),
    }


def _assert_metadata_only(report: dict) -> None:
    """Allowlist the schema so a future byte/disassembly dump fails the test."""
    assert set(report) == {
        "schema", "state", "matching", "source_matching_verified",
        "reference_sha256", "symbol", "address", "size", "size_source", "mode",
        "section", "data_ranges", "instruction_counts", "class_counts",
        "candidate", "vfp_arithmetic_counts", "signatures",
        "operand_multiset_delta", "first_signature_divergence", "scope",
    }
    assert report["schema"] == tool.SCHEMA
    assert report["state"] == "INVENTORIED"
    assert report["matching"] == "NOT MATCHED"
    assert report["source_matching_verified"] is False
    assert set(report["candidate"]) == {
        "source", "source_sha256", "compiler", "flags", "symbol", "size",
        "size_source", "mode", "section", "data_ranges", "relocations",
    }
    assert set(report["class_counts"]) == {"reference", "candidate", "delta"}
    assert set(report["instruction_counts"]) == {"reference", "candidate"}
    assert set(report["vfp_arithmetic_counts"]) == {"reference", "candidate"}
    assert set(report["signatures"]) == {"reference", "candidate"}
    assert set(report["operand_multiset_delta"]) == {
        "reference_total", "candidate_total", "rows"}
    for key in ("data_ranges",):
        for row in report[key]:
            assert set(row) == {"address", "size"}
    for row in report["candidate"]["data_ranges"]:
        assert set(row) == {"address", "size"}
    for row in report["operand_multiset_delta"]["rows"]:
        assert set(row) == {
            "opcode", "destination", "sources", "immediate", "memory",
            "source_origins", "reference_count", "candidate_count", "delta"}
    signature_keys = {
        "index", "opcode", "destination", "sources", "immediate", "memory",
        "source_origins"}
    for side in ("reference", "candidate"):
        for signature in report["signatures"][side]:
            assert set(signature) == signature_keys
            for mem in signature["memory"]:
                assert set(mem) == {"base", "offset"}
            for origin in signature["source_origins"]:
                assert set(origin) == {"register", "memory"}
                if origin["memory"] is not None:
                    assert set(origin["memory"]) == {"base", "offset"}
    divergence = report["first_signature_divergence"]
    if divergence is not None:
        assert set(divergence) == {
            "alignment", "index", "reference_opcode", "candidate_opcode",
            "reference", "candidate"}

    forbidden = {"bytes", "bytearray", "instruction_bytes", "disassembly",
                 "op_str", "mnemonic", "word", "words"}

    def visit(value) -> None:
        assert not isinstance(value, (bytes, bytearray))
        if isinstance(value, dict):
            for key, child in value.items():
                assert key not in forbidden
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(report)
    assert json.loads(json.dumps(report)) == report


def test_signature_builder_and_conditional_normalisation():
    sig = tool.build_signature(
        5, "vmul.f32",
        [tool.register("s1"), tool.register("s2"), tool.register("s18")])
    assert sig == {
        "index": 5,
        "opcode": "vmul.f32",
        "destination": "s1",
        "sources": ["s2", "s18"],
        "immediate": [],
        "memory": [],
        "source_origins": [
            {"register": "s2", "memory": None},
            {"register": "s18", "memory": None},
        ],
    }
    assert tool.signature_key(sig) == (
        "vmul.f32", "s1", ("s2", "s18"), (), (), (("s2", None, None),
                                                   ("s18", None, None)))
    # Context is not part of the multiset key.
    moved = dict(sig, index=99)
    assert tool.signature_key(moved) == tool.signature_key(sig)

    assert tool.canonical_opcode("vmuleq.f32") == "vmul.f32"
    assert tool.canonical_opcode("vnmls.f32") == "vnmls.f32"
    assert tool.canonical_opcode("vmls.f32") == "vmls.f32"
    assert tool.canonical_opcode("vadd.f64") == "vadd.f64"
    assert tool.canonical_opcode("vldr") is None
    assert tool.canonical_opcode("vdiv.f64") is None
    assert tool.canonical_opcode("vcvt.f64.f32") is None

    with_origin = tool.build_signature(
        5, "vmul.f32",
        [tool.register("s1"), tool.register("s2"), tool.register("s18")],
        {"s2": {"base": "r0", "offset": 56},
         "s18": {"base": "r0", "offset": 44}})
    assert with_origin["source_origins"] == [
        {"register": "s2", "memory": {"base": "r0", "offset": 56}},
        {"register": "s18", "memory": {"base": "r0", "offset": 44}},
    ]

    with_memory = tool.build_signature(
        6, "vmla.f32",
        [tool.register("s0"), tool.memory("r0", 44), tool.immediate(1)])
    assert with_memory["destination"] == "s0"
    assert with_memory["sources"] == []
    assert with_memory["immediate"] == [1]
    assert with_memory["memory"] == [{"base": "r0", "offset": 44}]
    assert with_memory["source_origins"] == []

    # Linear provenance: a vldr origin feeds the next multiply, then a later
    # arithmetic destination invalidates only that register.
    stream = [
        _record(0, "vldr", tool.register("s2"), tool.memory("r0", 56)),
        _record(1, "vldr", tool.register("s18"), tool.memory("r0", 44)),
        _record(2, "vmul.f32", tool.register("s1"), tool.register("s2"),
                tool.register("s18")),
        _record(3, "vmul.f32", tool.register("s2"), tool.register("s18"),
                tool.register("s1")),
    ]
    tracked = tool.compare_instruction_streams(stream, stream)
    assert tracked["reference_signatures"][0]["source_origins"] == [
        {"register": "s2", "memory": {"base": "r0", "offset": 56}},
        {"register": "s18", "memory": {"base": "r0", "offset": 44}},
    ]
    assert tracked["reference_signatures"][1]["source_origins"] == [
        {"register": "s18", "memory": {"base": "r0", "offset": 44}},
        {"register": "s1", "memory": None},
    ]


def test_operand_multiset_delta_counts_duplicates():
    vmul = tool.build_signature(
        0, "vmul.f32",
        [tool.register("s1"), tool.register("s2"), tool.register("s3")])
    other_vmul = tool.build_signature(
        1, "vmul.f32",
        [tool.register("s1"), tool.register("s2"), tool.register("s3")])
    vnmls = tool.build_signature(
        2, "vnmls.f32",
        [tool.register("s4"), tool.register("s5"), tool.register("s6")])
    vmla = tool.build_signature(
        0, "vmla.f32",
        [tool.register("s4"), tool.register("s5"), tool.register("s6")])
    delta = tool.operand_multiset_delta([vmul, other_vmul, vnmls], [vmla])
    assert delta["reference_total"] == 3
    assert delta["candidate_total"] == 1
    rows = {(row["opcode"], row["destination"]): row for row in delta["rows"]}
    assert rows[("vmul.f32", "s1")]["reference_count"] == 2
    assert rows[("vmul.f32", "s1")]["candidate_count"] == 0
    assert rows[("vmul.f32", "s1")]["delta"] == -2
    assert rows[("vnmls.f32", "s4")]["delta"] == -1
    assert rows[("vmla.f32", "s4")]["delta"] == 1
    # Equal-count signatures are absent from the delta.
    assert len(delta["rows"]) == 3


def test_compare_streams_reports_first_index_divergence():
    reference = [
        _record(0, "vldr", tool.register("s1"), tool.memory("r0", 4)),
        _record(1, "vmul.f32", tool.register("s1"), tool.register("s2"),
                tool.register("s3")),
        _record(2, "vnmls.f32", tool.register("s1"), tool.register("s4"),
                tool.register("s5")),
    ]
    candidate = [
        _record(0, "vldr", tool.register("s1"), tool.memory("r0", 4)),
        _record(1, "vmul.f32", tool.register("s1"), tool.register("s2"),
                tool.register("s3")),
        _record(2, "vmls.f32", tool.register("s1"), tool.register("s4"),
                tool.register("s5")),
    ]
    result = tool.compare_instruction_streams(reference, candidate)
    assert result["vfp_arithmetic_counts"] == {"reference": 2, "candidate": 2}
    divergence = result["first_signature_divergence"]
    assert divergence["index"] == 2
    assert divergence["reference_opcode"] == "vnmls.f32"
    assert divergence["candidate_opcode"] == "vmls.f32"
    assert divergence["reference"]["opcode"] == "vnmls.f32"
    assert divergence["candidate"]["opcode"] == "vmls.f32"

    # A shifted arithmetic instruction is a divergence at the raw window index.
    candidate[1] = _record(1, "vldr", tool.register("s1"),
                           tool.memory("r0", 8))
    result = tool.compare_instruction_streams(reference, candidate)
    assert result["first_signature_divergence"]["index"] == 1
    assert result["first_signature_divergence"]["candidate"] is None


def _first_file(candidates: list[Path]) -> Path | None:
    return next((path for path in candidates if path.is_file()), None)


def _local_paths() -> tuple[tuple[Path, Path, Path] | None, list[str]]:
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
        Path("/home/jason/Projects/nova3_re/reconstruction/recovered/"
             "libNOVA3_neon/collada_matrix4_inverse.cpp"),
    ])
    missing = []
    if reference is None:
        missing.append("Premium reference ELF")
    if gcc is None:
        missing.append("NDK r8e GCC 4.4.3")
    if source is None:
        missing.append("recovered getInverse source")
    if missing:
        return None, missing
    return (reference, gcc, source), []


def test_live_reference_candidate_operand_stream():
    pytest.importorskip("capstone", reason="live run needs capstone")
    pytest.importorskip("elftools", reason="live run needs pyelftools")
    paths, missing = _local_paths()
    if paths is None:
        pytest.skip("live ELF/compiler run skipped: absent " + ", ".join(missing))
    reference, gcc, source = paths
    report = tool.build_report(reference, gcc, source=source)
    _assert_metadata_only(report)

    assert report["reference_sha256"] == tool.REFERENCE_SHA256
    assert report["symbol"] == tool.SYMBOL
    assert report["address"] == "0x000ebb08"
    assert report["size"] == 776
    assert report["size_source"] == ".symtab"
    assert report["mode"] == "ARM"
    assert report["instruction_counts"] == {"reference": 194, "candidate": 194}
    assert report["class_counts"]["reference"]["vmul"] == 34
    assert report["class_counts"]["candidate"]["vmul"] == 38
    assert report["class_counts"]["reference"]["vfp_other"] == 19
    assert report["class_counts"]["candidate"]["vfp_other"] == 15
    assert report["operand_multiset_delta"]["reference_total"] == 79
    assert report["operand_multiset_delta"]["candidate_total"] == 79
    assert report["first_signature_divergence"] is not None
    assert report["first_signature_divergence"]["index"] == 5
