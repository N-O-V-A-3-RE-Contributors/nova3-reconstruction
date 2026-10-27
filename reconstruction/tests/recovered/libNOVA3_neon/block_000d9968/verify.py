#!/usr/bin/env python3
"""Offline, bounded source/ARM comparison. Emits hashes and counts, never code.

Requires existing clang++, g++, ld.lld, pyelftools and Unicorn. Never installs
anything. Reference is opened read-only and used only as a local test oracle.
The linked ARM candidate assigns dependency symbols test-only absolute addresses;
those dependencies are intercepted, never supplied by an original game library.
"""
import argparse
import ctypes as C
import hashlib
import importlib.metadata
import json
from pathlib import Path
import random
import struct
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)

REFERENCE_HASH = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
ADDRESSES = (0xd9968, 0xda9e4, 0xda9e8, 0xda9ec)
SIZES = (8, 4, 4, 52)
DEPENDENCIES = (0xd91b8, 0xda6f8, 0xd9970)
DEP_NAMES = ('nova3_iab_send_data_dependency', 'nova3_iab_item_array_dependency',
             'nova3_iab_item_by_id_dependency')
REGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
SAVED = (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
         UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)
DATA, STACK, CALLBACK = 0x2000000, 0x3000000, 0x4000000
STOP = CALLBACK + 4
SP = STACK + 0x8000


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(args, commands):
    done = subprocess.run([str(x) for x in args], check=True, text=True,
                          capture_output=True)
    commands.append({'argv': [str(x) for x in args], 'exit_code': done.returncode,
                     'stdout': done.stdout.strip(), 'stderr': done.stderr.strip()})
    return done.stdout


def load_image(path):
    with open(path, 'rb') as file:
        elf = ELFFile(file)
        assert elf.elfclass == 32 and elf.little_endian and elf['e_machine'] == 'EM_ARM'
        loads = [(s['p_vaddr'], s['p_memsz'], s.data()) for s in elf.iter_segments()
                 if s['p_type'] == 'PT_LOAD' and s['p_memsz']]
        symbols = {s.name: (s['st_value'], s['st_size'])
                   for s in elf.get_section_by_name('.symtab').iter_symbols()}
    return loads, symbols


def code_at(image, address, length):
    for base, _, data in image:
        if base <= address and address + length <= base + len(data):
            return data[address - base:address - base + length]
    raise AssertionError('function outside file-backed load segments')


def arguments(scenario, seed):
    integer = -1 - (seed & 0x7fffffff)
    pointer = seed ^ 0x12345678
    wide = -0x123456780000000 - seed
    real = seed * 0.25 - 1024.5
    return ([], [('i', integer)], [('p', pointer)], [('d', real)], [('q', wide)],
            [('i', integer), ('d', real), ('p', pointer), ('q', wide),
             ('i', 0), ('i', -7), ('d', -real), ('p', 0)],
            [('i', x) for x in (0, 1, -1, integer, 4, 5, 6, 7, 8, 9, 10, 11)])[scenario]


def argument_bits(args):
    return [struct.unpack('<Q', struct.pack('<d', value))[0] if kind == 'd'
            else value & ((1 << (64 if kind == 'q' else 32)) - 1)
            for kind, value in args]


