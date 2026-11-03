"""Synthetic ARM32 fixtures only; no original ELF, APK, OBB, or NDK required."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace

import pytest

from tools.compare import gcc443_seven_window as seven


ORIGINAL = struct.pack("<II", 0xEB00000C, 0xE12FFF1E)
PLACEHOLDER = struct.pack("<II", 0xEBFFFFFE, 0xE12FFF1E)


def elf32(code=ORIGINAL, *, relocatable=False, symbol="sample", extra_symbols=()):
    """Minimal, independently constructed ELF32 ARM fixture with a real symtab."""
    names = b"\0.text\0.strtab\0.symtab\0.shstrtab\0"
    address = 0 if relocatable else 0x1000
    strings = b"\0" + symbol.encode() + b"\0"
    symbols = bytes(16) + struct.pack("<IIIBBH", 1, address, len(code), 0x12, 0, 1)
    for name, value, size, info, section in extra_symbols:
        symbols += struct.pack("<IIIBBH", len(strings), value, size, info, 0, section)
        strings += name.encode() + b"\0"
    sections = [
        (".text", 1, 6, address, code, 0, 0, 4, 0),
        (".strtab", 3, 0, 0, strings, 0, 0, 1, 0),
        (".symtab", 2, 0, 0, symbols, 2, 1, 4, 16),
        (".shstrtab", 3, 0, 0, names, 0, 0, 1, 0),
    ]
    data = bytearray(52)
    headers = [(0,) * 10]
    for name, kind, flags, va, content, link, info, align, entsize in sections:
        data.extend(bytes(-len(data) % align))
        offset = len(data)
        data.extend(content)
        headers.append((names.index(name.encode() + b"\0"), kind, flags, va,
                        offset, len(content), link, info, align, entsize))
    data.extend(bytes(-len(data) % 4))
    shoff = len(data)
    for header in headers:
        data.extend(struct.pack("<10I", *header))
    data[:52] = (b"\x7fELF\x01\x01\x01" + bytes(9)
                 + struct.pack("<HHIIIIIHHHHHH", 1 if relocatable else 3, 40, 1,
                               0, 0, shoff, 0x05000000, 52, 0, 0, 40, 5, 4))
    return bytes(data)


# Pinned independently of the harness's hashing: fixture layout changes require
# deliberate review, rather than calculating the expected digest from input.
FIXTURE_SHA256 = "f7f89bc54db5069f5c8d7e3cf8453d26a95d0a519ad8f0450d7fe0e44e4710b8"


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    reference = tmp_path / "reference.elf"
    reference.write_bytes(elf32())
    compiler = tmp_path / "fake-g++"
    compiler.write_bytes(b"synthetic compiler identity\n")
    source = tmp_path / "sample.cpp"
    source.write_text("// synthetic source identity\n")
    targets = tuple(seven.Target(f"sample-{i}", 0x1000, "sample", Path("sample.cpp"),
                                 "sample", ("-O3",), "synthetic fixture only")
                    for i in range(7))
    compiled = []
    candidate = [elf32(relocatable=True)]

    def compile_once(gcc, src, flags, out, includes):
        assert gcc == compiler
        assert src == source
        assert flags == ["-O3"]
        assert includes == [source.parent]
        compiled.append(out.name)
        out.write_bytes(candidate[0])
        return True, ""

    monkeypatch.setattr(seven, "compile_object", compile_once)
    monkeypatch.setattr(seven.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout="synthetic GCC 4.4.3\n", returncode=0))

    def run(*, rows=targets, work="objects", expected_sha256=FIXTURE_SHA256):
        workdir = tmp_path / work
        workdir.mkdir(exist_ok=True)
        return seven.run_report(reference, compiler, workdir, root=tmp_path,
                                targets=rows, expected_sha256=expected_sha256)

    return SimpleNamespace(run=run, targets=targets, compiled=compiled,
                           candidate=candidate, source=source, reference=reference,
                           compiler=compiler, root=tmp_path)


@pytest.mark.parametrize("code,status", [
    (ORIGINAL, "MATCHED_RAW"),
    (PLACEHOLDER, "MATCHED_NORMALIZED_CALLS"),
    (struct.pack("<II", 0xEBFFFFFE, 0xE1A00000), "DIFFERENT"),
    (ORIGINAL + struct.pack("<I", 0xE1A00000), "DIFFERENT"),
])
def test_fixed_sha_real_elf_windows(experiment, code, status):
    assert hashlib.sha256(experiment.reference.read_bytes()).hexdigest() == FIXTURE_SHA256
    experiment.candidate[0] = elf32(code, relocatable=True)
    report = experiment.run()
    assert report["reference_sha256"] == FIXTURE_SHA256
    assert len(report["rows"]) == 7
    assert experiment.compiled == [f"{i:02d}.o" for i in range(7)]
    for row in report["rows"]:
        assert row["status"] == status
        assert row["address"] == 0x1000
        assert row["symbol"] == "sample"
        assert row["flags"] == ["-O3"]
        assert row["original_bytes"] == 8
        assert row["candidate_bytes"] == len(code)
        assert row["source_sha256"] == hashlib.sha256(experiment.source.read_bytes()).hexdigest()
        if status.startswith("MATCHED"):
            assert row["call_sites"][0]["arm_link_formula_reproduces_original_call"] is True
    assert seven.exit_code(report) == 0  # DIFFERENT is still a completed check.


def test_deterministic_reports_and_metadata_only(experiment):
    experiment.candidate[0] = elf32(PLACEHOLDER, relocatable=True)
    first = experiment.run(work="first")
    second = experiment.run(work="second")

    def normalize(report):
        report = json.loads(json.dumps(report))
        report.pop("reference")
        report.pop("timestamp", None)
        return report

    assert normalize(first) == normalize(second)
    assert len(experiment.compiled) == 14
    text = json.dumps(first, sort_keys=True)
    for forbidden in (ORIGINAL.hex(), PLACEHOLDER.hex(), ".text", "eb00000c",
                      "e12fff1e", '"instructions"', '"disassembly"', '"hexdump"'):
        assert forbidden not in text
    for row in first["rows"]:
        for key, value in row.items():
            if key.endswith("_bytes"):
                assert isinstance(value, int)
            if key.endswith("sha256"):
                assert len(value) == 64
                int(value, 16)
        for call in row["call_sites"]:
            assert set(call) == {"address", "relocatable_placeholder_target", "linked_target",
                                 "arm_link_formula_reproduces_original_call", "caller_aligned_4"}


@pytest.mark.parametrize("changes,error", [
    ({"symbol": "absent"}, "reference symbol absent"),
    ({"address": 0x1004}, "resolved symbol address"),
    ({"source": Path("missing.cpp")}, "source missing"),
    ({"source": None}, "source missing"),
    ({"flags": None}, "measured size-matching flags absent"),
    ({"candidate_symbol": "absent"}, "candidate symbol absent"),
    ({"symbol": None, "address": None}, "single function not selected"),
])
def test_errors_are_rows_never_skips(experiment, changes, error):
    rows = (replace(experiment.targets[0], **changes), *experiment.targets[1:])
    report = experiment.run(rows=rows)
    assert len(report["rows"]) == 7
    bad, *rest = report["rows"]
    assert bad["status"] == "ERROR"
    assert error in bad["error"]
    assert all(row["status"] == "MATCHED_RAW" for row in rest)
    assert seven.exit_code(report) == 1
    if changes.get("symbol", "sample") in (None, "absent"):
        assert bad["address"] is None
    else:
        assert bad["address"] == 0x1000  # resolved STT_FUNC, not the table's value
    assert len(experiment.compiled) == (7 if changes.get("candidate_symbol") == "absent" else 6)


def test_failed_compile_does_not_compare_stale_object(experiment, monkeypatch):
    work = experiment.root / "stale"
    work.mkdir()
    for i in range(7):
        (work / f"{i:02d}.o").write_bytes(elf32(relocatable=True))
    attempted = []

    def fail(gcc, source, flags, out, includes):
        assert not out.exists()
        attempted.append(out.name)
        return False, "compiler failure"

    monkeypatch.setattr(seven, "compile_object", fail)
    report = experiment.run(work="stale")
    assert len(attempted) == 7
    assert all(r["status"] == "ERROR" and r["candidate_bytes"] is None for r in report["rows"])


@pytest.mark.parametrize("value,size,info,section,error", [
    (0x1001, 8, 0x12, 1, "invalid A32 bounds"),  # Thumb st_value
    (0x1000, 0, 0x12, 1, "invalid A32 bounds"),
    (0x1000, 12, 0x12, 1, "invalid A32 bounds"),  # crosses section end
    (0x1000, 8, 0x12, 99, "no section"),
    (0x1000, 8, 0x12, 0, "no section"),
    (0x1000, 8, 0x11, 1, "reference symbol absent"),  # STT_OBJECT
])
def test_invalid_reference_symbol_keeps_seven_rows(
        experiment, value, size, info, section, error):
    reference = elf32(extra_symbols=(("broken", value, size, info, section),))
    experiment.reference.write_bytes(reference)
    rows = (replace(experiment.targets[0], symbol="broken", address=value),
            *experiment.targets[1:])
    report = experiment.run(rows=rows, expected_sha256=hashlib.sha256(reference).hexdigest())
    bad, *rest = report["rows"]
    assert len(report["rows"]) == 7
    assert bad["status"] == "ERROR" and error in bad["error"]
    assert bad["address"] == (None if info == 0x11 else value)
    assert all(r["status"] == "MATCHED_RAW" for r in rest)
    assert experiment.compiled == [f"{i:02d}.o" for i in range(1, 7)]
    assert seven.exit_code(report) == 1


def test_ambiguous_reference_symbol_is_an_error_row(experiment):
    reference = elf32(extra_symbols=(("sample", 0x1000, 8, 0x12, 1),))
    experiment.reference.write_bytes(reference)
    report = experiment.run(expected_sha256=hashlib.sha256(reference).hexdigest())
    assert len(report["rows"]) == 7
    assert all(r["status"] == "ERROR" and "ambiguous" in r["error"]
               and r["address"] is None for r in report["rows"])
    assert not experiment.compiled
    assert seven.exit_code(report) == 1


@pytest.mark.parametrize("missing", [True, False])
def test_absent_or_wrong_type_symtab_is_seven_error_rows(experiment, missing):
    reference = bytearray(elf32())
    shoff = struct.unpack_from("<I", reference, 32)[0]
    if missing:
        # Rename .symtab to .strtab without altering any function data.
        struct.pack_into("<I", reference, shoff + 3 * 40, 7)
    else:
        struct.pack_into("<I", reference, shoff + 3 * 40 + 4, 11)  # SHT_DYNSYM
    experiment.reference.write_bytes(reference)
    report = experiment.run(expected_sha256=hashlib.sha256(reference).hexdigest())
    assert len(report["rows"]) == 7
    assert all(r["status"] == "ERROR" and "reference symbol absent" in r["error"]
               for r in report["rows"])
    assert not experiment.compiled
    assert seven.exit_code(report) == 1


@pytest.mark.parametrize("failure", ["compile", "compare"])
def test_exceptional_diagnostics_do_not_leak_input_or_discard_rows(experiment, monkeypatch, failure):
    private_diagnostic = "input excerpt: " + ORIGINAL.hex()

    def fail(*args, **kwargs):
        if failure == "compile":
            raise subprocess.TimeoutExpired("compiler", 1, output=private_diagnostic)
        raise seven.ELFError(private_diagnostic)

    if failure == "compile":
        monkeypatch.setattr(seven, "compile_object", fail)
    else:
        monkeypatch.setattr(seven.window_check, "analyse", fail)
    report = experiment.run()
    assert len(report["rows"]) == 7
    assert all(r["status"] == "ERROR" for r in report["rows"])
    assert ORIGINAL.hex() not in json.dumps(report)
    assert private_diagnostic not in json.dumps(report)
    assert seven.exit_code(report) == 1


def test_reference_hash_mismatch_compiles_nothing(experiment):
    with pytest.raises(ValueError, match="reference SHA-256 mismatch"):
        experiment.run(expected_sha256="0" * 64)
    assert not experiment.compiled


@pytest.mark.parametrize("version", ["", "\n", "synthetic GCC 4.4.30\n",
                                     "synthetic GCC 14.4.3\n", "clang version 4.4.3\n"])
def test_wrong_or_empty_compiler_version_compiles_nothing(experiment, monkeypatch, version):
    monkeypatch.setattr(seven.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(stdout=version, returncode=0))
    with pytest.raises(ValueError, match="compiler is not GCC 4.4.3"):
        experiment.run()
    assert not experiment.compiled


@pytest.mark.parametrize("word", [0xE1A00000, 0xEA00000C, 0xFA00000C, 0xE12FFF1E])
def test_candidate_call_cannot_certify_original_non_call(experiment, word):
    reference = elf32(struct.pack("<II", word, 0xE12FFF1E))
    experiment.reference.write_bytes(reference)
    experiment.candidate[0] = elf32(PLACEHOLDER, relocatable=True)
    report = experiment.run(expected_sha256=hashlib.sha256(reference).hexdigest())
    assert len(experiment.compiled) == 7
    for row in report["rows"]:
        assert row["status"] == "DIFFERENT"
        call, = row["call_sites"]
        assert call["linked_target"] is None
        assert call["arm_link_formula_reproduces_original_call"] is False
    assert seven.exit_code(report) == 0


def test_candidate_thumb_mode_is_an_error(experiment):
    obj = bytearray(elf32(relocatable=True))
    # The sole defined symbol's st_value, found through the section directory.
    shoff = struct.unpack_from("<I", obj, 32)[0]
    symtab_offset = struct.unpack_from("<I", obj, shoff + 3 * 40 + 16)[0]
    struct.pack_into("<I", obj, symtab_offset + 16 + 4, 1)
    experiment.candidate[0] = bytes(obj)
    report = experiment.run()
    assert len(experiment.compiled) == 7
    assert all(r["status"] == "ERROR" and "ARM/Thumb" in r["error"] for r in report["rows"])


def test_address_alias_cannot_silently_change_comparison_window(experiment, monkeypatch):
    real_analyse = seven.window_check.analyse

    def wrong_window(*args):
        measured = real_analyse(*args)
        measured["original_window_sha256"] = "0" * 64
        return measured

    monkeypatch.setattr(seven.window_check, "analyse", wrong_window)
    report = experiment.run()
    assert all(r["status"] == "ERROR" and "different reference window" in r["error"]
               for r in report["rows"])


def test_normalization_does_not_hide_local_branch_or_blx_changes(experiment):
    # Existing gcc443_window_check masks B as well as BL; the harness narrows it.
    reference = elf32(struct.pack("<II", 0xEA00000C, 0xE12FFF1E))
    experiment.reference.write_bytes(reference)
    experiment.candidate[0] = elf32(struct.pack("<II", 0xEAFFFFFE, 0xE12FFF1E),
                                  relocatable=True)
    report = experiment.run(expected_sha256=hashlib.sha256(reference).hexdigest())
    assert all(row["status"] == "DIFFERENT" for row in report["rows"])
    assert not seven._only_call_displacements_differ(
        struct.pack("<I", 0xFA000001), struct.pack("<I", 0xFA000002))


def test_table_retains_missing_evidence_instead_of_guessing_flags():
    assert len(seven.TARGETS) == 7
    assert len({t.name for t in seven.TARGETS}) == 7
    known = [t for t in seven.TARGETS if t.flags is not None]
    assert [t.name for t in known] == ["getTransposed"]
    assert "-O3" in known[0].flags
    assert "-fvisibility=hidden" in known[0].flags


def test_absent_default_toolchain_is_nonzero_without_partial_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(seven, "ROOT", tmp_path)
    out = tmp_path / ".cache" / "seven.json"
    assert seven.main(["--out", str(out)]) == 2
    assert "toolchain absent" in capsys.readouterr().err
    assert not out.exists()


def test_cli_writes_seven_rows_and_propagates_errors(experiment, monkeypatch):
    monkeypatch.setattr(seven, "ROOT", experiment.root)
    compiler = experiment.root / seven.NDK / seven.GXX
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(experiment.compiler.read_bytes())
    report = experiment.run()
    report["rows"][0].update(status="ERROR", error="synthetic missing recipe")
    monkeypatch.setattr(seven, "run_report", lambda *args, **kwargs: report)
    out = experiment.root / ".cache" / "seven.json"
    assert seven.main(["--out", str(out)]) == 1
    assert json.loads(out.read_text()) == report
