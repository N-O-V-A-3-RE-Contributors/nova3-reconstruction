#!/usr/bin/env python3
"""Offline task71 verification; emits metadata/hashes, never reference code.

Host verification needs g++. Optional --reference needs existing clang++, ld.lld,
pyelftools and Unicorn. Original bytes stay in memory in this research oracle;
the production source neither loads nor links an original executable.
"""
import argparse
import ctypes as C
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[5]
SOURCE = ROOT / 'reconstruction/recovered/libNOVA3_neon/block_000d3cd0.cpp'
HEADER = SOURCE.with_suffix('') / 'interface.h'
TEST = ROOT / 'reconstruction/tests/recovered/libNOVA3_neon/block_000d3cd0_test.cpp'
REFERENCE_HASH = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
FUNCTIONS = [
    (0xd3cd0, 4, 'nova3_installer_native_start',
     'Java_com_gameloft_android_GAND_GloftN3LT_installer_GameInstaller_nativeStart'),
    (0xd3d0c, 12, 'nova3_drm_debug_log', 'drmDebugLog'),
    (0xd4cd4, 4, 'nova3_billing_set_java_vm', 'InAppBilling_setJavaVM'),
    (0xd4cd8, 4, 'nova3_billing_launch', '_ZN12InAppBilling18launchInAppBillingEPKc'),
]
DEPENDENCIES = {'nova3_installer_start_dependency': 0xd3c08,
                'nova3_billing_set_vm_dependency': 0xd4cc0}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv, commands, **kwargs):
    argv = list(map(str, argv))
    p = subprocess.run(argv, capture_output=True, text=True, **kwargs)
    commands.append(dict(argv=argv, exit_code=p.returncode,
                         stdout=p.stdout.strip(), stderr=p.stderr.strip()))
    if p.returncode:
        raise RuntimeError(f'Command failed: {argv}\n{p.stdout}{p.stderr}')
    return p.stdout.strip()


def extract(path, names):
    from elftools.elf.elffile import ELFFile
    found = {}
    with open(path, 'rb') as f:
        elf = ELFFile(f)
        assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian
        for symbol in elf.get_section_by_name('.symtab').iter_symbols():
            if symbol.name not in names:
                continue
            assert symbol['st_info']['type'] == 'STT_FUNC'
            section = elf.get_section(symbol['st_shndx'])
            offset = symbol['st_value'] - section['sh_addr']
            f.seek(section['sh_offset'] + offset)
            code = f.read(symbol['st_size'])
            assert symbol.name not in found and len(code) == symbol['st_size']
            found[symbol.name] = (symbol['st_value'], code)
        assert set(found) == set(names), 'missing function symbol'
        if path.name == 'arm.elf':
            assert not any(s['sh_type'] == 'SHT_DYNAMIC' for s in elf.iter_sections())
            assert not any(s.num_relocations() for s in elf.iter_sections()
                           if hasattr(s, 'num_relocations'))
    return found


