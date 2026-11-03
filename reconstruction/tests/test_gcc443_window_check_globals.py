"""Driver-number adversary for the GCC 4.4.3 window checker (task #170).

This is a measurement of ``tools/compare/gcc443_window_check.py``, which reads
reference windows from linked ELF and compares them with
``window_diff.diff_window`` (that module's per-word engine). It is not a claim
about any game function and needs no reference ELF or original object.

Property under measurement
--------------------------
A window's per-word classification must depend on the function's own
instruction words and its own relocations, never on how many *other* globals
share the candidate translation unit. Extra globals can move a global's
literal-pool slot, its linked address or its GOT slot without changing the
instruction the compiler selected.

What is built (NDK r8e GCC 4.4.3, path from tools/setup/fetch_gcc443.sh)
------------------------------------------------------------------------
Four targets, each measured at ``-O2`` and ``-O3``:

* two GOT-literal targets: in-window ``ldr rX,[pc,#..]`` literal words that
  hold ``.word sym`` GOT offsets (``R_ARM_GOTPC`` base plus ``R_ARM_GOT32``
  entries) and ``.word .rodata`` relative addresses (``R_ARM_REL32``);
* two jump-table targets: dense ``switch`` whose case bodies are non-uniform,
  so 4.4.3 emits an in-window ``addls pc,pc,rX,lsl #2`` dispatch through
  ``b`` words (a GOT-literal witness is also required from these targets).

For each target the module runs two adversaries with ``N`` = 0, 1 and 8 dummy
globals:

1. LINK_LAYOUT (reference side). The candidate ``.o`` stays the same; the
   object is linked with ``-nostdlib`` while ``N`` dummy globals and an exported
   string object precede it on the link line. The linked ``.data`` moves from
   ``0x1000`` to ``0x2000``, the linked ``.text`` moves from file offset 0x34
   to 0x4f4, and the linked window really changes.
2. DRIVER (candidate side). The source itself gains ``N`` dummy globals and is
   recompiled; each variant's own ``.o`` is linked and compared. Only extra
   literal words and their relocation fields may appear.

Gates, per target:
* the EQUAL count, the relocation-field count (RELOCATION_FIELD words plus
  object relocations inside the window) and ENCODING_DIFFERENCE are the same
  at 0, 1 and 8 dummies; the candidate function extent is the same too;
* a change in ``code_window_equal`` is accepted only when LITERAL_POOL words
  account for it (a word that is neither EQUAL nor LITERAL_POOL explains it);
* classification of the words that exist in every variant (by window offset)
  never changes.

A violation is printed as a per-dummy count table (counts and word-offset
kinds only, never instruction bytes) and fails the test. Every comparison
must report a non-empty per-word result.

All compiler products go under pytest ``tmp_path``. The module skips with a
clear reason (exit 0) when the r8e g++ is missing or not GCC 4.4.3.
"""
from __future__ import annotations

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

# tools/setup/fetch_gcc443.sh installs into <project>/.cache/toolchains and
# reuses $NOVA3_R8E_NDK or ~/.local/opt/android-ndk-r8e if already present.
NDK_VERSION = "android-ndk-r8e"
GXX_SUFFIX = Path("toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/"
                  "bin/arm-linux-androideabi-g++")
# NOVA3_R8E_NDK=none (or "missing") simulates an absent toolchain.
ENV_NDK = os.environ.get("NOVA3_R8E_NDK", "")
UNAVAILABLE = ENV_NDK.strip().lower() in ("none", "missing", "0")
BASES = [] if UNAVAILABLE else [base for base in (ENV_NDK,
                           str(Path.home() / ".local" / "opt" / NDK_VERSION),
                           str(ROOT / ".cache" / "toolchains" / NDK_VERSION))
         if base]
GXX = next((Path(base) / GXX_SUFFIX for base in BASES if (Path(base) / GXX_SUFFIX).is_file()),
           Path(BASES[-1]) / GXX_SUFFIX if BASES else GXX_SUFFIX)

