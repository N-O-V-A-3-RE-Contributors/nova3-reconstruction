#!/usr/bin/env python3
"""Metadata-only inventory of the Premium getInverse dependency; no recovery.

Run with --reference /local/path/libNOVA3_neon.so. The CLI requires the exact
named .symtab function at 0x000ebb08, size 776, and the pinned Premium hash.
--expected-sha256 permits synthetic ELF tests; it does not relax symbol bounds.
The Python API also accepts an explicit expected_size for small synthetic tests.

Only mapped code is decoded: $d literal pools remain data even inside st_size.
Immediate counts include explicit integer/FP operands (including branch targets),
with integer and FP values counted separately. Memory displacements and literal
contents are excluded. No bytes, assembly text, local paths or arithmetic from
the reference are emitted. Matrix-slot interpretation of r0/r1 displacements is
INFERRED; indexed accesses are reported separately, without guessing their range.

Compare/branch links are observed only within a basic block, with no intervening
flag clobber. Cross-block flag provenance remains UNKNOWN. These local links do
not establish a determinant path, behavioural equivalence or a reconstruction.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys

import capstone
from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs
from capstone import arm as arm
from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

SYMBOL = "_ZNK6glitch4core8CMatrix4IfE10getInverseERS2_"
ADDRESS = 0x000EBB08
SIZE = 776
REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"


class InventoryError(ValueError):
    """The requested evidence could not be established."""


def address(value: int) -> str:
    return f"0x{value:08x}"


def _read_window(blob: bytes, expected_size: int) -> tuple[dict, list[tuple]]:
    """Confirm the bound before reading it; never fall back to .dynsym/next VA."""
    elf = ELFFile(io.BytesIO(blob))
    if (elf.elfclass != 32 or not elf.little_endian
            or elf['e_machine'] != 'EM_ARM'
            or elf['e_type'] not in ('ET_DYN', 'ET_EXEC')):
        raise InventoryError("expected a linked little-endian ELF32 ARM reference")
    table = elf.get_section_by_name('.symtab')
    if table is None or table['sh_type'] != 'SHT_SYMTAB':
        raise InventoryError("cannot confirm boundary without .symtab")
    symbols = list(table.iter_symbols())
    hits = [s for s in symbols if s.name == SYMBOL]
    if len(hits) != 1 or hits[0]['st_info']['type'] != 'STT_FUNC':
        raise InventoryError(".symtab must contain one unique named STT_FUNC")
    symbol = hits[0]
    start = symbol['st_value'] & ~1
    size = symbol['st_size']  # The reported size always comes from .symtab.
    if start != ADDRESS or size != expected_size or not 0 < size <= SIZE:
        raise InventoryError(".symtab address/size does not confirm the requested boundary")
    section_index = symbol['st_shndx']
    if not isinstance(section_index, int) or not 0 < section_index < elf.num_sections():
        raise InventoryError(".symtab function has no defined section")
    section = elf.get_section(section_index)
    offset = start - section['sh_addr']
    if (section['sh_type'] != 'SHT_PROGBITS' or section['sh_flags'] & 6 != 6
            or offset < 0 or offset + size > section['sh_size']
            or section['sh_offset'] + offset + size > len(blob)):
        raise InventoryError(".symtab boundary is not within file-backed executable section")
    end = start + size
    for other in symbols:
        if (other.name != SYMBOL and other['st_info']['type'] == 'STT_FUNC'
                and other['st_shndx'] == section_index and other['st_size']):
            a = other['st_value'] & ~1
            if a < end and start < a + other['st_size']:
                raise InventoryError("overlapping .symtab function bounds")

    mappings: dict[int, str] = {}
    for s in symbols:
        kind = s.name.split('.', 1)[0]
        if (s['st_shndx'] == section_index and s['st_info']['type'] == 'STT_NOTYPE'
                and kind in ('$a', '$t', '$d') and s['st_value'] < end):
            va = s['st_value']
            if va in mappings and mappings[va] != kind:
                raise InventoryError("conflicting ARM mapping symbols")
            mappings[va] = kind
    previous = [va for va in mappings if va <= start]
    if not previous:
        raise InventoryError("mode cannot be confirmed from .symtab mapping symbols")
    mapping_start = max(previous)
    initial = mappings[mapping_start]
    expected_mode = '$t' if symbol['st_value'] & 1 else '$a'
    if initial != expected_mode:
        raise InventoryError("function mode disagrees with .symtab mapping symbols")
    mode = 'Thumb' if initial == '$t' else 'ARM'
    unit = 2 if mode == 'Thumb' else 4
    cuts = [start, *sorted(va for va in mappings if start < va < end), end]
    window = blob[section['sh_offset'] + offset:section['sh_offset'] + offset + size]
    code_ranges, data_ranges, chunks = [], [], []
    kind = initial
    for a, b in zip(cuts, cuts[1:]):
        kind = mappings.get(a, kind)
        row = {'address': address(a), 'size': b - a}
        if kind == '$d':
            data_ranges.append(row)
        elif kind == initial and a % unit == 0 and (b - a) % unit == 0:
            code_ranges.append(row)
            chunks.append((a, window[a - start:b - start], mode))
        else:
            raise InventoryError("mixed or unaligned code mapping in function boundary")
    metadata = {
        'symbol': symbol.name, 'address': address(start), 'size': size,
        'mode': mode, 'section': section.name,
        'symtab': {'value': address(symbol['st_value']), 'size': size,
                   'end_exclusive': address(end), 'section_index': section_index,
                   'mode_mapping_address': address(mapping_start)},
        'size_source': '.symtab',
        'window_sha256': hashlib.sha256(window).hexdigest(),
        'code_ranges': code_ranges, 'data_ranges': data_ranges,
    }
    return metadata, chunks


def _decode(chunks: list[tuple]) -> list:
    instructions = []
    for start, data, mode in chunks:
        decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM if mode == 'ARM' else CS_MODE_THUMB)
        decoder.detail = True
        decoded = list(decoder.disasm(data, start))
        if sum(i.size for i in decoded) != len(data):
            raise InventoryError("mapped code window did not decode completely")
        instructions.extend(decoded)
    return instructions


def _conditional(insn) -> bool:
    return insn.cc not in (arm.ARM_CC_AL, arm.ARM_CC_INVALID)


def _target(insn) -> int | None:
    if insn.operands and insn.operands[0].type == arm.ARM_OP_IMM:
        return insn.operands[0].imm
    return None


def _return(insn) -> bool:
    return (insn.id == arm.ARM_INS_BX and insn.operands
            and insn.operands[0].type == arm.ARM_OP_REG
            and insn.operands[0].reg == arm.ARM_REG_LR)


def _condition_inventory(instructions: list) -> dict:
    """Local NZCV producer/use observations, deliberately no determinant claim."""
    transfers = {arm.ARM_INS_B, arm.ARM_INS_BL, arm.ARM_INS_BLX, arm.ARM_INS_BX}
    comparisons = {arm.ARM_INS_CMP, arm.ARM_INS_CMN, arm.ARM_INS_TST, arm.ARM_INS_TEQ}
    leaders = set()
    for insn in instructions:
        if insn.id in transfers:
            leaders.add(insn.address + insn.size)
            if _target(insn) is not None:
                leaders.add(_target(insn) & ~1)
    branches, writes, returns = [], [], []
    compare = transfer = fp_compare = None
    previous_end = None
    for insn in instructions:
        if insn.address in leaders or insn.address != previous_end:
            compare = transfer = fp_compare = None
        previous_end = insn.address + insn.size

        def evidence() -> dict:
            return {
                'address': address(insn.address), 'condition_code': insn.cc,
                'compare_address': address(compare) if compare is not None else 'UNKNOWN',
                'flag_transfer_address': address(transfer) if transfer is not None else 'UNKNOWN',
            }

        if insn.id == arm.ARM_INS_B and _conditional(insn):
            branches.append(dict(evidence(), target=address(_target(insn))))
        if (insn.id == arm.ARM_INS_MOV and len(insn.operands) == 2
                and insn.operands[0].type == arm.ARM_OP_REG
                and insn.operands[0].reg == arm.ARM_REG_R0
                and insn.operands[1].type == arm.ARM_OP_IMM):
            writes.append(dict(evidence(), immediate=insn.operands[1].imm))
        if _return(insn):
            returns.append(address(insn.address))

        if insn.id in (arm.ARM_INS_VCMP, arm.ARM_INS_VCMPE):
            fp_compare = insn.address if not _conditional(insn) else None
        elif insn.id == arm.ARM_INS_VMSR:
            fp_compare = None
        # Capstone identifies this VMRS spelling as its FMSTAT alias on some
        # versions; verify the actual registers rather than mnemonic text.
        if (insn.id in (arm.ARM_INS_VMRS, arm.ARM_INS_FMSTAT) and len(insn.operands) == 2
                and all(o.type == arm.ARM_OP_REG for o in insn.operands)
                and insn.operands[0].reg == arm.ARM_REG_APSR_NZCV
                and insn.operands[1].reg == arm.ARM_REG_FPSCR):
            compare = fp_compare if not _conditional(insn) else None
            transfer = insn.address if compare is not None else None
        elif insn.update_flags:
            compare = insn.address if insn.id in comparisons and not _conditional(insn) else None
            transfer = None
        if insn.id in (arm.ARM_INS_BL, arm.ARM_INS_BLX):
            compare = transfer = fp_compare = None  # ABI call clobber, no callee inference.
    return {'conditional_branches': branches,
            'r0_immediate_writes': writes, 'bx_lr_addresses': returns}


def _memory_inventory(instructions: list) -> dict:
    """Single 32-bit direct r0/r1 accesses only; other forms are explicit gaps."""
    offsets = {0: {'load': set(), 'store': set()},
               1: {'load': set(), 'store': set()}}
    unresolved, pc_loads = [], []
    kinds = {arm.ARM_INS_LDR: 'load', arm.ARM_INS_STR: 'store',
             arm.ARM_INS_VLDR: 'load', arm.ARM_INS_VSTR: 'store'}
    for insn in instructions:
        memories = [o.mem for o in insn.operands if o.type == arm.ARM_OP_MEM]
        for mem in memories:
            if mem.base == arm.ARM_REG_PC:
                pc_loads.append(address(insn.address))
                continue
            base = {arm.ARM_REG_R0: 0, arm.ARM_REG_R1: 1}.get(mem.base)
            # VFP d-register accesses are 64-bit, not individual float slots.
            single = (insn.id in (arm.ARM_INS_LDR, arm.ARM_INS_STR)
                      or (insn.id in (arm.ARM_INS_VLDR, arm.ARM_INS_VSTR)
                          and insn.reg_name(insn.operands[0].reg).startswith('s')))
            if (base is not None and insn.id in kinds and single
                    and not mem.index and not insn.writeback
                    and 0 <= mem.disp < 64 and mem.disp % 4 == 0):
                offsets[base][kinds[insn.id]].add(mem.disp)
            else:
                unresolved.append(address(insn.address))
    seen = set().union(*(s for base in offsets.values() for s in base.values()))
    return {
        'index_offsets': sorted(x // 4 for x in seen),
        'base_register_displacements': [
            {'base_register': base, 'load_byte_offsets': sorted(values['load']),
             'store_byte_offsets': sorted(values['store'])}
            for base, values in offsets.items()],
        'unclassified_memory_addresses': sorted(set(unresolved)),
        'pc_relative_access_addresses': sorted(set(pc_loads)),
    }


def inventory(reference: Path, *, expected_sha256: str = REFERENCE_SHA256,
              expected_size: int = SIZE) -> dict:
    blob = reference.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != expected_sha256:
        raise InventoryError("reference SHA-256 mismatch")
    metadata, chunks = _read_window(blob, expected_size)
    instructions = _decode(chunks)
    end = ADDRESS + metadata['size']
    calls, unresolved_calls = [], []
    bl_targets, blx_targets = set(), set()
    integers, floating = set(), set()
    for insn in instructions:
        for operand in insn.operands:
            if operand.type == arm.ARM_OP_IMM:
                integers.add(operand.imm)
            elif operand.type == arm.ARM_OP_FP:
                floating.add(operand.fp.hex())
        if insn.id not in (arm.ARM_INS_BL, arm.ARM_INS_BLX):
            continue
        target = _target(insn)
        if target is None:
            unresolved_calls.append(address(insn.address))
        else:
            calls.append({'address': address(insn.address), 'target': address(target),
                          'inside_window': ADDRESS <= (target & ~1) < end})
            (bl_targets if insn.id == arm.ARM_INS_BL else blx_targets).add(target)

    def targets(values: set) -> list[dict]:
        return [{'address': address(t), 'inside_window': ADDRESS <= (t & ~1) < end}
                for t in sorted(values)]

    return dict(metadata, schema='nova3.matrix4-inverse-inventory/1',
                state='INVENTORIED', matching='NOT MATCHED',
                reference_sha256=digest, capstone_version=capstone.__version__,
                instruction_count=len(instructions),
                code_size=sum(i.size for i in instructions),
                bl_targets=targets(bl_targets), blx_targets=targets(blx_targets),
                calls=calls, unresolved_call_addresses=unresolved_calls,
                immediate_operands={'integer_values': sorted(integers),
                                    'floating_values_hex': sorted(floating),
                                    'distinct_count': len(integers) + len(floating)},
                element_accesses=_memory_inventory(instructions),
                return_condition_shape=_condition_inventory(instructions),
                determinant_path='UNKNOWN',
                hypotheses=[{'label': 'INFERRED',
                             'field': 'element_accesses.index_offsets'}])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--expected-sha256', default=REFERENCE_SHA256)
    args = parser.parse_args(argv)
    try:
        report = inventory(args.reference, expected_sha256=args.expected_sha256)
    except (InventoryError, ELFError, OSError, ValueError):
        # Do not echo exceptions containing paths, reference strings or bytes.
        print('inventory failed: hash, .symtab boundary, mapping or decoding unconfirmed',
              file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
