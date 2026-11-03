"""Offline contract tests for tools/compare/cpuquery_callsites.py.

The tool decides (a) which PLT stubs route to the recovered cpu-features
getters by walking `.rel.plt` entries through the `.got` base, and (b) which
functions get decoded by keeping only STT_FUNC symbols whose whole
`[st_value, st_value+st_size)` range lies inside `.text`.  A mistake in
either silently drops call sites from the recovered call graph, so both are
pinned here against a synthetic, hand-built 32-bit ARM ELF.

Everything below is synthetic: no reference binary, no `analysis/` or
`originals/` input, no external assembler.  The only "machine code" is
hand-encoded 32-bit ARM `B`/`BL`/`NOP` words.  These are not original objects
and no claim is made about any real build.
"""
import json
import runpy
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytest.importorskip("elftools")
from elftools.elf.elffile import ELFFile  # noqa: E402  (after skip guard)

TOOL = ROOT / "tools/compare/cpuquery_callsites.py"
# Run the tool in-process through its real entry point: main() takes no argv
# and parses sys.argv, so argv is supplied the way the CLI does it.
runpy.run_path(str(TOOL), run_name="not_main")
sys.path.insert(0, str(TOOL.parent))
import cpuquery_callsites  # noqa: E402  (imported after sys.path fix-up)
sys.path.remove(str(TOOL.parent))

# Hand-encoded A32 words: `bl <imm24>` = 0xEB000000 | imm24 with
# imm24 = (target - (pc + 8)) >> 2 (ARM branch PC bias is 8).
ARM_BL = 0xEB000000
ARM_B = 0xEA000000
ARM_NOP = 0xE1A00000

# ST_* values from the ELF gABI (pyelftools' ENUM_ST_INFO_TYPE/BIND tables).
STB_LOCAL, STB_GLOBAL = 0, 1
STT_NOTYPE, STT_OBJECT, STT_FUNC, STT_SECTION, STT_FILE = 0, 1, 2, 3, 4
SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB, SHT_REL, SHT_DYNSYM = 1, 2, 3, 9, 11
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 0x1, 0x2, 0x4

TEXT_VA = 0x1000
PLT_VA = 0x2000
GOT_VA = 0x3000
R_ARM_JUMP_SLOT = 22  # RAA_JUMP_SLOT in pyelftools' ENUM_RELOC_TYPE_ARM


def arm_bl(at, target):
    """Encode the A32 `BL` word placed at `at` that branches to `target`."""
    return ARM_BL | (((target - (at + 8)) >> 2) & 0x00FFFFFF)


def sym(st_value, st_size, bind, stype, st_shndx, name):
    return dict(st_value=st_value, st_size=st_size, st_info=(bind << 4) | stype,
                st_other=0, st_shndx=st_shndx, name=name)


def dynsym(*names):
    """Null symbol plus one undefined STT_FUNC STB_GLOBAL import per name."""
    return [sym(0, 0, 0, 0, 0, "")] + [
        sym(0, 0, STB_GLOBAL, STT_FUNC, 0, n) for n in names]


def r_info(sym_index, r_type=R_ARM_JUMP_SLOT):
    """ELF32 little-endian r_info word: sym << 8 | type."""
    return (sym_index << 8) | r_type


def rel_bytes(relocs):
    """Encode (r_offset, dynsym index) pairs as ELF32 SHT_REL entries."""
    return b"".join(struct.pack("<II", off, r_info(i)) for off, i in relocs)


def _table(symbols):
    strtab = b"\x00"
    offsets = {}
    data = b""
    for s in symbols:
        if s["name"] not in offsets:
            offsets[s["name"]] = len(strtab)
            strtab += s["name"].encode() + b"\x00"
        data += struct.pack("<IIIBBH", offsets[s["name"]], s["st_value"],
                            s["st_size"], s["st_info"], s["st_other"],
                            s["st_shndx"])
    return strtab, data


