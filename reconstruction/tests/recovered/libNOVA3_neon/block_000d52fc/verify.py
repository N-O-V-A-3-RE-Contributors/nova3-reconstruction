#!/usr/bin/env python3
"""Offline task65 verification. Never exports reference bytes or downloads files.

Host tests require only a C++17 compiler. --reference additionally requires the
preexisting pyelftools/Unicorn packages, clang++ and ld.lld for ARM verification.
All build products and aggregate reports belong in a disposable directory.
"""
import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[5]
SOURCE = ROOT / 'reconstruction/recovered/libNOVA3_neon/block_000d52fc.cpp'
HEADER = SOURCE.with_suffix('') / 'api.h'
TEST = ROOT / 'reconstruction/tests/recovered/libNOVA3_neon/block_000d52fc_test.cpp'
REFERENCE_HASH = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
JNI = 'Java_com_gameloft_android_GAND_GloftN3LT_iab_InAppBilling_nativeSet'
FUNCTIONS = [(0xd52fc, 4, 'InAppBilling_getTotalItems', 'count'),
             (0xd5300, 76, 'InAppBilling_FreeArrayMem', 'free'),
             (0xd57d8, 8, JNI + 'IABObject', 'iab'),
             (0xd58b8, 8, JNI + 'Context', 'context')]
DEP_PREFIX = '_ZN5nova39recovered14block_000d52fc12dependencies'
DEPS = {DEP_PREFIX + '15get_total_itemsEv': 0xd52d8,
        DEP_PREFIX + '14set_iab_objectEP8_jobject': 0xd5700,
        DEP_PREFIX + '11set_contextEP8_jobject': 0xd57e0, '_ZdaPv': 0x69b7fc}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, **kwargs):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f'command failed ({result.returncode}): {command}\n{result.stdout}{result.stderr}')
    return result