def differential(original, candidate, host):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
        UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
        UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
        UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_CPSR)
    args = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3]
    saved = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
             UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
    stack, sp, stop, data = 0x3000000, 0x3008000, 0x4000000, 0x2000000

    class Oracle:
        def __init__(self, functions):
            self.functions = functions
            self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
            pages = {address & ~4095 for address, code in functions}
            pages.update(address & ~4095 for address in DEPENDENCIES.values())
            for page in pages:
                self.uc.mem_map(page, 4096)
            for address, code in functions:
                assert (address & 4095) + len(code) <= 4096
                self.uc.mem_write(address, code)
            self.uc.mem_map(stack, 0x10000)
            self.uc.mem_map(stop, 4096)
            self.uc.mem_map(data, 4096)
            self.uc.mem_write(data, bytes(range(256)) * 16)
            self.uc.hook_add(UC_HOOK_CODE, self.hook)
            self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.memory)

        def memory(self, uc, access, address, size, value, _):
            # The sole reference memory effects are drmDebugLog's dead spill.
            assert sp - 256 <= address and address + size <= sp, 'external memory access'

        def hook(self, uc, address, size, _):
            if address in DEPENDENCIES.values():
                assert self.which in (0, 2) and not self.calls
                which = 1 if address == 0xd3c08 else 2
                assert which == (1 if self.which == 0 else 2)
                registers = [uc.reg_read(r) for r in args]
                self.trace = [1, which, *(
                    registers if which == 1 else [registers[0], 0, 0, 0]), self.result]
                for reg in args[1:] + [UC_ARM_REG_R12]:
                    uc.reg_write(reg, 0xcccccccc)
                uc.reg_write(UC_ARM_REG_R0, self.result)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
                self.calls += 1
            else:
                assert self.start <= address < self.end, 'unexpected control transfer'

        def execute(self, which, inputs, flags):
            self.which, self.result, self.calls = which, inputs[4], 0
            self.start, code = self.functions[which]
            self.end = self.start + len(code)
            self.trace = [0] * 7
            self.uc.mem_write(sp - 256, b'\xa5' * 512)
            self.uc.reg_write(UC_ARM_REG_CPSR, 0x10 | flags)
            for reg, value in zip(args, inputs): self.uc.reg_write(reg, value)
            for i, reg in enumerate(saved): self.uc.reg_write(reg, 0x76540000 + i)
            self.uc.reg_write(UC_ARM_REG_SP, sp)
            self.uc.reg_write(UC_ARM_REG_LR, stop)
            self.uc.emu_start(self.start, stop, count=100)
            assert self.uc.reg_read(UC_ARM_REG_PC) == stop, 'instruction budget exceeded'
            assert self.uc.reg_read(UC_ARM_REG_SP) == sp
            assert all(self.uc.reg_read(r) == 0x76540000 + i for i, r in enumerate(saved))
            assert self.calls == (1 if which in (0, 2) else 0)
            # The C++ void APIs do not promise r0, but these ARM builds retain it.
            assert self.uc.reg_read(UC_ARM_REG_R0) == (self.result if self.calls else inputs[0])
            assert self.uc.reg_read(UC_ARM_REG_CPSR) & 0xf0000000 == flags
            assert bytes(self.uc.mem_read(data, 4096)) == bytes(range(256)) * 16
            return self.trace

    reference = Oracle([original[f[3]] for f in FUNCTIONS])
    rebuilt = Oracle([candidate[f[2]] for f in FUNCTIONS])
    probe = C.CDLL(str(host)).nova3_task71_probe
    probe.argtypes = [C.c_uint, C.POINTER(C.c_uint32), C.POINTER(C.c_uint64)]
    probe.restype = None
    rng = random.Random(71)
    edge = [0, 1, 0x7fffffff, 0x80000000, 0xffffffff]
    cases = [[a, a ^ 0xabcdef01, a ^ 0x76543210, a ^ 0xffffffff, r]
             for a in edge for r in edge]
    cases += [[rng.getrandbits(32) for _ in range(5)] for _ in range(256)]
    transcript = hashlib.sha256()
    counts = {}
    for which, (_, _, name, _) in enumerate(FUNCTIONS):
        count = 0
        for values in cases:
            out = (C.c_uint64 * 7)()
            probe(which, (C.c_uint32 * 5)(*values), out)
            for bits in range(16):
                flags = bits << 28
                expected = reference.execute(which, values, flags)
                assert rebuilt.execute(which, values, flags) == expected
                assert list(out) == expected, 'host trace differs from original ARM'
                transcript.update(json.dumps([which, values, flags, expected]).encode())
                count += 1
        counts[name] = count
    return dict(cases_per_function=counts, total_cases=sum(counts.values()),
                transcript_sha256=transcript.hexdigest(),
                seed=71, flag_combinations=16,
                scope='Original ARM, compiled ARM and compiled host: r0-r3 arguments, r0 '
                      'result (ARM only for void APIs), dependency identity/count, NZCV flags, restored '
                      'SP/callee-saved registers, no external memory access. Callees are '
                      'intercepted; no JVM, installer, billing or game integration. '
                      'Dead stack scratch writes are intentionally outside equivalence.')