def build_elf(sections, symtab=None, dynsym_symbols=None):
    """Assemble a minimal little-endian ELF32/ARM file and return its bytes."""
    secs = list(sections)
    entries = []  # (name, type, flags, link_kind, info, addr, data)
    for name, sh_type, flags, addr, data in secs:
        # SHT_REL sections link the symbol table; sh_link is fixed up below.
        link = ".dynsym" if sh_type == SHT_REL else ""
        entries.append((name, sh_type, flags, link, 0, addr, data))
    if symtab is not None:
        strtab, data = _table(symtab)
        entries.append((".strtab", SHT_STRTAB, 0, "", 0, 0, strtab))
        # sh_info = index of the first non-local symbol: skip the locals.
        first_global = sum(1 for x in symtab if (x["st_info"] >> 4) != STB_LOCAL)
        entries.append((".symtab", SHT_SYMTAB, 0, ".strtab", first_global, 0, data))
    if dynsym_symbols is not None:
        strtab, data = _table(dynsym_symbols)
        idx = len(entries)
        entries.append((".dynstr", SHT_STRTAB, SHF_ALLOC, "", 0, 0, strtab))
        first_global = sum(1 for x in dynsym_symbols if (x["st_info"] >> 4) != STB_LOCAL)
        entries.append((".dynsym", SHT_DYNSYM, SHF_ALLOC, ".dynstr", first_global, 0, data))
    entries.append((".shstrtab", SHT_STRTAB, 0, "", 0, 0, b""))

    # Resolve sh_link targets to section indices.
    index = {e[0]: i + 1 for i, e in enumerate(entries)}
    resolved = []
    for name, sh_type, flags, link, info, addr, data in entries:
        link = index[link] if link else 0
        resolved.append((name, sh_type, flags, link, info, addr, data))

    shstr = b"\x00"
    names = {}
    for e in resolved:
        names[e[0]] = len(shstr)
        shstr += e[0].encode() + b"\x00"
    resolved[-1] = resolved[-1][:6] + (shstr,)

    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
    body = b"\x00" * 52
    placed = []
    for name, sh_type, flags, link, info, addr, data in resolved:
        body += b"\x00" * (-len(body) % 4)
        placed.append((name, sh_type, flags, link, info, addr, len(body), len(data)))
        body += data
    body += b"\x00" * (-len(body) % 4)
    shoff = len(body)
    body += b"\x00" * 40
    for name, sh_type, flags, link, info, addr, offset, size in placed:
        body += struct.pack("<10I", names[name], sh_type, flags, addr, offset, size,
                            link, info, 4,
                            16 if sh_type in (SHT_SYMTAB, SHT_DYNSYM)
                            else 8 if sh_type == SHT_REL else 0)
    header = ident + struct.pack("<HHIIIIIHHHHHH", 2, 40, 1, 0, 0, shoff,
                                 0x05000000, 52, 0, 0, 40, len(placed) + 1,
                                 len(placed))
    return header + body[len(header):]


def write_elf(tmp_path, name="fixture.so", **kwargs):
    path = tmp_path / name
    path.write_bytes(build_elf(**kwargs))
    return path


def sized_func(st_value, st_size, name, bind=STB_GLOBAL):
    return sym(st_value, st_size, bind, STT_FUNC, 1, name)