def oracle(reference_path, candidate_path, host_path):
    from elftools.elf.elffile import ELFFile
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
        UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
        UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
        UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_CPSR)
    assert digest(reference_path) == REFERENCE_HASH, 'wrong local reference'
    registers = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3]
    saved = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
             UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
    saved_values = [0xa0000000 + i * 0x1001 for i in range(8)]
    array, children, stack, stop = 0x2001000, 0x2002000, 0x3008000, 0x300fffc

    def load(path):
        # Reference bytes remain solely in local memory. Only metadata is emitted.
        elf = ELFFile(io.BytesIO(Path(path).read_bytes()))
        assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian
        machine = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        pages = set()

        def map_range(address, size):
            for page in range(address & ~4095, (address + size + 4095) & ~4095, 4096):
                pages.add(page)

        for segment in elf.iter_segments():
            if segment['p_type'] == 'PT_LOAD':
                map_range(segment['p_vaddr'], segment['p_memsz'])
        for address in DEPS.values():
            map_range(address, 4)
        map_range(0x2000000, 0x10000)
        map_range(0x3000000, 0x10000)
        # Coalesce pages: mapping thousands individually exhausts Unicorn's
        # physical-section table even though the total memory is modest.
        ordered = sorted(pages)
        first = previous = ordered[0]
        for page in ordered[1:] + [ordered[-1] + 8192]:
            if page != previous + 4096:
                machine.mem_map(first, previous + 4096 - first)
                first = page
            previous = page
        for segment in elf.iter_segments():
            if segment['p_type'] == 'PT_LOAD':
                machine.mem_write(segment['p_vaddr'], segment.data())
        symbols = elf.get_section_by_name('.symtab')
        for address, size, name, _ in FUNCTIONS:
            symbol, = symbols.get_symbol_by_name(name)
            assert symbol['st_value'] == address and symbol['st_info']['type'] == 'STT_FUNC'
            if path == reference_path:
                assert symbol['st_size'] == size
        if path == candidate_path:
            assert all(not section.num_relocations() for section in elf.iter_sections()
                       if hasattr(section, 'num_relocations'))
            assert not any(section['sh_type'] == 'SHT_DYNAMIC' for section in elf.iter_sections())
        return machine, elf

    original, reference_elf = load(reference_path)
    candidate, source_elf = load(candidate_path)
    state = {}

    def dependency_hook(machine, address, size, user):
        args = [machine.reg_read(reg) for reg in registers]
        if address == 0xd52d8:
            state['trace'].append(('C', state['count'] & 0xffffffff))
            result = state['count'] & 0xffffffff
        elif address == 0x69b7fc:
            assert args[0] in state['ids'], 'unexpected delete address'
            state['trace'].append(('D', state['ids'][args[0]]))
            state['count'] = -17  # detect an extra count query after deletion
            result = 0xa5a55a5a  # ignored by the void cleanup API
        else:
            state['trace'].append(('I' if address == 0xd5700 else 'X', args[0]))
            result = 0xa5a55a5a
        for reg in registers[1:] + [UC_ARM_REG_R12]:
            machine.reg_write(reg, 0xcccccccc)
        machine.reg_write(UC_ARM_REG_R0, result)
        machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

    def read_hook(machine, access, address, size, value, user):
        if array <= address < array + 66 * 4:
            assert size == 4 and (address - array) % 4 == 0
            state['reads'].append((address - array) // 4)

    for machine in (original, candidate):
        for address in DEPS.values():
            machine.hook_add(UC_HOOK_CODE, dependency_hook, begin=address, end=address)
        machine.hook_add(UC_HOOK_MEM_READ, read_hook)

    def execute(machine, entry, args, count=0, mask=0, null_outer=False):
        state.update(trace=[], reads=[], count=count, ids={})
        slots = count + 2 if count > 0 else 3
        contents = b''
        if entry == 0xd5300:
            pointers = [children + 16 * i if i >= max(count, 0) or (i < 64 and mask & (1 << i)) else 0
                        for i in range(slots)]
            contents = struct.pack('<' + 'I' * slots, *pointers)
            machine.mem_write(array, contents)
            state['ids'] = {p: i for i, p in enumerate(pointers) if p}
            state['ids'][array] = slots
        machine.reg_write(UC_ARM_REG_CPSR, 0x10)
        for reg, value in zip(registers, args):
            machine.reg_write(reg, value)
        for reg, value in zip(saved, saved_values):
            machine.reg_write(reg, value)
        machine.reg_write(UC_ARM_REG_SP, stack)
        machine.reg_write(UC_ARM_REG_LR, stop)
        machine.emu_start(entry, stop, count=3000)
        assert machine.reg_read(UC_ARM_REG_PC) == stop, 'instruction budget exhausted'
        assert machine.reg_read(UC_ARM_REG_SP) == stack
        assert [machine.reg_read(reg) for reg in saved] == saved_values
        if entry == 0xd52fc:
            state['trace'].append(('R', machine.reg_read(UC_ARM_REG_R0)))
        if entry == 0xd5300:
            assert state['reads'] == list(range(max(count, 0))), state
            assert bytes(machine.mem_read(array, len(contents))) == contents
        else:
            assert state['reads'] == []
        return ','.join(f'{kind}:{value}' for kind, value in state['trace'])

    rng = random.Random(65)
    cases = []
    for value in [-0x80000000, -100, -1, 0, 1, 0x7fffffff] + [rng.randrange(-2**31, 2**31) for _ in range(256)]:
        cases.append(('count', f'count {value}', [0x87654321, 0, 0, 0], value, 0, False))
    for count in (-0x80000000, -100, -1, 0):
        for null in (False, True):
            cases.append(('free', f'free {count} 7 {int(null)}',
                          [0 if null else array, 0, 0, 0], count, 7, null))
    for count in range(1, 11):
        for mask in range(1 << count):
            cases.append(('free', f'free {count} {mask} 0', [array, 0, 0, 0], count, mask, False))
    for mask in (0, 2**64-1, 0xaaaaaaaaaaaaaaaa, 0x5555555555555555):
        cases.append(('free', f'free 64 {mask} 0', [array, 0, 0, 0], 64, mask, False))
    for mode in ('iab', 'context'):
        arguments = [(env, receiver, value) for env in (0, 0x12345678, 0xffffffff)
                     for receiver in (0, 0x87654321) for value in (0, 1, 0x80000000, 0xffffffff)]
        arguments += [tuple(rng.randrange(2**32) for _ in range(3)) for _ in range(128)]
        for env, receiver, value in arguments:
            cases.append((mode, f'{mode} {env} {receiver} {value}', [env, receiver, value, 0xdeadbeef], 0, 0, False))
    counts = {}
    payload, expected = [], []
    for mode, command, args, count, mask, null in cases:
        address = next(address for address, _, _, key in FUNCTIONS if key == mode)
        reference_result = execute(original, address, args, count, mask, null)
        assert execute(candidate, address, args, count, mask, null) == reference_result
        payload.append(command)
        expected.append(reference_result)
        counts[mode] = counts.get(mode, 0) + 1
    inputs = '\n'.join(payload) + '\n'
    host = run([host_path, '--probe'], input=inputs)
    assert host.stdout.splitlines() == expected, 'host differs from original ARM trace'
    comparisons = []
    for address, size, name, _ in FUNCTIONS:
        symbol, = source_elf.get_section_by_name('.symtab').get_symbol_by_name(name)
        rebuilt = bytes(candidate.mem_read(address, symbol['st_size']))
        reference = bytes(original.mem_read(address, size))
        identical = rebuilt == reference
        comparisons.append(dict(address=hex(address), symbol=name,
            reference_size=size, candidate_size=len(rebuilt),
            reference_code_sha256=hashlib.sha256(reference).hexdigest(),
            candidate_code_sha256=hashlib.sha256(rebuilt).hexdigest(),
            exact_bytes=identical, status='MATCHED' if identical else 'NOT MATCHED'))
    assert digest(reference_path) == REFERENCE_HASH, 'reference changed'
    return dict(reference_sha256=REFERENCE_HASH, differential_cases=counts,
                total_cases=len(cases), input_sha256=hashlib.sha256(inputs.encode()).hexdigest(),
                trace_sha256=hashlib.sha256(host.stdout.encode()).hexdigest(),
                method='Original A32, compiled A32 and host C++ dependency traces; callees intercepted',
                assembly_comparisons=comparisons,
                packages={p: importlib.metadata.version(p) for p in ('unicorn', 'pyelftools')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    build = args.build_dir or Path(tempfile.mkdtemp(prefix='nova65-'))
    build.mkdir(parents=True, exist_ok=True)
    host, sanitized, arm_object, arm_elf = [build / name for name in ('test', 'sanitized', 'source.o', 'source.elf')]
    base = ['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pedantic']
    host_command = base + ['-O2', str(SOURCE), str(TEST), '-o', str(host)]
    sanitize_command = base + ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                               '-fno-pie', '-no-pie', str(SOURCE), str(TEST), '-o', str(sanitized)]
    run(host_command)
    host_output = run([host]).stdout.strip()
    run(sanitize_command)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
    sanitized_output = run([sanitized], env=env).stdout.strip()
    report = dict(schema_version=1, source_sha256=digest(SOURCE), header_sha256=digest(HEADER),
                  test_sha256=digest(TEST), verification_script_sha256=digest(__file__),
                  host_compiler=run(['g++', '--version']).stdout.splitlines()[0],
                  commands=[host_command, sanitize_command], host_result=host_output,
                  host_exit_code=0,
                  sanitizer_result=sanitized_output, sanitizer_exit_code=0,
                  sanitizer_environment={'ASAN_OPTIONS': 'detect_leaks=0', 'UBSAN_OPTIONS': 'halt_on_error=1'},
                  leak_check='NOT RUN: LeakSanitizer cannot operate under session ptrace',
                  machine_code_status='UNVERIFIED (no reference argument)')
    if args.reference:
        arm_command = ['clang++', '--target=armv7a-none-eabi', '-std=c++17', '-O2', '-marm',
                       '-mcpu=cortex-a8', '-fno-exceptions', '-fno-rtti', '-ffunction-sections',
                       '-c', str(SOURCE), '-o', str(arm_object)]
        run(arm_command)
        # Place only newly compiled functions at their reference VAs and resolve
        # dependency branches to reference addresses. No original code is linked.
        # This research ELF is for comparison, not a runnable billing subsystem.
        linker_script = build / 'comparison.ld'
        sections = '\n'.join(f'. = {address:#x}; .function_{i} : {{ *(.text.{name}) }}'
                             for i, (address, _, name, _) in enumerate(FUNCTIONS))
        definitions = '\n'.join(f'{name} = {address:#x};' for name, address in DEPS.items())
        linker_script.write_text('ENTRY(InAppBilling_getTotalItems)\n' + definitions + '\nSECTIONS {\n' + sections +
                                 '\n/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }\n}\n')
        link_command = ['ld.lld', '-m', 'armelf', '-T', str(linker_script), str(arm_object), '-o', str(arm_elf)]
        run(link_command)
        report.update(arm_compiler=run(['clang++', '--version']).stdout.splitlines()[0],
                      arm_linker=run(['ld.lld', '--version']).stdout.strip(),
                      arm_compile_exit_code=0, arm_link_exit_code=0,
                      linker_script_sha256=digest(linker_script), arm_elf_sha256=digest(arm_elf))
        report['commands'] += [arm_command, link_command]
        report['oracle'] = oracle(args.reference, arm_elf, host)
        report['oracle_exit_code'] = 0
        report['machine_code_status'] = 'Per-function comparison; see oracle.assembly_comparisons'
    output = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.write_text(output)
    print(output, end='')


if __name__ == '__main__':
    main()