class Oracle:
    def __init__(self, image):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        pages = set()
        for base, size, _ in image:
            pages.update(range(base & ~4095, (base + size + 4095) & ~4095, 4096))
        # Coalesce adjacent pages: a mapping per page exhausts Unicorn/QEMU's
        # memory-section limit on the large reference ELF.
        ordered = sorted(pages)
        start = previous = ordered[0]
        for page in ordered[1:]:
            if page != previous + 4096:
                self.uc.mem_map(start, previous + 4096 - start)
                start = page
            previous = page
        self.uc.mem_map(start, previous + 4096 - start)
        for base, _, data in image:
            self.uc.mem_write(base, data)
        self.uc.mem_map(DATA, 0x2000)
        self.uc.mem_map(STACK, 0x10000)
        self.uc.mem_map(CALLBACK, 4096)
        self.uc.mem_write(DATA, struct.pack('<I', DATA + 0x100))
        self.uc.mem_write(DATA + 0x100 + 0x8c, struct.pack('<I', CALLBACK))
        self.uc.hook_add(UC_HOOK_CODE, self.hook)

    def hook(self, uc, address, size, _):
        if address not in (*DEPENDENCIES, CALLBACK):
            assert self.entry <= address < self.entry + self.length, 'unexpected control transfer'
            return
        self.calls += 1
        assert self.calls == 1
        r0, r1, r2, r3 = [uc.reg_read(r) for r in REGS]
        if address == CALLBACK:
            assert self.entry == ADDRESSES[3] and r0 == DATA
            actual = []
            cursor = r3
            for kind, _ in self.args:
                width = 8 if kind in ('q', 'd') else 4
                cursor = (cursor + width - 1) & ~(width - 1)
                actual.append(int.from_bytes(uc.mem_read(cursor, width), 'little'))
                cursor += width
            self.trace = [r1, r2, self.result, len(actual), *actual]
        else:
            which = DEPENDENCIES.index(address)
            assert self.entry == ADDRESSES[which]
            self.trace = [r0 if which != 1 else 0, r1 if which == 2 else 0,
                          1, self.result]
            if which == 2:
                assert r1 == DATA + 0x500
                uc.mem_write(r1 + 7, bytes([uc.mem_read(r1 + 7, 1)[0] ^ 0x5a]))
        # Permitted caller-clobbered registers are deliberately poisoned.
        for reg in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12):
            uc.reg_write(reg, 0xaabbccdd)
        uc.reg_write(UC_ARM_REG_R0, self.result)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def run(self, which, registers, result, args=()):
        self.entry, self.length = ADDRESSES[which], SIZES[which]
        self.args, self.result, self.calls, self.trace = args, result, 0, None
        self.uc.mem_write(SP - 256, bytes([0xa5]) * 1024)
        self.uc.mem_write(DATA + 0x500, bytes(32))
        cursor = SP - 4
        first_vararg = 0xcccccccc
        for kind, value in args:
            width = 8 if kind in ('q', 'd') else 4
            cursor = (cursor + width - 1) & ~(width - 1)
            encoded = struct.pack('<d', value) if kind == 'd' else (
                value & ((1 << (8 * width)) - 1)).to_bytes(width, 'little')
            if cursor == SP - 4:
                first_vararg = int.from_bytes(encoded, 'little')
            else:
                self.uc.mem_write(cursor, encoded)
            cursor += width
        if which == 3:
            registers = [*registers[:3], first_vararg]
        for reg, value in zip(REGS, registers):
            self.uc.reg_write(reg, value)
        for i, reg in enumerate(SAVED):
            self.uc.reg_write(reg, 0x76540000 + i)
        self.uc.reg_write(UC_ARM_REG_SP, SP)
        self.uc.reg_write(UC_ARM_REG_LR, STOP)
        self.uc.emu_start(self.entry, STOP, count=100)
        assert self.uc.reg_read(UC_ARM_REG_PC) == STOP, 'instruction budget exhausted'
        assert self.uc.reg_read(UC_ARM_REG_SP) == SP and self.calls == 1
        assert self.uc.reg_read(UC_ARM_REG_R0) == result
        assert all(self.uc.reg_read(reg) == 0x76540000 + i for i, reg in enumerate(SAVED))
        if which == 2:
            assert bytes(self.uc.mem_read(DATA + 0x500, 32)) == bytes(7) + b'\x5a' + bytes(24)
        return self.trace