ABI = ["-march=armv7-a", "-mfpu=vfpv3-d16", "-mfloat-abi=softfp", "-fPIC",
       "-fno-exceptions", "-fno-rtti", "-fno-unwind-tables",
       "-fno-asynchronous-unwind-tables"]
OPT_LEVELS = ("-O2", "-O3")
DUMMIES = (0, 1, 8)
UNIT = 4
GOT_RELOCS = {3, 25, 43, 96, 97}   # R_ARM_REL32, ABS32, GOT32, GOT_PREL, GOTPC
JUMP_TABLE_RELOCS = {2, 3}         # R_ARM_ABS32, R_ARM_REL32
# Every message about the measured result carries this exact phrase.
SCOPE = "measurement tool property"

GOT_LITERAL_BODY = """\
extern "C" {
int nova3_g_weight = 7;
int nova3_g_table[4] = {3, 5, 9, 17};
float nova3_g_scale = 1.5f;
double nova3_g_wide = 2.25;
const int nova3_g_boxes[4] = {4, 8, 16, 32};
int nova3_g_flags(int x);
int %(symbol_C)s(int x) {
    int (*fn)(int) = nova3_g_flags;
    return fn(x) + x * 3 + nova3_g_weight + nova3_g_table[x & 3] + (int)(x * nova3_g_scale)
         + (int)(x * nova3_g_wide) + nova3_g_boxes[(x >> 2) & 3] + ((x > 4) ? 11 : 0);
}
}
"""

JUMP_TABLE_BODY = """\
extern "C" {
int nova3_g_weight = 7;
unsigned nova3_g_mask = 0xf0000001u;
int nova3_g_flags(int x);
int %(symbol_C)s(int x) {
    switch (x) {
    case 0: return x + 3;
    case 1: return x * nova3_g_weight;
    case 2: return x ^ 0x5a;
    case 3: return nova3_g_flags(x);
    case 4: return x - 9;
    case 5: return (x << 2) + 1;
    case 6: return x * x;
    case 7: return x | 0x10;
    case 8: return x >> 1;
    case 9: return x - nova3_g_weight;
    case 10: return x & nova3_g_mask;
    case 11: return x + 4096;
    default: return -1;
    }
}
}
"""

DUMMY_DEF = "int nova3_dummy_global_%d = %d;\n"

# N extra translation-unit globals, defined ahead of the candidate's .data.
DUMMY_OBJECT_ASM = """\
    .text
    .global nova3_force_export
    .type nova3_force_export, %object
nova3_force_export:
    .ascii "x"
    .size nova3_force_export, 1
    .data
    .align 2
"""


def _dummy_assembly(count: int) -> str:
    body = "".join(
        "    .global nova3_dummy_global_%d\n    .type nova3_dummy_global_%d, %%object\n"
        "nova3_dummy_global_%d:\n    .word %d\n    .size nova3_dummy_global_%d, 4\n"
        % (i, i, i, 1000 + i, i) for i in range(count))
    return DUMMY_OBJECT_ASM + body


def _source(body: str, values: dict, dummies: int) -> str:
    # The symbols are declared inside extern "C" already, so 4.4.3 emits the
    # bare name; object_compare refuses to infer Itanium-mangled C++ names.
    return body.replace("%(symbol_C)s", values["symbol_C"]) + "".join(
        DUMMY_DEF % (i, 100 + i) for i in range(dummies))


TARGETS = [
    dict(kind="GOT_LITERAL", name="got_plus", body=GOT_LITERAL_BODY, witness=True),
    dict(kind="GOT_LITERAL", name="got_scale", body=GOT_LITERAL_BODY, witness=True),
    dict(kind="JUMP_TABLE", name="switch_dense", body=JUMP_TABLE_BODY, witness=True),
    dict(kind="JUMP_TABLE", name="switch_mask", body=JUMP_TABLE_BODY, witness=True),
]


