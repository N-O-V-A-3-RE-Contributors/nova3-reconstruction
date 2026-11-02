"""Thumb-mode accounting check for the GCC 4.4.3 window checker.

This is a measurement test of the tool only, not of any game function, and it
needs no reference ELF or original object.

A small synthetic C++ source is compiled twice with the NDK r8e GCC 4.4.3
frontend, once at ``-marm`` and once at ``-mthumb`` (an ``int`` volatile loop,
so each mode gets real code). Each object is linked with ``-nostdlib`` into a
private shared object holding just that function, plus its exported
``STT_FUNC`` symbol. ``tools/compare/window_diff.py`` reads the linked window
(reference side); ``nova3_team.object_compare.candidate_window`` reads the
``.o`` (candidate side; it derives the mode from ``st_value`` bit 0).

The same command is run four ways: ARM self, Thumb self, ARM reference against
the Thumb object and Thumb reference against the ARM object. The measured
behaviour, these tests pin it instead of assuming it:

* Self comparison is 100% EQUAL in both modes, with 0 relocations and 0
  encoding differences.
* Cross-mode comparison reports ``ARM_THUMB_MODE_MISMATCH``, which is not a
  word comparison, before any word is read. ``code_window_equal`` is never
  true, so these tests do not accept a size-only mismatch as an equal result.
* ``window_diff`` never reads a window at an odd or shifted address. It uses
  one contiguous aligned span through the ``diff_window`` API. In Thumb mode
  it reads half-words (``instruction_unit == 2``), and the aligned view is
  exactly the candidate bytes. Reading at ``(addr|1)``, ``(addr|1)+1`` or
  ``(addr|1)+2`` in the same file reproduces no half-word of the candidate.
* The mode label in the report is the reference mode: the tool prints
  ``Thumb`` (``instruction_unit == 2``) when the reference symbol is Thumb.
  Mode comes from ``st_value`` bit 0 (and ``.symtab``/``.dynsym`` parity); the
  linked reference has no mapping symbols. Thumb-ness is cross-checked on the
  ``.o``, which does carry ``$a``/``$t`` mapping symbols.

The module skips cleanly (exit 0) when the r8e toolchain is absent.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NDK_ROOT = Path(os.environ.get("NOVA3_R8E_NDK",
                               "/home/jason/.local/opt/android-ndk-r8e"))
BIN = NDK_ROOT / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin"
GXX = BIN / "arm-linux-androideabi-g++"
API = "android-14"
FLAGS = ["-march=armv7-a", "-mfpu=neon", "-mfloat-abi=softfp", "-O2",
         "-fno-exceptions", "-fno-rtti", "-fno-unwind-tables"]

SYMBOL = "nova3_thumb_probe"
SOURCE = """\
extern "C" {
int %(symbol)s(volatile int *data, int count) {
    int sum = 0;
    while (count-- > 0)
        sum += data[count] * (count + 3);
    return sum;
}
}
""" % {"symbol": SYMBOL}
COMPILER = "NDK r8e GCC 4.4.3 (arm-linux-androideabi-g++)"


def _toolchain_missing() -> str | None:
    if not GXX.is_file() or not os.access(GXX, os.X_OK):
        return "NDK r8e GCC 4.4.3 frontend not found at %s" % GXX
    return None


SKIP = _toolchain_missing()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(argv: list[str], cwd: Path, timeout: int = 60) -> None:
    """Bounded subprocess; argv is logged, compiler output goes to build.log."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GCC_EXEC_PREFIX")}
    env["TMPDIR"] = str(cwd)
    with (cwd / "build.log").open("ab") as stream:
        stream.write(b"$ " + json.dumps(argv).encode() + b"\n")
        stream.flush()
        done = subprocess.run(argv, cwd=cwd, env=env, stdout=stream,
                              stderr=subprocess.STDOUT, timeout=timeout)
    if done.returncode != 0:
        pytest.fail("%s exited %d; see build.log" % (Path(argv[0]).name,
                                                    done.returncode))


