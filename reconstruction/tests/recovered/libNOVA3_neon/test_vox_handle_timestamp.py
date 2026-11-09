#!/usr/bin/env python3
"""Offline source-only tests; optional read-only Premium reference inspection.

No original executable is loaded or executed. Reference absence or optional
Python/ARM tooling absence is a reported SKIP (exit 0), never a match. Invalid
reference evidence is a failure. Host tests do not establish equivalence.
Builds use temporary system storage, removed on exit; output is metadata only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / 'reconstruction/recovered/libNOVA3_neon/vox_handle_timestamp.cpp'
HEADER = SOURCE.with_suffix('.h')
SYMBOLS = {
    '_ZNK3vox6Handle12GetTimeStampERjS1_': (0x196748, 20),
    '_ZN3vox6Handle12SetTimeStampEjj': (0x19675c, 12),
}

# Independent, literal expected results; no binary or private source fixtures.
HARNESS = r'''
#include "vox_handle_timestamp.h"
#include <cstdlib>
#include <cstdio>
static void check(bool ok) { if (!ok) { std::fputs("check failed\n", stderr); std::abort(); } }
struct Guarded { uint32_t before; vox::Handle h; uint32_t after; };
static Guarded fresh() {
    return {0x87654321u, {{0x11223344u,0x55667788u,0x99aabbccu,0xddeeff00u},
                         0x12345678u,0xabcdef01u},0x31415926u};
}
static void guards(const Guarded& v) {
    check(v.before==0x87654321u && v.after==0x31415926u);
    check(v.h.opaque_prefix[0]==0x11223344u && v.h.opaque_prefix[1]==0x55667788u);
    check(v.h.opaque_prefix[2]==0x99aabbccu && v.h.opaque_prefix[3]==0xddeeff00u);
}
int main() {
    const uint32_t pairs[][2] = {
        {0,0},{UINT32_MAX,UINT32_MAX},{0,UINT32_MAX},{UINT32_MAX,0},
        {0x12345678u,0xabcdef01u},{17,93},{17,93},{UINT32_MAX,0},{0,0}
    };
    auto v=fresh();
    for (const auto& p : pairs) {
        v.h.SetTimeStamp(p[0],p[1]);
        check(v.h.timestamp==p[0] && v.h.group==p[1]); guards(v);
        uint32_t a=0x55555555u,b=0xaaaaaaaau;
        const vox::Handle& c=v.h;
        c.GetTimeStamp(a,b);
        check(a==p[0] && b==p[1]);
        check(v.h.timestamp==p[0] && v.h.group==p[1]); guards(v);
    }
    // Every shared-external/receiver-field alias pair, including first output
    // aliasing the second source. Columns: output indices, final external,T,G.
    const uint32_t cases[][5] = {
        {0,0,0xabcdef01u,0x12345678u,0xabcdef01u},
        {0,1,0x12345678u,0xabcdef01u,0xabcdef01u},
        {0,2,0x12345678u,0x12345678u,0xabcdef01u},
        {1,0,0xabcdef01u,0x12345678u,0xabcdef01u},
        {1,1,0x77777777u,0xabcdef01u,0xabcdef01u},
        {1,2,0x77777777u,0x12345678u,0xabcdef01u},
        {2,0,0x12345678u,0x12345678u,0x12345678u},
        {2,1,0x77777777u,0x12345678u,0x12345678u},
        {2,2,0x77777777u,0x12345678u,0x12345678u}
    };
    for (const auto& c : cases) {
        v=fresh(); uint32_t external=0x77777777u;
        uint32_t* out[]={&external,&v.h.timestamp,&v.h.group};
        v.h.GetTimeStamp(*out[c[0]],*out[c[1]]);
        check(external==c[2] && v.h.timestamp==c[3] && v.h.group==c[4]); guards(v);
    }
    // Setter arguments may themselves be receiver fields: value semantics.
    v=fresh(); v.h.SetTimeStamp(v.h.group,v.h.timestamp);
    check(v.h.timestamp==0xabcdef01u && v.h.group==0x12345678u); guards(v);
    std::puts("timestamp host cases passed");
}
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def source_metadata():
    return {p.name: digest(p.read_bytes()) for p in (SOURCE, HEADER)}


def host_test(directory):
    compiler = shutil.which(os.environ.get('CXX', 'c++'))
    if not compiler:
        raise RuntimeError('host C++ compiler missing')
    harness = directory / 'test.cpp'
    harness.write_text(HARNESS)
    version = run([compiler, '--version']).splitlines()[0]
    records = []
    for optimization in ('-O0', '-O2'):
        flags = ['-std=c++17', optimization, '-fstrict-aliasing', '-Wall', '-Wextra', '-Werror']
        executable = directory / ('host' + optimization[1:])
        run([compiler, *flags, '-I', str(SOURCE.parent), str(SOURCE), str(harness), '-o', str(executable)])
        output = run([str(executable)])
        records.append(dict(flags=flags, result=output))
    return dict(COMPILES=True, TESTED=True, compiler=version, builds=records,
                source_sha256=source_metadata(), harness_sha256=digest(HARNESS.encode()),
                BEHAVIOURALLY_COMPARED='UNKNOWN', INSTRUCTION_WINDOW='NOT MATCHED',
                VERIFIED='UNKNOWN')


def symbol_window(elf, name, expected=None):
    table = elf.get_section_by_name('.symtab')
    if table is None:
        raise ValueError('missing independent symbol table')
    found = [s for s in table.iter_symbols() if s.name == name and s['st_info']['type'] == 'STT_FUNC']
    if len(found) != 1:
        raise ValueError('ambiguous or missing function symbol')
    symbol = found[0]
    address, size = symbol['st_value'], symbol['st_size']
    if expected is not None and (address, size) != expected:
        raise ValueError('unexpected exact function bounds')
    if address & 3 or size == 0 or size % 4:
        raise ValueError('not an aligned A32 window')
    section = elf.get_section(symbol['st_shndx'])
    offset = address - section['sh_addr']
    if not section['sh_flags'] & 4 or offset < 0 or offset + size > section['sh_size']:
        raise ValueError('function outside executable section')
    mappings = sorted((s['st_value'], s.name.split('.')[0]) for s in table.iter_symbols()
                      if s['st_shndx'] == symbol['st_shndx'] and s.name.split('.')[0] in ('$a', '$t', '$d'))
    preceding = [mode for at, mode in mappings if at <= address]
    if not preceding or preceding[-1] != '$a':
        raise ValueError('A32 mapping not independently established')
    if any(address < at < address + size and mode != '$a' for at, mode in mappings):
        raise ValueError('mixed-state function')
    # Reject candidate/reference relocations inside the compared window.
    for relsec in elf.iter_sections():
        if relsec['sh_type'] not in ('SHT_REL', 'SHT_RELA'):
            continue
        if elf['e_type'] == 'ET_REL' and relsec['sh_info'] != symbol['st_shndx']:
            continue
        if any(address <= r['r_offset'] < address + size for r in relsec.iter_relocations()):
            raise ValueError('relocation affects instruction window')
    return section.data()[offset:offset + size]


def validate_accesses(code, address, getter, capstone):
    decoder = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM | capstone.CS_MODE_LITTLE_ENDIAN)
    decoder.detail = True
    instructions = list(decoder.disasm(code, address))
    # Register names are normalized to semantic roles. Assert every memory access
    # and exact sequencing, not merely the presence of the two receiver offsets.
    expected = ([('ldr', 'r3, [r0, #0x10]'), ('str', 'r3, [r1]'),
                 ('ldr', 'r3, [r0, #0x14]'), ('str', 'r3, [r2]'), ('bx', 'lr')]
                if getter else [('str', 'r2, [r0, #0x14]'),
                                ('str', 'r1, [r0, #0x10]'), ('bx', 'lr')])
    actual = [(i.mnemonic, i.op_str) for i in instructions]
    if sum(i.size for i in instructions) != len(code) or actual != expected:
        raise ValueError('reference field accesses/order or return differ from timestamp specification')


def reference_test(path, directory):
    if not path.is_file():
        return dict(status='SKIP', reason='reference not available', INSTRUCTION_WINDOW='NOT MATCHED')
    try:
        from elftools.elf.elffile import ELFFile
        import capstone
    except ImportError:
        return dict(status='SKIP', reason='optional pyelftools/capstone missing', INSTRUCTION_WINDOW='NOT MATCHED')
    reference_hash = digest(path.read_bytes())
    if reference_hash != '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf':
        raise ValueError('not the documented Premium retail reference SHA-256')
    windows = {}
    with path.open('rb') as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_ARM' or elf['e_type'] != 'ET_DYN':
            raise ValueError('reference is not linked little-endian ELF32 ARM')
        for name, bounds in SYMBOLS.items():
            code = symbol_window(elf, name, bounds)
            validate_accesses(code, bounds[0], 'GetTimeStamp' in name, capstone)
            windows[name] = code
    result = dict(reference_sha256=reference_hash, reference_structure='PASS',
                  bounds_mode_accesses_order='PASS', source_sha256=source_metadata(),
                  BEHAVIOURALLY_COMPARED='UNKNOWN', VERIFIED='UNKNOWN')
    compiler = shutil.which('clang++')
    if not compiler:
        return dict(result, status='SKIP', reason='optional clang++ missing', INSTRUCTION_WINDOW='NOT MATCHED')
    flags = ['--target=armv7-none-eabi', '-marm', '-O2', '-std=c++17',
             '-ffreestanding', '-fno-exceptions', '-fno-rtti']
    candidate = directory / 'candidate.o'
    result.update(compiler=run([compiler, '--version']).splitlines()[0], flags=flags,
                  compiler_sha256=digest(Path(compiler).resolve().read_bytes()),
                  original_compiler='UNKNOWN')
    # An installed compiler unable to target ARM is an optional-tool skip, not
    # equivalence. Host compilation above remains mandatory.
    try:
        run([compiler, *flags, '-c', str(SOURCE), '-o', str(candidate)])
    except subprocess.CalledProcessError:
        return dict(result, status='SKIP', reason='ARM candidate compilation unavailable', INSTRUCTION_WINDOW='NOT MATCHED')
    comparisons = {}
    with candidate.open('rb') as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_ARM' or elf['e_type'] != 'ET_REL':
            raise ValueError('invalid candidate ARM object')
        for name, reference in windows.items():
            code = symbol_window(elf, name)
            comparisons[name] = dict(equal=code == reference,
                                     reference_window_sha256=digest(reference),
                                     candidate_window_sha256=digest(code), candidate_size=len(code),
                                     reference_address=SYMBOLS[name][0], reference_size=SYMBOLS[name][1],
                                     state='A32', relocations_in_window=False)
    return dict(result, status='COMPARED', comparisons=comparisons,
                candidate_object_sha256=digest(candidate.read_bytes()),
                INSTRUCTION_WINDOW=('MATCHED' if all(c['equal'] for c in comparisons.values()) else 'NOT MATCHED'),
                scope='exact function windows only; not original objects, compiler provenance, full ABI or library equivalence')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nova-timestamp-') as temporary:
        directory = Path(temporary)
        print(json.dumps(dict(host=host_test(directory)), sort_keys=True))
        if args.reference is not None:
            print(json.dumps(dict(reference=reference_test(args.reference, directory)), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