def _toolchain_status() -> str | None:
    if not GXX.is_file() or not os.access(GXX, os.X_OK):
        checked = (", ".join(BASES) + ", " + str(GXX)) if BASES else \
            "NOVA3_R8E_NDK marks the toolchain unavailable"
        return ("NDK r8e GCC 4.4.3 g++ from tools/setup/fetch_gcc443.sh is not "
                "installed (checked %s); nothing was compiled" % checked)
    try:
        done = subprocess.run([str(GXX), "--version"], capture_output=True,
                              text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return "NDK r8e g++ at %s is not runnable: %s" % (GXX, exc)
    if done.returncode != 0 or "4.4.3" not in done.stdout:
        first = (done.stdout or done.stderr or "").splitlines()[:1]
        return "NDK r8e g++ at %s did not report GCC 4.4.3: %r" % (GXX, first)
    return None


SKIP = _toolchain_status()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(argv: list[str], cwd: Path) -> None:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GCC_EXEC_PREFIX")}
    env["TMPDIR"] = str(cwd)
    with (cwd / "build.log").open("ab") as stream:
        stream.write(b"$ " + json.dumps(argv).encode() + b"\n")
        stream.flush()
        done = subprocess.run(argv, cwd=str(cwd), env=env, stdout=stream,
                              stderr=subprocess.STDOUT, timeout=60)
    assert done.returncode == 0, "%s exited %d; see build.log" % (
        Path(argv[0]).name, done.returncode)


def _compile(tmp: Path, source: Path, obj: Path, opt: str) -> None:
    _run([str(GXX), "-c", opt] + ABI + ["-o", str(obj), str(source)], tmp)


def _assemble(tmp: Path, assembly: str, obj: Path) -> None:
    """Assemble with the r8e driver; no C headers, so _GLOBAL_OFFSET_TABLE_ is a constant."""
    source = tmp / (obj.stem + ".s")
    source.write_text(assembly, encoding="utf-8")
    _run([str(GXX), "-x", "assembler", "-c", ABI[0], "-mthumb-interwork",
          "-o", str(obj), str(source)], tmp)


def _link(tmp: Path, obj: Path, out: Path, tag: str, linked_dummies: int = 0) -> None:
    """Link ``obj``; ``linked_dummies`` extra globals are placed before its .data.

    DRIVER passes 0 because the dummy globals are already in ``obj``;
    LINK_LAYOUT passes N and the object itself stays the n=0 build.
    """
    extra: list[str] = []
    if linked_dummies:
        # Defined first on the link line so its .text/.data place the window;
        # the string export gives the link one output section and dynamic
        # symbol table even though everything is -fPIC.
        extra_obj = tmp / ("extra_%s.o" % tag)
        _assemble(tmp, _dummy_assembly(linked_dummies), extra_obj)
        extra = [str(extra_obj)]
    _run([str(GXX), "-shared", "-nostdlib", "-o", str(out)] + extra + [str(obj)], tmp)


# --- minimal ELF32 readers: names, sizes and counts only -------------------

def _sections(data: bytes) -> list[tuple]:
    assert data[:7] == b"\x7fELF\x01\x01\x01", "not little-endian ELF32"
    shoff = struct.unpack_from("<I", data, 32)[0]
    shnum = struct.unpack_from("<H", data, 48)[0]
    return [struct.unpack_from("<IIIIIIIIII", data, shoff + 40 * i) for i in range(shnum)]


def _symbols(data: bytes, table_type: int = 2) -> list[dict]:
    sections = _sections(data)
    rows = []
    for sec in sections:
        if sec[1] != table_type:
            continue
        strings = sections[sec[6]]
        names = data[strings[4]:strings[4] + strings[5]]
        for pos in range(sec[4], sec[4] + sec[5], 16):
            nameoff, value, size, info, _o, shndx = struct.unpack_from("<IIIBBH", data, pos)
            end = names.find(b"\0", nameoff)
            rows.append(dict(name=names[nameoff:end].decode(errors="replace"),
                             value=value, size=size, info=info, shndx=shndx))
    return rows


def _relocations(data: bytes) -> list[dict]:
    sections = _sections(data)
    rows = []
    for sec in sections:
        if sec[1] not in (4, 9):  # SHT_RELA, SHT_REL
            continue
        for pos in range(sec[4], sec[4] + sec[5], sec[9] or 8):
            offset, info = struct.unpack_from("<II", data, pos)
            rows.append(dict(offset=offset, rtype=info & 0xFF))
    return rows


def _symbol(data: bytes, symbol: str, table_type: int = 2) -> dict | None:
    for row in _symbols(data, table_type):
        if row["name"] == symbol and row["info"] & 15 == 2 and row["shndx"]:
            return row
    return None


def _file_offset(data: bytes, address: int) -> int:
    phoff = struct.unpack_from("<I", data, 28)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from(
            "<IIIII", data, phoff + phentsize * i)
        if p_type == 1 and p_vaddr <= address < p_vaddr + p_filesz:
            return address - p_vaddr + p_offset
    raise AssertionError("linked address 0x%x is not mapped" % address)


def _linked_address(so: bytes, symbol: str) -> int:
    row = _symbol(so, symbol, table_type=11)
    assert row is not None, "%s missing from the linked .so .dynsym" % symbol
    return row["value"]


# --- measurement -----------------------------------------------------------

def _table(rows: list[dict]) -> str:
    head = ("| dummy | ref file off | ref sha/16 | cand sha/16 | size | words | EQUAL "
            "| reloc-field | obj relocs/field | ENC-DIFF | LITERAL | other | code_window_equal |")
    lines = [head, "|" + "---|" * 14]
    for r in rows:
        lines.append("| %(dummies)d | 0x%(file_offset)x | %(ref_sha)s | %(cand_sha)s | "
                     "%(size)d | %(words)d | %(EQUAL)d | %(RELOCATION_FIELD)d | "
                     "%(relocs)d/%(reloc_field)d | %(ENCODING_DIFFERENCE)d | "
                     "%(LITERAL_POOL)d | %(other)d | %(code_window_equal)s |" % r)
    return "\n".join(lines)


def _row(target: dict, opt: str, n: int, report: dict, item: dict) -> dict:
    counts = report["counts"]
    reloc_field = counts["RELOCATION_FIELD"] + report["relocation_offsets_used"]
    other = counts["ENCODING_DIFFERENCE"] + counts["UNCLASSIFIED_TAIL"] + max(
        0, report["candidate_size"] - report["reference_size"]) // UNIT
    return dict(
        dummies=n, words=report["words_compared"], size=report["candidate_size"],
        EQUAL=counts["EQUAL"], RELOCATION_FIELD=counts["RELOCATION_FIELD"],
        reloc_field=reloc_field, relocs=report["relocation_offsets_used"],
        ENCODING_DIFFERENCE=counts["ENCODING_DIFFERENCE"],
        LITERAL_POOL=counts["LITERAL_POOL"], other=other,
        code_window_equal=report["code_window_equal"],
        file_offset=item["file_offset"],
        ref_sha=report["reference_sha256"][:16] if "reference_sha256" in report else "?",
        cand_sha=report["candidate_object_sha256"][:16] if
        "candidate_object_sha256" in report else "?",
    )


def _kinds_by_offset(report: dict) -> dict[int, str]:
    return {d["offset"]: d["kind"] for d in report["word_details"]}


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    if SKIP:
        pytest.skip(SKIP)
    tmp = tmp_path_factory.mktemp("gcc443_window_check_globals")
    wd = _load("window_diff_under_measurement", ROOT / "tools/compare/window_diff.py")
    measured = []
    for target in TARGETS:
        objs, drivers, links = {}, {}, {}
        for opt in OPT_LEVELS:
            for n in DUMMIES:
                symbol = "nova3_globals_%s_%s_n%d" % (target["name"],
                                                      opt.replace("-", "o"), n)
                source = tmp / ("%s.cpp" % symbol)
                source.write_text(_source(target["body"], {"symbol_C": symbol}, n),
                                  encoding="utf-8")
                obj = tmp / ("%s.o" % symbol)
                _compile(tmp, source, obj, opt)
                objs[(opt, n)] = dict(symbol=symbol, obj=obj, bytes=obj.read_bytes())
                # DRIVER: the dummy globals are in the source; each .o is linked.
                so = tmp / ("%s_driver.so" % symbol)
                _link(tmp, obj, so, tag="drv_%s" % symbol)
                drivers[(opt, n)] = dict(obj=obj, so=so.read_bytes())
                # LINK_LAYOUT: N dummy globals only on the link line.
                so = tmp / ("%s_link_n%d.so" % (symbol, n))
                _link(tmp, objs[(opt, 0)]["obj"], so, linked_dummies=n,
                      tag="link_%s_%d" % (symbol, n))
                links[(opt, n)] = so.read_bytes()
        measured.append(dict(target=target, objs=objs, drivers=drivers, links=links))
    return {"tmp": tmp, "wd": wd, "targets": measured}


def _compare(build, so: bytes, obj: bytes, symbol: str) -> tuple[dict, dict]:
    address = _linked_address(so, symbol) & ~1
    report = build["wd"].diff_window(so, obj, address=address, symbol=symbol)
    # A32 objects only; the mode gate of window_diff must not fire.
    assert report["instruction_unit"] == UNIT, report["status"]
    return report, dict(file_offset=_file_offset(so, address))


def _family(build, entry, opt: str, family: str) -> tuple[list[dict], list[dict]]:
    """Compare the target at one -O level with 0, 1 and 8 dummy globals."""
    target = entry["target"]
    rows, reports = [], []
    for n in DUMMIES:
        item = entry["objs"][(opt, n)]
        if family == "LINK_LAYOUT":
            # The linked image moves; the candidate .o stays the n=0 build.
            so, obj = entry["links"][(opt, n)], entry["objs"][(opt, 0)]["bytes"]
            symbol = entry["objs"][(opt, 0)]["symbol"]
        else:
            so, obj, symbol = entry["drivers"][(opt, n)]["so"], item["bytes"], item["symbol"]
        report, extra = _compare(build, so, obj, symbol)
        rows.append(_row(target, opt, n, report, extra))
        reports.append(report)
    return rows, reports


def test_toolchain_and_targets(build):
    """Toolchain is 4.4.3, targets really carry GOT literals/jump tables, and
    the LINK_LAYOUT adversary really moves the window the tool reads."""
    for entry in build["targets"]:
        target = entry["target"]
        for opt in OPT_LEVELS:
            item = entry["objs"][(opt, 0)]
            obj = item["bytes"]
            row = _symbol(obj, item["symbol"])
            relocs = _relocations(obj)
            inside = [r for r in relocs if row["value"] <= r["offset"]
                      < row["value"] + row["size"]]
            kinds = {r["rtype"] for r in inside}
            if target["witness"]:
                assert kinds & GOT_RELOCS, "%s %s: no in-window GOT/REL32 " \
                    "literal relocation, found %s" % (item["symbol"], opt, sorted(kinds))
            if target["kind"] == "JUMP_TABLE":
                assert not (kinds & JUMP_TABLE_RELOCS), (
                    "%s %s: jump table moved out of the window into .rodata" % (
                        item["symbol"], opt))
                window = obj[row["value"]:row["value"] + row["size"]]
                table_words = sum(
                    1 for at in range(0, len(window) - 3, UNIT)
                    if (struct.unpack_from("<I", window, at)[0] >> 28) == 0xE
                    and (struct.unpack_from("<I", window, at)[0] & 0x0E000000) == 0x0A000000)
                assert table_words >= 6, "%s %s: only %d in-window branch-table " \
                    "words" % (item["symbol"], opt, table_words)
            window_report, extra = _compare(build, entry["drivers"][(opt, 0)]["so"],
                                            obj, item["symbol"])
            assert window_report["words_compared"] > 0
            # The linked adversary is not a no-op: with more globals linked ahead
            # of the object, the window the tool reads is at another file offset.
            offsets = [_file_offset(entry["links"][(opt, n)], _linked_address(
                entry["links"][(opt, n)], item["symbol"]) & ~1) for n in DUMMIES]
            assert len(set(offsets)) == len(DUMMIES), (
                "%s %s: LINK_LAYOUT did not move the window: %s" % (
                    item["symbol"], opt, [hex(o) for o in offsets]))
            print("target %s (%s) %s %s: %d words, in-window relocations %s, "
                  "linked offsets %s" % (
                      target["name"], target["kind"], opt, item["symbol"],
                      row["size"] // UNIT, sorted(kinds), [hex(o) for o in offsets]))


@pytest.mark.parametrize("family", ["LINK_LAYOUT", "DRIVER"])
def test_global_count_does_not_move_the_counts(build, family):
    """EQUAL, relocation-field and ENCODING_DIFFERENCE counts ignore extra globals."""
    failures = []
    for entry in build["targets"]:
        target = entry["target"]
        for opt in OPT_LEVELS:
            rows, reports = _family(build, entry, opt, family)
            table = _table(rows)
            key = "%s %s %s" % (target["kind"], target["name"], opt)
            for row, report in zip(rows, reports):
                assert report["words_compared"] > 0 and report["word_details"], (
                    "%s %s %s dummy %d: empty per-word result" % (
                        target["name"], opt, family, row["dummies"]))
            problems = _compare_variants(key, family, rows, reports)
            if family == "LINK_LAYOUT":
                # The measured side really varies with the global count: the
                # linked window moves in the file and its bytes change.
                if len({r["file_offset"] for r in rows}) != len(rows):
                    problems.append("%s: LINK_LAYOUT did not move the linked window "
                                    "across dummies" % key)
                if len({r["ref_sha"] for r in rows}) != len(rows):
                    problems.append("%s: LINK_LAYOUT kept identical reference window "
                                    "bytes across dummies" % key)
            if problems:
                failures.append("%s %s (%s)\n%s\n%s" % (
                    key, family, SCOPE, table, "\n".join(problems)))
                continue
            print("%s: reference windows at file offsets %s (%s); literal-pool "
                  "words %s" % (
                      key, [hex(r["file_offset"]) for r in rows], family,
                      [r["LITERAL_POOL"] for r in rows]))
            print("%s %s: %s holds across dummies %s\n%s" % (
                key, family, SCOPE, DUMMIES, table))
    assert not failures, (
        "%s violated: adding translation-unit globals moved the window "
        "counts\n\n%s" % (SCOPE, "\n\n".join(failures)))


def _compare_variants(key, family, rows, reports):
    """Per-field differences against the 0-dummy baseline; [] means stable."""
    base, problems = rows[0], []
    for row in rows[1:]:
        for field in ("size", "EQUAL", "reloc_field", "ENCODING_DIFFERENCE"):
            if row[field] != base[field]:
                problems.append("%s %s dummy %d: %s %d -> %d (%s)" % (
                    key, family, row["dummies"], field, base[field], row[field],
                    "moved with the global count" if field != "size"
                    else "window extent changed"))
        if row["code_window_equal"] != base["code_window_equal"]:
            explained = row["code_window_equal"] or (
                base["code_window_equal"] and row["LITERAL_POOL"] > 0)
            if not explained:
                problems.append("%s %s dummy %d: code_window_equal %s -> %s not "
                                "explained by literal-pool words (LITERAL_POOL=%d)" % (
                                    key, family, row["dummies"], base["code_window_equal"],
                                    row["code_window_equal"], row["LITERAL_POOL"]))
    # Words present in every variant must keep their class.
    shared = [w for w in reports[0]["word_details"] if w["offset"] < base["size"]]
    for row, report in zip(rows[1:], reports[1:]):
        later = _kinds_by_offset(report)
        for word in shared:
            if later.get(word["offset"]) != word["kind"]:
                problems.append("%s %s dummy %d: word at +0x%x %s -> %s" % (
                    key, family, row["dummies"], word["offset"], word["kind"],
                    later.get(word["offset"])))
                break
    return problems
