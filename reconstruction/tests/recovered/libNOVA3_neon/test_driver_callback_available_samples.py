"""Source-only host tests; bounded offline Premium inspection and ARM comparison.

Required evidence fails closed (no skips). NOVA3_PREMIUM_ELF and NOVA3_ARM_CXX
may select existing local inputs, never download them. Reference bytes/dumps and
objects stay in memory or pytest's private temporary directory. comparison.json
records hashes, flags, relocation status and raw instruction-window equality;
structural agreement and emulated behaviour are NOT an instruction match.
"""
import ctypes as C
import hashlib
import io
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "reconstruction/recovered/libNOVA3_neon/driver_callback_available_samples.cpp"
HEADER = SRC.with_suffix(".h")
VA, SIZE = 0x009D38D8, 192
SYMBOL = "_ZN3vox29DriverCallbackSourceInterface21GetNbAvailableSamplesEi"
REFERENCE_SHA = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
LOCAL = Path("/home/jason/Projects/nova3_re")


class Buffer(C.Structure):
    _fields_ = [("extent", C.c_int), ("consumed", C.c_int), ("free", C.c_ubyte)]


class State(C.Structure):
    _fields_ = [("buffers", C.POINTER(Buffer)), ("count", C.c_int),
                ("current", C.c_int), ("divisor", C.c_int)]


def run(command):
    result = subprocess.run([str(x) for x in command], capture_output=True, text=True)
    assert result.returncode == 0, f"compilation/tool failure ({result.returncode}): {result.stderr}"
    return result.stdout


@pytest.fixture(scope="module")
def host_library(tmp_path_factory):
    directory = tmp_path_factory.mktemp("callback-samples-host")
    wrapper = directory / "wrapper.cpp"
    wrapper.write_text('#include "driver_callback_available_samples.h"\n'
                       'extern "C" int available(const nova3_recovered::CallbackSampleState* s, int n) {\n'
                       ' return nova3_recovered::GetNbAvailableSamples(*s, n); }\n')
    compiler = shutil.which("c++")
    assert compiler, "required host C++ compiler missing"
    library = directory / "source.so"
    run([compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         "-fPIC", "-shared", "-I", SRC.parent, SRC, wrapper, "-o", library])
    lib = C.CDLL(str(library))
    lib.available.argtypes = [C.POINTER(State), C.c_int]
    lib.available.restype = C.c_int
    return lib