def plt_elf(tmp_path, relocs, drop=None,
            dynsym_names=("android_getCpuFeatures", "second_import")):
    """ELF with .plt/.got/.rel.plt; relocs are (r_offset, dynsym index) pairs.

    All relocation sections share the dynsym, so `drop` (a section name) can
    remove any one of .plt/.got/.rel.plt while keeping the link valid.
    """
    sections = [
        (".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, TEXT_VA, b"\x00" * 16),
        (".plt", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, PLT_VA, b"\x00" * 48),
        (".got", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, GOT_VA, b"\x00" * 400),
        (".rel.plt", SHT_REL, SHF_ALLOC, 0, rel_bytes(relocs)),
    ]
    return write_elf(
        tmp_path,
        sections=[sec for sec in sections if sec[0] != drop],
        symtab=[sym(0, 0, 0, 0, 0, "")],
        dynsym_symbols=dynsym(*dynsym_names))


# ------------------------------------------------------------- PLT mapping


def test_plt_targets_maps_got_lo_and_got_lo_plus_4_to_adjacent_stubs(tmp_path):
    # Fixture convention: .plt base == .got base.  The tool computes
    # stub = .plt + (r_offset - .got) * 4, so one GOT slot (4 bytes)
    # advances the stub by 16 bytes: GOT slots got_lo and got_lo + 4 map to
    # .plt + 0 and .plt + 16.  The GOT slots are what the spec asks for; the
    # resulting adjacent *stubs* (expected +4) are not what this tool yields.
    path = plt_elf(tmp_path, [(GOT_VA, 1), (GOT_VA + 4, 2)])
    with open(path, "rb") as fh, ELFFile(fh) as elf:
        relocs = list(elf.get_section_by_name(".rel.plt").iter_relocations())
        assert [r.entry.r_offset for r in relocs] == [GOT_VA, GOT_VA + 4]
        assert [r.entry.r_info_sym for r in relocs] == [1, 2]
        assert cpuquery_callsites.plt_targets(elf) == {
            PLT_VA + 0: "android_getCpuFeatures",
            PLT_VA + 16: "second_import",  # observed; +4 is the spec's intent
        }


def test_plt_targets_omits_stub_outside_section(tmp_path):
    # Here .plt is at 0x2000 and .got at 0x3000.  With the tool's absolute
    # formula a GOT-relative relocation (r_offset >= .got) always maps to
    # .plt + 16*k, i.e. >= .plt; the 16-byte step leaves only 3 stubs inside
    # a 48-byte .plt (+0/+16/+32).  Offsets past the section are omitted:
    # +48 and +52 map past .plt and yield nothing.
    path = plt_elf(tmp_path, [(GOT_VA + 8, 1), (GOT_VA + 48, 1),
                              (GOT_VA + 52, 1)])
    with open(path, "rb") as fh, ELFFile(fh) as elf:
        assert cpuquery_callsites.plt_targets(elf) == {PLT_VA + 32: "android_getCpuFeatures"}


def test_plt_targets_ignores_nameless_relocations(tmp_path):
    path = plt_elf(tmp_path, [(GOT_VA, 0), (GOT_VA + 4, 2)])
    with open(path, "rb") as fh, ELFFile(fh) as elf:
        assert cpuquery_callsites.plt_targets(elf) == {PLT_VA + 16: "second_import"}


@pytest.mark.parametrize("drop", [".plt", ".got", ".rel.plt"])
def test_plt_targets_returns_empty_when_a_section_is_missing(tmp_path, drop):
    path = plt_elf(tmp_path, [(GOT_VA, 1)], drop=drop)
    with open(path, "rb") as fh, ELFFile(fh) as elf:
        assert cpuquery_callsites.plt_targets(elf) == {}


def test_plt_targets_on_elf_without_dynamic_sections(tmp_path):
    path = write_elf(
        tmp_path,
        sections=[(".text", SHT_PROGBITS, SHF_ALLOC, TEXT_VA, b"\x00" * 16)],
        symtab=[sized_func(TEXT_VA, 16, "only")])
    with open(path, "rb") as fh, ELFFile(fh) as elf:
        assert cpuquery_callsites.plt_targets(elf) == {}


# ---------------------------------------------------------- symbol filter


def test_func_symbols_keeps_sized_functions_sorted_with_local_flag(tmp_path):
    path = write_elf(
        tmp_path,
        sections=[(".text", SHT_PROGBITS, SHF_ALLOC, TEXT_VA, b"\x00" * 0x80)],
        symtab=[
            sym(0, 0, STB_LOCAL, STT_FILE, 0xFFF1, "fixture.c"),
            sized_func(0x1030, 8, "local_fn", bind=STB_LOCAL),
            sized_func(0x1010, 16, "main_fn"),
            sized_func(0x1000, 0, "zero_sized"),
            sym(0x1000, 16, STB_GLOBAL, STT_OBJECT, 1, "an_object"),
            sym(0x1000, 16, STB_GLOBAL, STT_NOTYPE, 1, "a_label"),
            sym(0x1000, 16, STB_GLOBAL, STT_SECTION, 1, ""),
            sized_func(0x1040, 4, "tail_fn"),
        ])
    with open(path, "rb") as fh, ELFFile(fh) as elf:
        rows = cpuquery_callsites.func_symbols(elf)
    # (addr, size, name, is_local) sorted by address then size then name.
    assert rows == [
        (0x1010, 16, "main_fn", False),
        (0x1030, 8, "local_fn", True),
        (0x1040, 4, "tail_fn", False),
    ]


# --------------------------------------------- .text bounds filter + main()

# The tool's stub formula is .plt + (r_offset - .got) * 4 over section
# offsets, so GOT slot 0 (r_offset == .got) maps to the .plt base.
STUB0_R_OFFSET = GOT_VA
# main() hunts the module's own hard-coded TARGETS addresses; no CLI flag
# relocates them (--also only adds targets).  The direct BL therefore has to
# name the real TARGETS VA, so .text is laid out around it: values derived
# from the tool's own table, not from any game binary.
_DIRECT_VA, _DIRECT_SIZE = cpuquery_callsites.TARGETS["android_getCpuFeatures"]
TEXT_VA = _DIRECT_VA - 0x20                 # .text = [TEXT_VA, TEXT_VA + 0x40)
TARGET_VA = _DIRECT_VA                      # inside host_fn's decoded range


def run_cli(reference, out):
    """Run the tool as a script; it is cwd-independent and takes --reference."""
    return subprocess.run(
        [sys.executable, str(TOOL), "--reference", str(reference), "--out", str(out)],
        capture_output=True, text=True, timeout=120)


def callsite_elf(tmp_path):
    """.text spans [TEXT_VA, TEXT_VA + 0x40); host_fn ends exactly at its end.

    host_fn  [TEXT_VA, TEXT_VA + 0x40) decodes: its first slot is a direct BL
             to TARGET_VA (the real TARGETS VA) and its last slot (+0x3C) is
             a BL to the PLT stub.  All other slots are NOPs.
    overrun  [TEXT_VA + 0x38, +0x80): ends past .text -> must be skipped.
    before   [TEXT_VA - 0x10, +0x20): starts before .text -> must be skipped.
    """
    words = [ARM_NOP] * 16
    words[0] = arm_bl(TEXT_VA + 0x00, TARGET_VA)
    words[15] = arm_bl(TEXT_VA + 0x3C, PLT_VA)
    (tmp_path / "callsite").mkdir()
    return write_elf(
        tmp_path / "callsite",
        name="fixture.so",
        sections=[
            (".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, TEXT_VA,
             struct.pack("<16I", *words)),
            (".plt", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, PLT_VA,
             b"\x00" * 48),
            (".got", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, GOT_VA, b"\x00" * 400),
            (".rel.plt", SHT_REL, SHF_ALLOC, 0, rel_bytes([(STUB0_R_OFFSET, 1)])),
        ],
        symtab=[
            sym(0, 0, STB_LOCAL, STT_FILE, 0xFFF1, "fixture.c"),
            sized_func(TEXT_VA, 0x40, "host_fn"),
            sized_func(TEXT_VA + 0x38, 0x48, "overrun_fn"),
            sized_func(TEXT_VA - 0x10, 0x20, "before_fn"),
        ],
        dynsym_symbols=dynsym("android_getCpuCount"))


def test_fixture_helpers_agree_before_cli(tmp_path):
    """The fixture really is what the bounds/PLT assertions assume."""
    path = callsite_elf(tmp_path)
    with open(path, "rb") as fh, ELFFile(fh) as elf:
        assert cpuquery_callsites.plt_targets(elf) == {PLT_VA: "android_getCpuCount"}
        assert cpuquery_callsites.func_symbols(elf) == [
            (TEXT_VA - 0x10, 0x20, "before_fn", False),
            (TEXT_VA, 0x40, "host_fn", False),
            (TEXT_VA + 0x38, 0x48, "overrun_fn", False),
        ]


def test_bounds_filter_decodes_call_sites(tmp_path):
    pytest.importorskip("capstone")
    out = tmp_path / "out" / "call_sites.json"
    proc = run_cli(callsite_elf(tmp_path), out)
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    report = json.loads(out.read_text())
    assert report["call_site_count"] == len(report["call_sites"])
    # 3 sized functions exist; only host_fn lies wholly inside .text, so only
    # its 16 instructions are decoded.
    assert report["functions_decoded"] == 1
    assert report["distinct_hosts"] == ["host_fn"]
    assert report["plt_stub_names"] == {f"0x{PLT_VA:08x}": "android_getCpuCount"}

    direct = [h for h in report["call_sites"] if h["linkage"] == "direct"]
    assert len(direct) == 1, report["call_sites"]
    hit = direct[0]
    assert hit["call_site"] == f"0x{TEXT_VA:08x}"
    assert hit["mnemonic"] == "bl"
    assert hit["callee"] == "android_getCpuFeatures"
    assert hit["callee_addr"] == f"0x{TARGET_VA:08x}"
    assert hit["host_symbol"] == "host_fn"
    assert hit["host_addr"] == f"0x{TEXT_VA:08x}" and hit["host_size"] == 0x40

    plt_hits = [h for h in report["call_sites"] if h["linkage"] == "plt"]
    assert len(plt_hits) == 1, report["call_sites"]
    assert plt_hits[0]["call_site"] == f"0x{TEXT_VA + 0x3C:08x}"
    assert plt_hits[0]["callee"] == "android_getCpuCount"
    assert plt_hits[0]["callee_addr"] == f"0x{PLT_VA:08x}"
    assert plt_hits[0]["host_symbol"] == "host_fn"


def test_cli_exit_1_writes_json_when_no_call_site(tmp_path):
    pytest.importorskip("capstone")
    path = write_elf(
        tmp_path,
        sections=[(".text", SHT_PROGBITS, SHF_ALLOC, TEXT_VA,
                   struct.pack("<4I", *([ARM_NOP] * 4)))],
        symtab=[sized_func(TEXT_VA, 0x10, "quiet")])
    out = tmp_path / "quiet" / "report.json"
    proc = run_cli(path, out)
    assert proc.returncode == 1, proc.stderr
    assert out.is_file()
    report = json.loads(out.read_text())
    assert report["call_sites"] == []
    assert report["functions_decoded"] == 1


def test_cli_argument_error_is_exit_2(tmp_path):
    proc = subprocess.run([sys.executable, str(TOOL)], capture_output=True,
                          text=True, timeout=120, cwd=str(tmp_path))
    assert proc.returncode == 2