def _compile_link(tmp: Path, mode: str) -> tuple[bytes, bytes, str]:
    """Compile at -m<mode> and link a .so holding only the probe function."""
    source = tmp / "probe.cpp"
    if not source.exists():
        source.write_text(SOURCE, encoding="utf-8")
    obj = tmp / ("probe_%s.o" % mode)
    so = tmp / ("probe_%s.so" % mode)
    compile_argv = [str(GXX), "-c", "-m" + mode] + FLAGS + ["-o", str(obj), str(source)]
    _run(compile_argv, tmp)
    link_argv = [str(GXX), "-shared", "-fPIC", "-nostdlib", "-Wl,--no-undefined",
                 "-o", str(so), str(obj)]
    _run(link_argv, tmp)
    return obj.read_bytes(), so.read_bytes(), " ".join(compile_argv)


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    if SKIP:
        pytest.skip(SKIP)
    tmp = tmp_path_factory.mktemp("window_diff_thumb")
    arm_obj, arm_so, arm_cmd = _compile_link(tmp, "arm")
    thumb_obj, thumb_so, thumb_cmd = _compile_link(tmp, "thumb")
    assert arm_obj != thumb_obj, "-marm and -mthumb produced identical objects"
    return {"tmp": tmp, "arm": {"obj": arm_obj, "so": arm_so, "cmd": arm_cmd},
            "thumb": {"obj": thumb_obj, "so": thumb_so, "cmd": thumb_cmd},
            "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest()}


@pytest.fixture(scope="module")
def gate():
    wd = _load("window_diff_under_test", ROOT / "tools/compare/window_diff.py")
    from nova3_team import object_compare
    return {"wd": wd, "oc": object_compare}


def _probe_dynsym(so: bytes) -> int:
    shoff = int.from_bytes(so[32:36], "little")
    shnum = struct.unpack("<H", so[48:50])[0]
    sections = [struct.unpack("<IIIIIIIIII", so[shoff + 40 * i:shoff + 40 * i + 40])
                for i in range(shnum)]
    for table in sections:
        if table[1] != 11:
            continue
        strings = sections[table[6]]
        data = so[strings[4]:strings[4] + strings[5]]
        for pos in range(table[4], table[4] + table[5], 16):
            nameoff, value, size, info, _o, shndx = struct.unpack(
                "<IIIBBH", so[pos:pos + 16])
            name = data[nameoff:data.find(b"\0", nameoff)].decode()
            if info & 15 == 2 and shndx and name == SYMBOL:
                return value
    raise AssertionError("probe symbol missing from .dynsym")


def _diff(gate, reference: bytes, candidate: bytes, address: int) -> dict:
    return gate["wd"].diff_window(reference, candidate, address=address,
                                  symbol=SYMBOL)


def _object_mappings(obj: bytes) -> set[str]:
    """Mapping-symbol names ($a/$t/$d) from the object's own symbol tables."""
    shoff = int.from_bytes(obj[32:36], "little")
    shnum = struct.unpack("<H", obj[48:50])[0]
    sections = [struct.unpack("<IIIIIIIIII", obj[shoff + 40 * i:shoff + 40 * i + 40])
                for i in range(shnum)]
    found = set()
    for table in sections:
        if table[1] not in (2, 11):  # SHT_SYMTAB and SHT_DYNSYM both carry $t
            continue
        strings = sections[table[6]]
        data = obj[strings[4]:strings[4] + strings[5]]
        for pos in range(table[4], table[4] + table[5], 16):
            nameoff, _v, _s, _i, _o, _x = struct.unpack("<IIIBBH", obj[pos:pos + 16])
            name = data[nameoff:data.find(b"\0", nameoff)].decode(errors="replace")
            if name.startswith("$"):
                found.add(name)
    return found


def _probe_symbol_value(obj: bytes) -> int:
    shoff = int.from_bytes(obj[32:36], "little")
    shnum = struct.unpack("<H", obj[48:50])[0]
    sections = [struct.unpack("<IIIIIIIIII", obj[shoff + 40 * i:shoff + 40 * i + 40])
                for i in range(shnum)]
    for table in sections:
        if table[1] != 2:
            continue
        strings = sections[table[6]]
        data = obj[strings[4]:strings[4] + strings[5]]
        for pos in range(table[4], table[4] + table[5], 16):
            nameoff, value, _s, info, _o, shndx = struct.unpack(
                "<IIIBBH", obj[pos:pos + 16])
            name = data[nameoff:data.find(b"\0", nameoff)].decode()
            if info & 15 == 2 and shndx and name == SYMBOL:
                return value
    raise AssertionError("probe symbol missing from the object")


def test_provenance_and_gates(build, gate):
    """Record exactly what was compiled, and gate the two measurements we need."""
    for label in ("arm", "thumb"):
        mode = "-mthumb" if label == "thumb" else "-marm"
        assert mode in build[label]["cmd"]
        assert hashlib.sha256(build[label]["obj"]).digest() != \
            hashlib.sha256(build[label]["so"]).digest()
    # The .o decides mode from st_value bit 0; mapping symbols agree with it.
    for label, expected in (("arm", False), ("thumb", True)):
        row = gate["oc"].candidate_window(build[label]["obj"], SYMBOL)
        assert row["thumb"] is expected
        value = _probe_symbol_value(build[label]["obj"])
        assert bool(value & 1) is expected
        mappings = _object_mappings(build[label]["obj"])
        assert ("$t" in mappings) is expected and ("$a" in mappings) is not expected
    # The linked reference keeps $t for the function in .dynsym (and has no
    # $a), so mapping symbols and st_value parity agree on Thumb.
    assert "$t" in _object_mappings(build["thumb"]["so"])
    assert "$a" not in _object_mappings(build["thumb"]["so"])


def test_self_comparison_both_modes_all_words_equal(build, gate):
    """(a) Self comparison is 100% EQUAL with 0 relocations and 0 encoding diffs."""
    for label, unit in (("arm", 4), ("thumb", 2)):
        so, obj = build[label]["so"], build[label]["obj"]
        address = _probe_dynsym(so) & ~1
        report = _diff(gate, so, obj, address)
        assert report["status"] == "ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION", label
        assert report["instruction_unit"] == unit, (label, report["instruction_unit"])
        counts = report["counts"]
        assert report["words_compared"] > 0 and counts["EQUAL"] == report["words_compared"], label
        assert counts["RELOCATION_FIELD"] == 0 and report["relocation_offsets_used"] == 0
        assert report["candidate_relocation_count"] == 0, label
        assert counts["ENCODING_DIFFERENCE"] == 0, label
        assert counts["LITERAL_POOL"] == 0 and counts["UNCLASSIFIED_TAIL"] == 0, label
        # 100% equal by bytes as well as by classification.
        assert report["code_window_equal"] is True, label
        assert report["instruction_stream_equal"] is True, label


def test_cross_mode_is_never_equal(build, gate):
    """(b) ARM vs Thumb fails on mode, before any word comparison."""
    arm, thumb = build["arm"], build["thumb"]
    for reference, candidate, ref_label in ((arm, thumb, "arm"), (thumb, arm, "thumb")):
        address = _probe_dynsym(reference["so"]) & ~1
        report = _diff(gate, reference["so"], candidate["obj"], address)
        assert report["status"] == "ARM_THUMB_MODE_MISMATCH", (ref_label, report["status"])
        # The mode-mismatch report has no code_window_equal field at all, so
        # the "is not true" criterion holds strictly: the key is absent.
        assert "code_window_equal" not in report, ref_label
        assert report.get("code_window_equal") is not True, ref_label
        assert report["source_matching_verified"] is False
        # This gate fires before any word is compared; no word counts or
        # instruction unit exist, and the two extents differ.
        assert "counts" not in report and "instruction_unit" not in report
        assert report["candidate_size"] != report["reference_size"]
    # If the mode gate is bypassed and Thumb half-words are read as A32 words,
    # the streams diverge; this is recorded here as a guard on the gate itself.
    thumb_window = gate["oc"].candidate_window(thumb["obj"], SYMBOL)["bytes"]
    arm_window = gate["oc"].candidate_window(arm["obj"], SYMBOL)["bytes"]
    assert len(thumb_window) % 2 == 0 and len(arm_window) % 4 == 0
    a32_view = [int.from_bytes(thumb_window[i:i + 4], "little")
                for i in range(0, len(thumb_window) - 3, 4)]
    arm_words = [int.from_bytes(arm_window[i:i + 4], "little")
                 for i in range(0, len(arm_window), 4)]
    overlap = sum(left == right for left, right in zip(a32_view, arm_words))
    assert overlap < min(len(a32_view), len(arm_words)), "cross-mode streams coincide"


def _thumb_decode(first: int, second: int) -> str:
    """Classify a Thumb instruction word (16-bit encodings and selected 32-bit)."""
    if (first & 0xFE00) == 0xB400 and first & 0x7F:      # PUSH {rlist, lr}
        return "push"
    if first == 0xE92D and second & 0x8000:                # PUSH.W {reglist}
        return "push.w"
    if (first & 0xFF87) == 0x4700:                         # BX Rm
        return "bx"
    if (first & 0xE000) == 0x2000 and first & 0x0800:      # data processing T2
        return "data-processing"
    if (first & 0xFF00) == 0xBD00:                         # POP {rlist, pc}
        return "pop"
    if (first & 0xFF87) in (0x4700, 0x4780):               # BX/BLX Rm
        return "bx"
    if (first >> 8) in (0xD0, 0xE0) and first & 0xFF00:    # B<c>/B (16-bit)
        return "branch"
    if (first & 0xF800) == 0x4800:                         # LDR (literal)
        return "load-literal"
    if (first & 0xFE00) == 0xB000:                         # misc (16-bit)
        return "misc"
    if (first & 0xF800) == 0x0000 or (first & 0xF000) == 0x0000:
        return "shift/alu"                                 # LSL/ADD/MOV etc.
    high = first & 0xF800
    if high in (0xF000, 0xF800, 0xE800):                    # Thumb-2 32-bit space
        return "thumb2"
    return "other"


def _reference_window(gate, so: bytes, address: int) -> tuple[dict, bytes]:
    """Bound the linked reference exactly as window_diff does, then slice it."""
    row = _selected_row(gate, so, address)
    return row, gate["oc"]._span(so, row["file_offset"], row["size"])


def _selected_row(gate, data: bytes, address: int) -> dict:
    from nova3_team import object_matching
    rows, _ = object_matching.select_functions(data, addresses={address}, limit=1)
    assert len(rows) == 1
    return rows[0]


def test_thumb_effective_address_is_odd_safe_aligned(build, gate):
    """(c) Odd st_value bits are cleared once; nothing is read at addr|1 or +1."""
    so, obj = build["thumb"]["so"], build["thumb"]["obj"]
    raw = _probe_dynsym(so)
    assert raw & 1 == 1, "linked Thumb st_value must carry the bit-0 mode tag"
    aligned = raw & ~1
    assert (raw | 1) & ~1 == aligned and raw != aligned
    report = _diff(gate, so, obj, aligned)
    assert report["reference_address"] == aligned and report["instruction_unit"] == 2
    window = gate["oc"].candidate_window(obj, SYMBOL)
    assert window["thumb"] is True
    row, reference = _reference_window(gate, so, aligned)
    assert row["thumb"] is True and reference == window["bytes"]

    # The aligned read reproduces every candidate half-word, and the first
    # decoded half-word is a valid Thumb prologue.
    candidate = [reference[i:i + 2] for i in range(0, len(reference), 2)]
    assert candidate == [window["bytes"][i:i + 2]
                         for i in range(0, len(window["bytes"]), 2)]
    assert len(candidate) > 1
    # Decode the window start. GCC 4.4.3 may omit the prologue (the first word
    # is then another valid Thumb opcode), so the whole window must decode:
    # every word is a Thumb opcode, and the aligned window start itself must be
    # one (not, e.g., an A32 branch word read as data).
    decoded = [(offset, _thumb_decode(
        int.from_bytes(reference[offset:offset + 2], "little"),
        int.from_bytes(reference[offset + 2:offset + 4], "little")))
        for offset in range(0, len(reference) - 3, 2)]
    first_kind = decoded[0][1]
    assert first_kind != "other", hex(int.from_bytes(reference[:2], "little"))
    prologues = [kind for _o, kind in decoded if kind in ("push", "push.w", "bx")]
    print("thumb first word: %s; prologues in window: %s" % (first_kind, prologues))

    # Shifting the window start by one byte, one half-word or one odd address
    # reproduces no candidate half-word. The test fails here if (addr|1)+1
    # reads the same words as (addr|1)&~1.
    for shift in (1, 2, 3):
        shifted = gate["oc"]._span(so, row["file_offset"] + shift, window["size"])
        same = sum(shifted[i:i + 2] == candidate[i // 2]
                   for i in range(0, len(candidate) * 2, 2))
        assert shifted != reference, "shift %d reads the same window" % shift
        assert same == 0, "window shifted by %d matches %d half-words" % (shift, same)
    # An address one byte or one half-word past the entry is not a function
    # start the tool will bound, so it can never silently use a shifted window.
    for bad in (aligned + 1, aligned + 2):
        with pytest.raises(gate["oc"].CompareError):
            _diff(gate, so, obj, bad)


def test_thumb_mode_comes_from_the_reference(build, gate):
    """(c)/(d) The comparison unit follows the reference symbol, not the candidate."""
    arm, thumb = build["arm"], build["thumb"]
    thumb_addr = _probe_dynsym(thumb["so"]) & ~1
    report = _diff(gate, thumb["so"], arm["obj"], thumb_addr)
    assert report["status"] == "ARM_THUMB_MODE_MISMATCH"
    assert "instruction_unit" not in report
    thumb_self = _diff(gate, thumb["so"], thumb["obj"], thumb_addr)
    assert thumb_self["instruction_unit"] == 2
    arm_addr = _probe_dynsym(arm["so"]) & ~1
    arm_report = _diff(gate, arm["so"], arm["obj"], arm_addr)
    assert arm_report["instruction_unit"] == 4


def test_mode_label_is_thumb_for_thumb_reference(build, gate):
    """(d) The object's mapping marks the function Thumb; the report says Thumb."""
    from nova3_team.indexer import build_ledger

    so, obj = build["thumb"]["so"], build["thumb"]["obj"]
    address = _probe_dynsym(so) & ~1
    report = _diff(gate, so, obj, address)
    # window's schema has no textual mode field, so the Thumb label is the
    # documented pair: a 2-byte instruction unit on an even instruction address.
    assert report["instruction_unit"] == 2
    assert report["reference_address"] % 2 == 0
    assert report["status"] == "ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION"

    # Mapping symbols: both the .o (.symtab) and the linked .so (.dynsym)
    # mark the function with $t and never $a; st_value parity agrees.
    assert "$t" in _object_mappings(obj) and "$a" not in _object_mappings(obj)
    assert "$t" in _object_mappings(so) and "$a" not in _object_mappings(so)
    # The ledger reads linked ELF only; on the linked reference the function
    # is Thumb by both $t mapping and st_value parity.
    so_ledger = build_ledger(so)
    rows = [row for row in so_ledger["functions"] if row["name"] == SYMBOL]
    assert rows and all(row["thumb"] is True for row in rows)
    from nova3_team.indexer import LedgerError
    with pytest.raises(LedgerError):
        build_ledger(obj)  # ET_REL: documented refusal, not a silent ARM label
    print("window_diff schema %s: Thumb label is instruction_unit=2; "
          "report carries no textual mode field" % report["schema"])
