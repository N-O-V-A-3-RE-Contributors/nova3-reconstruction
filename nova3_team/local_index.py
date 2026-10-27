"""Build an allow-listed, metadata-only ledger from private, locally held Ghidra exports.

Only exported boundaries and reference file checksums leave this scanner. Never
copy raw code bytes, pseudocode, Java, screenshots, decompiler output, or assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

HEX64=re.compile(r'^[0-9a-f]{64}$')
HEX_ADDR=re.compile(r'^[0-9a-fA-F]{4,16}$')


def collect_program(data, program, *, library, ghidra_version, limit=32,
                    excluded_addresses=(), disassemble=False):
    """Read metadata from an already open, read-only Ghidra Program.

    This stricter alternative to the legacy export scanner never decompiles or
    saves the program. The caller must open it read-only and owns its lifecycle.
    Require an existing instruction at the entry, a contiguous Ghidra body
    exactly matching an ELF STT_FUNC extent, explicit TMode and a full byte
    comparison against the independently read ELF. Bounds can still originate
    from the same imported symbol: this establishes NO behavioural/source match.

    With disassemble=True, try at most limit*4 symbol extents of at most 256
    bytes in a transaction that is ALWAYS rolled back. Require complete bounded
    instruction coverage instead of a saved body; mode then comes from ELF.
    This permits additional analysis without altering the persistent project.

    Only allowlisted coordination metadata is returned. Reject mismatched
    program identity/architecture before inspecting functions. Missing candidate
    evidence is counted, never converted into a guessed boundary. Exceptions
    abort the operation rather than returning a partial ledger.
    """
    if type(disassemble) is not bool:
        raise ValueError('disassemble must be boolean')
    transaction = program.startTransaction('temporary function inventory') if disassemble else None
    try:
        return _collect_program(data, program, library=library, ghidra_version=ghidra_version,
                                limit=limit, excluded_addresses=excluded_addresses,
                                disassemble=disassemble)
    finally:
        if transaction is not None:
            program.endTransaction(transaction, False)


def _disassemble_extent(program, entry, size, thumb):
    """Bound disassembly to one ELF extent; never run global autoanalysis."""
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.program.model.address import AddressSet
    from ghidra.util.task import TaskMonitor
    from java.math import BigInteger

    body = AddressSet(entry, entry.add(size - 1))
    context = program.getProgramContext()
    register = program.getRegister('TMode')
    mode = context.getValue(register, entry, False)
    if mode is not None and str(mode) != str(int(thumb)):
        return None  # Never replace conflicting saved instruction/context evidence.
    if mode is None:
        context.setValue(register, entry, entry, BigInteger.ONE if thumb else BigInteger.ZERO)
    command = DisassembleCommand(entry, body, True)
    command.enableCodeAnalysis(False)
    if not command.applyTo(program, TaskMonitor.DUMMY):
        return None
    return body


def _collect_program(data, program, *, library, ghidra_version, limit,
                     excluded_addresses, disassemble):
    from . import indexer

    if type(limit) is not int or not 1 <= limit <= 256:
        raise indexer.LedgerError('candidate limit must be between 1 and 256')
    excluded = set(excluded_addresses)
    for address in excluded:
        indexer._integer(address, 'excluded address')
    if not isinstance(ghidra_version, str) or not indexer._TOOL_VERSION.fullmatch(ghidra_version):
        raise indexer.LedgerError('numeric Ghidra version required')
    private = indexer.build_ledger(data)
    binary = private['binary']
    if str(program.getExecutableSHA256()).lower() != binary['sha256']:
        raise indexer.LedgerError('Ghidra program binary hash mismatch')
    architecture = str(program.getLanguageID())
    order = 'LE' if binary['endianness'] == 'little' else 'BE'
    if not architecture.startswith(f'ARM:{order}:32:'):
        raise indexer.LedgerError('Ghidra program architecture mismatch')
    base = indexer._integer(int(program.getImageBase().getOffset()), 'image base')
    mode_register = program.getRegister('TMode')
    if mode_register is None:
        raise indexer.LedgerError('Ghidra program has no ARM TMode register')
    manager = program.getFunctionManager()
    listing = program.getListing()
    memory = program.getMemory()
    context = program.getProgramContext()
    space = program.getAddressFactory().getDefaultAddressSpace()
    records, hashes, skipped = [], {}, {}
    attempts = 0

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for function in private['functions']:
        address, size = function['address'], function['size']
        if address in excluded:
            continue
        if not size or size > indexer.MAX_PUBLISHED_FUNCTION_SIZE or function['size_conflict']:
            skip('invalid_symbol_extent'); continue
        alignment = 2 if function['thumb'] else 4
        if address % alignment or size % alignment:
            skip('unaligned_extent'); continue
        if disassemble and size > 256:
            skip('outside_small_disassembly_scope'); continue
        indexer._integer(address + base + size - 1, 'rebased extent')
        entry = space.getAddress(address + base)
        if disassemble:
            if attempts >= limit * 4:
                break
            attempts += 1
            body = _disassemble_extent(program, entry, size, function['thumb'])
            if body is None:
                skip('disassembly_failed'); continue
            cursor = address + base
            instructions = listing.getInstructions(body, True)
            while instructions.hasNext():
                instruction = instructions.next()
                if int(instruction.getAddress().getOffset()) != cursor:
                    break
                cursor += int(instruction.getLength())
            if cursor != address + base + size:
                skip('incomplete_instruction_coverage'); continue
        else:
            found = manager.getFunctionAt(entry)
            if found is None or listing.getInstructionAt(entry) is None:
                skip('no_existing_function_instruction'); continue
            body = found.getBody()
            if (int(body.getNumAddressRanges()) != 1
                    or int(body.getMinAddress().getOffset()) != address + base
                    or int(body.getNumAddresses()) != size):
                skip('body_extent_disagrees'); continue
        mode = context.getValue(mode_register, entry, False)
        if mode is None or str(mode) not in ('0', '1') or bool(int(str(mode))) != function['thumb']:
            skip('missing_or_conflicting_mode'); continue
        symbol = function['symbols'][0]
        offset = symbol['file_offset']
        expected = data[offset:offset + size]
        observed = bytes(int(memory.getByte(entry.add(i))) & 255 for i in range(size))
        if observed != expected:
            skip('full_extent_mapping_disagrees'); continue
        calls = []
        instructions = listing.getInstructions(body, True)
        while instructions.hasNext():
            instruction = instructions.next()
            for reference in instruction.getReferencesFrom():
                if reference.getReferenceType().isCall():
                    calls.append({'from': str(reference.getFromAddress()),
                                  'to': str(reference.getToAddress()),
                                  'type': str(reference.getReferenceType())})
        records.append(dict(binary_sha256=binary['sha256'], architecture=architecture,
                            address=address, raw_value=address | int(function['thumb']),
                            thumb=function['thumb'], symbol=symbol['name'], symbol_size=size,
                            ghidra_image_base=base, ghidra_address=address + base,
                            ghidra_version=ghidra_version, byte_mapping_validated=True,
                            calls=calls))
        hashes[address] = hashlib.sha256(expected).hexdigest()
        if len(records) == limit:
            break
    result = indexer.build_coordination_ledger(data, library=library, ghidra_exports=records)
    for row in result['functions']:
        row['reference_code_sha256'] = hashes[row['address']]
        row['boundary_evidence'].update(byte_mapping='full_extent_compared',
                                        entry_instruction='present')
        if disassemble:
            row['boundary_confidence'] = 'elf_symbol_with_bounded_ghidra_disassembly_and_full_mapping_check'
            row['boundary_evidence'].update(instruction_coverage='complete_symbol_extent',
                                            mode='elf_symbol_thumb_bit',
                                            analysis='bounded_disassembly_rolled_back')
        else:
            row['boundary_confidence'] = 'elf_symbol_with_ghidra_body_and_full_mapping_check'
            row['boundary_evidence']['ghidra_body'] = 'contiguous_equal_symbol_extent'
    result['statistics']['project_scan'] = {
        'candidate_limit': limit, 'candidates_checked': len(records),
        'disassembly_attempts': attempts,
        'excluded_existing_addresses': len(excluded), 'skipped': skipped,
        'discovery': ('bounded transient disassembly; transaction rolled back' if disassemble
                      else 'existing Ghidra bodies only; no analysis or program writes')}
    return result


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while True:
            chunk=f.read(1024*1024)
            if not chunk: break
            h.update(chunk)
    return h.hexdigest()


def number(value, *, hex_default=False):
    if type(value) is int: return value
    if not isinstance(value,str): return None
    value=value.strip()
    if value.lower().startswith('0x') and re.fullmatch(r'0[xX][0-9a-fA-F]+',value):
        return int(value,16)
    if hex_default and re.fullmatch(r'[0-9a-fA-F]{1,16}',value):return int(value,16)
    if re.fullmatch(r'[0-9]{1,10}',value):return int(value)
    return None


def extract_bounds(data, filename):
    """Return only explicit numeric boundaries; NEVER guess from code length."""
    obj=data.get('function',data) if isinstance(data,dict) else None
    if not isinstance(obj,dict):return None
    addr=None
    for key in ('address','entry','entry_point','start','function_address','entryAddress'):
        if key in obj:
            addr=number(obj[key],hex_default=True)
            if addr is not None:break
    if addr is None and HEX_ADDR.fullmatch(filename): addr=int(filename,16)
    if addr is None:return None
    size=None
    for key in ('size','length','byte_size','body_size','function_size'):
        if key in obj:
            size=number(obj[key])
            if size is not None:break
    if size is None:
        for key in ('end','end_address','endAddress'):
            if key in obj:
                end=number(obj[key],hex_default=True)
                if end is not None:
                    size=end-addr
                    break
    if size is None or not (0 < size <= 1048576) or not (0<=addr<2**64):return None
    return addr,size


def collect(root, *, max_exports=100000):
    root=Path(root)
    sources=root/'analysis'
    exports=root/'generated'/'ghidra'/'exports'
    digest_to_name={}
    for so in sorted(sources.rglob('*.so')) if sources.exists() else []:
        if not so.is_file() or so.is_symlink():continue
        digest=sha256(so)
        digest_to_name[digest]=so.name
    refs={}
    functions={}
    scanned=0
    rejected=0
    for folder in sorted(exports.glob('*')) if exports.exists() else []:
        if not folder.is_dir() or not HEX64.fullmatch(folder.name.lower()) or folder.is_symlink():continue
        digest=folder.name.lower()
        if digest not in digest_to_name:continue  # Never trust an unverified directory name.
        refs[digest]={'library':digest_to_name[digest], 'sha256':digest,
                      'source':'locally hashed ELF; NOT redistributed'}
        for path in sorted(folder.glob('*.json')):
            scanned+=1
            if scanned>max_exports:raise ValueError('Too many Ghidra exports; refusing unbounded scan')
            if path.is_symlink() or not path.is_file() or path.stat().st_size>4*1024*1024:
                rejected+=1;continue
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                bounds=extract_bounds(data,path.stem)
            except (OSError,UnicodeError,ValueError,TypeError):
                rejected+=1;continue
            if bounds is None:
                rejected+=1;continue
            addr,size=bounds
            # Tool identification is evidence of a candidate function boundary,
            # not equivalence to a reconstructed implementation.
            key=(digest,addr)
            if key not in functions:
                functions[key]={ 'reference_sha256':digest, 'address':f'0x{addr:x}',
                                 'size':size, 'boundary_status':'tool_identified',
                                 'status':'pending', 'source':'private Ghidra export metadata' }
    return {'schema_version':1,'references':sorted(refs.values(),key=lambda x:x['sha256']),
            'functions':sorted(functions.values(),key=lambda x:(x['reference_sha256'],int(x['address'],16))),
            'statistics':{'ghidra_json_examined':scanned,'rejected_or_unbounded':rejected,
                          'boundaries_extracted':len(functions)},
            'notes':'Tool-identified boundaries, NOT matched C++ source, NOT a complete original-function census.'}


def main(argv=None):
    ap=argparse.ArgumentParser(description='Extract only ELF-validated Ghidra function metadata, never original binaries')
    ap.add_argument('--project',default='.')
    ap.add_argument('--out',default='coordination/function_ledger.json')
    ap.add_argument('--write',action='store_true')
    args=ap.parse_args(argv)
    root=Path(args.project).resolve()
    output=(root/args.out).resolve()
    if not output.is_relative_to(root):raise SystemExit('Output must be inside project')
    data=collect(root)
    print(json.dumps(data['statistics'],indent=2))
    if not args.write:
        print('Preview only. Use --write to save sanitised metadata (no raw binary data).')
        return
    if not data['functions']:
        print('No usable function sizes/boundaries found. No ledger file written.')
        return
    if output.exists():
        print(f'Existing ledger left untouched: {output}')
        return
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print(f'Created metadata ledger: {output} ({len(data["functions"])} functions)')

if __name__=='__main__':main()