def sequence_oracle(records, current, divisor, request, count=None):
    """Flatten unread sample tokens, not the implementation's running arithmetic."""
    if count is None:
        count = len(records)
    if count <= 0:
        return -1
    ordered = records[current:] + records[:current]
    prefix = []
    for extent, consumed, free in ordered:
        if free:
            return len(prefix)
        # Tokens are ordinal sample positions after consumed, including floor
        # division's discarded trailing extent units.
        prefix.extend(range(consumed, extent // divisor))
        if len(prefix) >= request:
            return request
    return -1


def cases():
    # Named boundaries: immediate free, partial, exact, cap, wrap, exhaustion,
    # one slot, zero samples/request/count, non-boolean free, nonmultiple extent.
    yield ([(99, 99, 255)], 0, 0, 5, None)
    for request in (0, 1, 3, 4, 5, 7, 8, 9, 20):
        yield ([(11, 2, 0), (17, 1, 0), (0, 0, 1)], 0, 2, request, None)
        yield ([(17, 1, 0), (0, 0, 2), (11, 2, 0)], 2, 2, request, None)
        yield ([(11, 2, 0), (17, 1, 0)], 1, 2, request, None)
    for free in (0, 1):
        for extent in (0, 1, 2, 3, 7):
            for request in (0, 1, 2, 7):
                yield ([(extent, 0, free)], 0, 2, request, None)
    yield ([], 0, 0, 3, 0)
    yield ([], -77, 0, 0, -1)
    rng = random.Random(357)
    for _ in range(240):
        n, divisor = rng.randint(1, 6), rng.randint(1, 5)
        records = []
        for _ in range(n):
            extent = rng.randint(0, 30)
            records.append((extent, rng.randint(0, extent // divisor),
                            rng.choice((0, 0, 0, 1, 255))))
        yield (records, rng.randrange(n), divisor, rng.randint(0, 45), None)


def call_host(lib, records, current, divisor, request, count):
    buffers = (Buffer * len(records))(*(Buffer(*r) for r in records))
    state = State(buffers, len(records) if count is None else count, current, divisor)
    before = bytes(state), bytes(buffers)
    result = lib.available(C.byref(state), request)
    assert result == lib.available(C.byref(state), request)
    assert (bytes(state), bytes(buffers)) == before, "accounting mutated source state"
    return result


def test_host_sequence_and_immutability(host_library):
    for args in cases():
        assert call_host(host_library, *args) == sequence_oracle(*args), args


def inspect_reference():
    from elftools.elf.elffile import ELFFile
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
    from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG

    path = Path(os.environ.get("NOVA3_PREMIUM_ELF", str(
        LOCAL / "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so")))
    assert path.is_file(), "required local Premium reference missing"
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == REFERENCE_SHA
    elf = ELFFile(io.BytesIO(data))
    assert elf.elfclass == 32 and elf.little_endian
    assert elf["e_machine"] == "EM_ARM"
    assert b"GCC: (GNU) 4.4.3" in elf.get_section_by_name(".comment").data()
    table = elf.get_section_by_name(".symtab")
    assert table is not None
    symbols = list(table.iter_symbols())
    selected = [s for s in symbols if s.name == SYMBOL]
    assert len(selected) == 1
    symbol = selected[0]
    assert (symbol["st_value"], symbol["st_size"], symbol["st_info"]["type"]) == (VA, SIZE, "STT_FUNC")
    section = elf.get_section(symbol["st_shndx"])
    mappings = [s for s in symbols if s["st_shndx"] == symbol["st_shndx"]
                and s.name.startswith(("$a", "$t", "$d"))]
    preceding = max((s for s in mappings if s["st_value"] <= VA), key=lambda s: s["st_value"])
    assert preceding.name.split(".")[0] == "$a" and preceding["st_value"] == VA
    assert not any(VA < s["st_value"] < VA + SIZE for s in mappings)

    def window(address, length):
        assert section["sh_addr"] <= address < address + length <= section["sh_addr"] + section["sh_size"]
        offset = section["sh_offset"] + address - section["sh_addr"]
        return data[offset:offset + length]

    code = window(VA, SIZE)
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    decoder.detail = True
    insns = list(decoder.disasm(code, VA))
    assert len(insns) == 48 and all(i.size == 4 for i in insns)
    by_offset = {i.address - VA: i for i in insns}
    # Every data load, with base register, width and displacement. No writes
    # except the stack prologue: both traversal and buffer state are read-only.
    loads = []
    for i in insns:
        if i.mnemonic in ("ldr", "ldrb"):
            m = next(o.mem for o in i.operands if o.type == ARM_OP_MEM)
            loads.append((i.mnemonic, i.reg_name(m.base), m.disp))
        assert not i.mnemonic.startswith(("str", "stm"))
    assert loads == [("ldr", "r0", 0x50), ("ldr", "r0", 0x58),
                     ("ldr", "r0", 0x6c), ("ldrb", "r5", 20),
                     ("ldr", "r0", 0x68), ("ldr", "r5", 4),
                     ("ldr", "r5", 16), ("ldrb", "r5", 20),
                     ("ldr", "r5", 4), ("ldr", "r5", 16)]
    # All local control-flow edges, signed comparisons and return sites.
    branches = {i.address - VA: (i.mnemonic, i.operands[0].imm - VA)
                for i in insns if i.mnemonic.startswith("b") and i.mnemonic != "bl"}
    assert branches == {20: ("ble", 168), 48: ("bne", 172),
                        80: ("bgt", 148), 84: ("b", 180),
                        116: ("bne", 172), 144: ("bge", 180),
                        164: ("bne", 88)}
    calls = [(i.address - VA, i.operands[0].imm) for i in insns if i.mnemonic == "bl"]
    assert calls == [(64, 0xbe2a84), (88, 0xbe2b60), (124, 0xbe2a84)]
    for name, address in (("__aeabi_idiv", 0xbe2a84), ("__aeabi_idivmod", 0xbe2b60)):
        assert any(s.name == name and s["st_value"] == address for s in symbols)

    def operands(offset, mnemonic, regs, immediates=()):
        i = by_offset[offset]
        assert i.mnemonic == mnemonic
        assert tuple(i.reg_name(o.reg) for o in i.operands if o.type == ARM_OP_REG) == regs
        assert tuple(o.imm for o in i.operands if o.type == ARM_OP_IMM) == immediates

    # Stride = (index + index*2)*8 = 24; signed quotient minus consumption;
    # remainder in r1 becomes next index; bound is exactly count, not free-only.
    for offset in (28, 100):
        operands(offset, "add", ("r5", "r4", "r4"))
        assert by_offset[offset].operands[2].shift.value == 1
    for offset in (32, 104):
        operands(offset, "add", ("r5", "sb", "r5"))
        assert by_offset[offset].operands[2].shift.value == 3
    operands(8, "mov", ("sl", "r1"))
    operands(16, "cmp", ("r8",), (0,))
    operands(40, "cmp", ("r7",), (0,))
    operands(44, "movne", ("r6",), (0,))
    operands(60, "mov", ("r1", "fp"))
    operands(72, "rsb", ("r6", "r6", "r0"))
    operands(76, "cmp", ("sl", "r6"))
    operands(92, "mov", ("r4", "r1"))
    operands(96, "mov", ("r1", "fp"))
    operands(132, "rsb", ("r3", "r3", "r0"))
    operands(136, "add", ("r6", "r3", "r6"))
    operands(140, "cmp", ("r6", "sl"))
    operands(148, "add", ("r7", "r7"), (1,))
    operands(152, "add", ("r0", "r4"), (1,))
    operands(156, "cmp", ("r7", "r8"))
    operands(160, "mov", ("r1", "r8"))
    operands(168, "mvn", ("r6",), (0,))
    operands(172, "mov", ("r0", "r6"))
    operands(180, "mov", ("r6", "sl"))
    operands(184, "mov", ("r0", "r6"))
    assert by_offset[176].mnemonic == by_offset[188].mnemonic == "pop"
    return code, window(0xbe2a84, 244), elf


def emulate_reference(code, helpers, args):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
    records, current, divisor, request, count = args
    count = len(records) if count is None else count
    uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    for address, size in ((VA & ~4095, 4096), (0xbe2000, 4096),
                          (0x1000000, 4096), (0x2000000, 8192), (0x3000000, 4096)):
        uc.mem_map(address, size)
    uc.mem_write(VA, code)
    uc.mem_write(0xbe2a84, helpers)
    receiver, buffers, stop = 0x1000000, 0x1000100, 0x3000000
    obj = bytearray(0x70)
    for offset, value in ((0x50, count), (0x58, current), (0x68, divisor), (0x6c, buffers)):
        struct.pack_into("<I", obj, offset, value & 0xffffffff)
    ring = bytearray(24 * len(records))
    for index, (extent, consumed, free) in enumerate(records):
        struct.pack_into("<i", ring, index * 24 + 4, extent)
        struct.pack_into("<i", ring, index * 24 + 16, consumed)
        ring[index * 24 + 20] = free
    uc.mem_write(receiver, bytes(obj))
    if ring:
        uc.mem_write(buffers, bytes(ring))
    before = bytes(uc.mem_read(receiver, 4096))
    uc.reg_write(UC_ARM_REG_R0, receiver)
    uc.reg_write(UC_ARM_REG_R1, request)
    uc.reg_write(UC_ARM_REG_SP, 0x2001ff0)
    uc.reg_write(UC_ARM_REG_LR, stop)
    uc.emu_start(VA, stop, count=10000)
    assert uc.reg_read(UC_ARM_REG_PC) == stop, "bounded reference failed to return"
    assert bytes(uc.mem_read(receiver, 4096)) == before, "reference mutated ring/state"
    return C.c_int32(uc.reg_read(UC_ARM_REG_R0)).value


def test_reference_control_flow_behavior_and_arm_candidate(host_library, tmp_path):
    from elftools.elf.elffile import ELFFile
    code, helpers, _ = inspect_reference()
    count = 0
    for args in cases():
        original = emulate_reference(code, helpers, args)
        assert original == sequence_oracle(*args), args
        assert original == call_host(host_library, *args), args
        count += 1

    compiler = Path(os.environ.get("NOVA3_ARM_CXX", str(Path.home() /
        ".local/opt/android-ndk-r8e/toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-g++")))
    assert compiler.is_file(), "required identified GCC 4.4.3 toolchain missing"
    version = run([compiler, "--version"])
    assert "4.4.3" in version
    flags = ["-O2", "-marm", "-march=armv7-a", "-mfpu=vfpv3", "-mfloat-abi=softfp",
             "-fPIC", "-fno-exceptions", "-fno-rtti", "-Wall", "-Wextra", "-Werror"]
    obj = tmp_path / "candidate.o"
    run([compiler, *flags, "-c", SRC, "-o", obj])
    with obj.open("rb") as stream:
        candidate = ELFFile(stream)
        assert candidate["e_type"] == "ET_REL" and candidate["e_machine"] == "EM_ARM"
        symbols = list(candidate.get_section_by_name(".symtab").iter_symbols())
        functions = [s for s in symbols if s["st_info"]["type"] == "STT_FUNC" and s["st_size"]]
        assert len(functions) == 1
        function = functions[0]
        section = candidate.get_section(function["st_shndx"])
        start = function["st_value"]
        assert start % 4 == 0
        body = section.data()[start:start + function["st_size"]]
        relocations = [r["r_offset"] - start for s in candidate.iter_sections()
                       if s["sh_type"] in ("SHT_REL", "SHT_RELA")
                       and s["sh_info"] == function["st_shndx"]
                       for r in s.iter_relocations()
                       if start <= r["r_offset"] < start + len(body)]
    from collections import Counter
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
    decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    reference_structure = Counter(i.mnemonic for i in decoder.disasm(code, VA))
    candidate_structure = Counter(i.mnemonic for i in decoder.disasm(body, 0))
    report = dict(reference_sha256=REFERENCE_SHA, address=VA, size=SIZE, mode="ARM",
                  reference_instruction_classes=dict(reference_structure),
                  candidate_instruction_classes=dict(candidate_structure),
                  instruction_class_counts_equal=(reference_structure == candidate_structure),
                  reference_window_sha256=hashlib.sha256(code).hexdigest(),
                  source_sha256=hashlib.sha256(SRC.read_bytes()).hexdigest(),
                  header_sha256=hashlib.sha256(HEADER.read_bytes()).hexdigest(),
                  compiler_version=version, compiler_sha256=hashlib.sha256(compiler.read_bytes()).hexdigest(),
                  flags=flags, object_sha256=hashlib.sha256(obj.read_bytes()).hexdigest(),
                  candidate_size=len(body), candidate_window_sha256=hashlib.sha256(body).hexdigest(),
                  candidate_relocation_offsets=relocations,
                  raw_instruction_window_equal=(body == code),
                  instruction_window_status="MATCHED" if body == code and not relocations else "NOT MATCHED",
                  structural_check="bounded field/arithmetic/branch inspection passed",
                  behavior=f"{count} synthetic cases: original ARM emulation / host source / sequence oracle",
                  original_class_abi="UNRESOLVED", whole_library_match="UNKNOWN")
    (tmp_path / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"ARM candidate: {len(body)} bytes vs {SIZE}; "
          f"instruction classes equal={reference_structure == candidate_structure}; "
          f"{report['instruction_window_status']}; "
          f"{count} behavior cases; private report: {tmp_path / 'comparison.json'}")
    # Equality is measured, never required: a portable view has a different ABI,
    # and relocatable helper calls differ from their linked reference encoding.
    assert body and count > 300