def verify(reference, build):
    commands = []
    common = ['-std=c++17', '-Wall', '-Wextra', '-Wpedantic', '-Werror']
    for name, extra in [('test', ['-O2']), ('release-test', ['-O2', '-DNDEBUG']),
                        ('sanitized-test', ['-O1', '-g', '-fsanitize=address,undefined',
                         '-fno-omit-frame-pointer', '-fno-pie', '-no-pie'])]:
        run(['g++', *common, *extra, SOURCE, TEST, '-o', build / name], commands)
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
        run([build / name], commands, env=env)
    report = dict(source_sha256=digest(SOURCE), header_sha256=digest(HEADER),
                  test_sha256=digest(TEST), verification_script_sha256=digest(__file__),
                  host_compiler=run(['g++', '--version'], commands).splitlines()[0],
                  leak_check='NOT RUN; ASAN_OPTIONS=detect_leaks=0 (session ptrace limitation)',
                  sanitizer_environment={'ASAN_OPTIONS': 'detect_leaks=0', 'UBSAN_OPTIONS': 'halt_on_error=1'},
                  machine_code_status='UNVERIFIED: no reference supplied', commands=commands)
    if reference is None:
        return report
    assert digest(reference) == REFERENCE_HASH, 'wrong reference SHA256'
    run(['g++', *common, '-O2', '-fPIC', '-shared', '-DNOVA3_SHARED_TEST',
         SOURCE, TEST, '-o', build / 'host.so'], commands)
    flags = ['--target=armv7a-none-eabi', '-mcpu=cortex-a8', '-marm',
             '-mfloat-abi=softfp', '-mfpu=neon', *common, '-O2',
             '-ffreestanding', '-ffunction-sections', '-fno-exceptions', '-fno-rtti',
             '-fno-unwind-tables', '-fno-asynchronous-unwind-tables']
    run(['clang++', *flags, '-c', SOURCE, '-o', build / 'arm.o'], commands)
    linker = '\n'.join(f'{name} = {address:#x};' for name, address in DEPENDENCIES.items())
    linker += '\nSECTIONS {\n'
    for i, (address, _, name, _) in enumerate(FUNCTIONS):
        linker += f' .function{i} {address:#x} : {{ *(.text.{name}) }}\n'
    linker += ' /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }\n}\n'
    (build / 'compare.ld').write_text(linker)
    run(['ld.lld', '-T', build / 'compare.ld', '--entry=nova3_installer_native_start',
         build / 'arm.o', '-o', build / 'arm.elf'], commands)
    original = extract(reference, [f[3] for f in FUNCTIONS])
    candidate = extract(build / 'arm.elf', [f[2] for f in FUNCTIONS])
    comparisons = []
    for address, size, name, original_name in FUNCTIONS:
        ref_address, ref_code = original[original_name]
        new_address, new_code = candidate[name]
        assert address == ref_address == new_address and len(ref_code) == size
        comparisons.append(dict(address=hex(address), reference_symbol=original_name,
            candidate_symbol=name, reference_size=size, candidate_size=len(new_code),
            reference_code_sha256=hashlib.sha256(ref_code).hexdigest(),
            candidate_code_sha256=hashlib.sha256(new_code).hexdigest(),
            result='MATCHED' if ref_code == new_code else 'NOT MATCHED'))
    report.update(reference_sha256=REFERENCE_HASH, arm_flags=flags,
                  arm_compiler=run(['clang++', '--version'], commands).splitlines()[0],
                  linker=run(['ld.lld', '--version'], commands),
                  comparison_linker_script=linker, comparisons=comparisons,
                  versions={p: importlib.metadata.version(p) for p in ('pyelftools', 'unicorn')},
                  differential=differential(original, candidate, build / 'host.so'),
                  machine_code_status='See per-function results; placement and absolute dependency '
                                      'symbols are comparison-only; no original code is linked')
    assert digest(reference) == REFERENCE_HASH, 'reference changed'
    report['reference_sha256_rechecked'] = True
    return report


def main():
    if not __debug__:
        raise SystemExit('Run without Python -O: verification assertions are required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.build_dir:
        args.build_dir.mkdir(parents=True, exist_ok=True)
        report = verify(args.reference, args.build_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='nova3-task71-') as directory:
            report = verify(args.reference, Path(directory))
    output = json.dumps(report, indent=2) + '\n'
    if args.report: args.report.write_text(output)
    print(output, end='')


if __name__ == '__main__':
    main()