def verify(reference, build):
    assert digest(reference) == REFERENCE_HASH, 'reference hash mismatch'
    root = Path(__file__).resolve().parents[5]
    source = root / 'reconstruction/recovered/libNOVA3_neon/block_000d9968.cpp'
    header = source.parent / 'block_000d9968/interface.h'
    test = root / 'reconstruction/tests/recovered/libNOVA3_neon/block_000d9968_test.cpp'
    commands = []
    common = ['-std=c++17', '-Wall', '-Wextra', '-Wpedantic', '-Werror']
    command(['g++', *common, '-O2', '-c', source, '-o', build / 'host.o'], commands)
    command(['g++', *common, '-O2', test, '-o', build / 'test'], commands)
    command([build / 'test'], commands)
    command(['g++', *common, '-O2', '-DNDEBUG', test, '-o', build / 'release-test'], commands)
    command([build / 'release-test'], commands)
    command(['g++', *common, '-O2', '-fPIC', '-shared', '-DNOVA3_SHARED_TEST',
             test, '-o', build / 'test.so'], commands)
    arm_flags = ['--target=armv7-none-eabi', '-march=armv7-a', '-marm',
                 '-mfloat-abi=softfp', '-mfpu=neon', *common, '-Os',
                 '-ffreestanding', '-ffunction-sections', '-fomit-frame-pointer',
                 '-fno-exceptions', '-fno-rtti', '-fno-unwind-tables',
                 '-fno-asynchronous-unwind-tables']
    command(['clang++', *arm_flags, '-c', source, '-o', build / 'arm.o'], commands)
    with open(build / 'arm.o', 'rb') as file:
        elf = ELFFile(file)
        names = [s.name for s in elf.get_section_by_name('.symtab').iter_symbols()
                 if s['st_info']['type'] == 'STT_FUNC']
    method = next(name for name in names if 'call_object_method' in name)
    names = ['nova3_000d9968', 'nova3_000da9e4', 'nova3_000da9e8', method]
    # Placement/absolute external symbols are solely a research comparison link.
    # The production source has no absolute addresses or reference dependencies.
    linker = '\n'.join(f'{name} = {address:#x};' for name, address in zip(DEP_NAMES, DEPENDENCIES))
    linker += '\nSECTIONS {\n'
    for i, (name, address) in enumerate(zip(names, ADDRESSES)):
        linker += f' .function{i} {address:#x} : {{ *(.text.{name}) }}\n'
    linker += ' /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }\n}\n'
    (build / 'compare.ld').write_text(linker)
    command(['ld.lld', '-T', build / 'compare.ld', '--entry=nova3_000d9968',
             build / 'arm.o', '-o', build / 'arm.elf'], commands)
    original, original_symbols = load_image(reference)
    candidate, candidate_symbols = load_image(build / 'arm.elf')
    original_names = [
        'Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeSendData',
        'InAppBilling_GetItemInfoArray', 'InAppBilling_GetItemInfoByID',
        '_ZN7_JNIEnv16CallObjectMethodEP8_jobjectP10_jmethodIDz']
    comparisons = []
    for address, size, original_name, name in zip(ADDRESSES, SIZES, original_names, names):
        assert original_symbols[original_name] == (address, size)
        candidate_address, candidate_size = candidate_symbols[name]
        assert candidate_address == address
        ref = code_at(original, address, size)
        owned = code_at(candidate, address, candidate_size)
        comparisons.append({'address': hex(address), 'reference_symbol': original_name,
                            'candidate_symbol': name, 'reference_size': size,
                            'candidate_size': candidate_size,
                            'reference_code_sha256': hashlib.sha256(ref).hexdigest(),
                            'candidate_code_sha256': hashlib.sha256(owned).hexdigest(),
                            'result': 'MATCHED' if ref == owned else 'NOT MATCHED'})
    lib = C.CDLL(str(build / 'test.so'))
    forward = lib.nova3_test_forward
    forward.argtypes = [C.c_uint, C.c_size_t, C.c_size_t, C.c_size_t,
                        C.c_size_t, C.POINTER(C.c_uint64)]
    forward.restype = None
    variadic = lib.nova3_test_variadic
    variadic.argtypes = [C.c_uint, C.c_uint32, C.POINTER(C.c_uint64)]
    variadic.restype = None
    rng = random.Random(68)
    seeds = [0, 1, 0x7fffffff, 0x80000000, 0xffffffff] + [rng.getrandbits(32) for _ in range(64)]
    oracles = [Oracle(original), Oracle(candidate)]
    transcript = hashlib.sha256()
    forwarding_cases = variadic_cases = 0
    for which in range(3):
        for seed in seeds:
            first, second, third = seed, DATA + 0x500, seed ^ 0x12345678
            result = seed ^ 0xa5a5a5a5
            out = (C.c_uint64 * 16)()
            forward(which, first, second, third, result, out)
            regs = [first, second, third, seed ^ 0xdeadbeef]
            for oracle in oracles:
                assert oracle.run(which, regs, result) == list(out[:4])
            transcript.update(struct.pack('<4Q', *out[:4]))
            forwarding_cases += 1
    for scenario in range(7):
        for seed in seeds:
            out = (C.c_uint64 * 16)()
            variadic(scenario, seed, out)
            result = 0 if seed & 1 else (0x50000000 | (seed & 0xffffff))
            args = arguments(scenario, seed)
            expected = [seed, seed ^ 0x55aa55aa, result, len(args), *argument_bits(args)]
            assert list(out[:4 + len(args)]) == expected
            for oracle in oracles:
                actual = oracle.run(3, [DATA, seed, seed ^ 0x55aa55aa, 0], result, args)
                assert actual == expected
            transcript.update(struct.pack('<' + 'Q' * len(expected), *expected))
            variadic_cases += 1
    return {
        'reference_sha256': REFERENCE_HASH,
        'source_sha256': digest(source), 'interface_sha256': digest(header),
        'test_sha256': digest(test), 'verification_script_sha256': digest(__file__),
        'compiler': subprocess.check_output(['clang++', '--version'], text=True).splitlines()[0],
        'host_compiler': subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
        'linker': subprocess.check_output(['ld.lld', '--version'], text=True).strip(),
        'versions': {m: importlib.metadata.version(m) for m in ('unicorn', 'pyelftools')},
        'arm_flags': arm_flags, 'comparison_linker_script': linker,
        'comparisons': comparisons,
        'differential': {'forwarding_cases': forwarding_cases, 'variadic_cases': variadic_cases,
                         'backends': ['reference ARM', 'newly compiled ARM', 'newly compiled host'],
                         'transcript_sha256': transcript.hexdigest(),
                         'scope': 'Adapters only; billing targets and JNI V callback are modeled. '
                                  'Checks argument/result bits, call count, reference mutation, '
                                  'restored stack and ARM callee-saved registers. '
                                  'No JVM, billing implementation, exceptions or game integration.'},
        'commands': commands,
    }


def main():
    if not __debug__:
        raise SystemExit('FAIL: run without Python -O; verification assertions are required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--build-dir', type=Path)
    args = parser.parse_args()
    if args.build_dir:
        args.build_dir.mkdir(parents=True, exist_ok=True)
        report = verify(args.reference, args.build_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='nova3-task68-') as directory:
            report = verify(args.reference, Path(directory))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
