"""Evidence-inventory tests; synthetic code is source-owned, never game bytes."""
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
TOOL = ROOT / 'tools/compare/collada_matrix4_inverse_inventory.py'
spec = importlib.util.spec_from_file_location('inverse_inventory', TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def _branch(site: int, target: int, *, link=False, condition=14) -> int:
    """Encode a source-owned A32 branch for the fixture, without an assembler."""
    displacement = target - (site + 8)
    assert displacement % 4 == 0
    return ((condition << 28) | 0x0a000000 | (int(link) << 24)
            | ((displacement // 4) & 0x00ffffff))


def _elf(tmp_path, words, *, literal_words=(), size=None, value=None,
         mapping='$a', table_name='.symtab', duplicate=False, machine=40):
    """Minimal linked ELF32, including actual STT_FUNC and ARM mapping symbols."""
    code = struct.pack('<' + 'I' * len(words), *words)
    literals = struct.pack('<' + 'I' * len(literal_words), *literal_words)
    text = code + literals
    strings = bytearray(b'\0')

    def name(text):
        offset = len(strings)
        strings.extend(text.encode() + b'\0')
        return offset

    map_name, symbol_name, data_name = name(mapping), name(tool.SYMBOL), name('$d')
    symbols = [bytes(16), struct.pack('<IIIBBH', map_name, tool.ADDRESS, 0, 0, 0, 1)]
    if literal_words:
        symbols.append(struct.pack('<IIIBBH', data_name, tool.ADDRESS + len(code),
                                   0, 0, 0, 1))
    local_count = len(symbols)
    symbols.append(struct.pack('<IIIBBH', symbol_name,
                               tool.ADDRESS if value is None else value,
                               len(text) if size is None else size, 0x12, 0, 1))
    if duplicate:
        symbols.append(symbols[-1])
    symtab = b''.join(symbols)
    section_names = bytearray(b'\0')

    def section_name(text):
        offset = len(section_names)
        section_names.extend(text.encode() + b'\0')
        return offset

    names = [section_name(n) for n in ('.text', '.strtab', table_name, '.shstrtab')]
    blob = bytearray(52)

    def append(data):
        blob.extend(b'\0' * (-len(blob) % 4))
        offset = len(blob)
        blob.extend(data)
        return offset

    text_offset = append(text)
    strings_offset = append(strings)
    table_offset = append(symtab)
    names_offset = append(section_names)
    blob.extend(b'\0' * (-len(blob) % 4))
    shoff = len(blob)
    sections = [bytes(40)]
    sections.append(struct.pack('<10I', names[0], 1, 6, tool.ADDRESS,
                                text_offset, len(text), 0, 0, 4, 0))
    sections.append(struct.pack('<10I', names[1], 3, 0, 0,
                                strings_offset, len(strings), 0, 0, 1, 0))
    sections.append(struct.pack('<10I', names[2], 2 if table_name == '.symtab' else 11,
                                0, 0, table_offset, len(symtab), 2, local_count, 4, 16))
    sections.append(struct.pack('<10I', names[3], 3, 0, 0,
                                names_offset, len(section_names), 0, 0, 1, 0))
    blob.extend(b''.join(sections))
    ident = b'\x7fELF\x01\x01\x01' + bytes(9)
    blob[:52] = struct.pack('<16sHHIIIIIHHHHHH', ident, 3, machine, 1, 0, 0,
                            shoff, 0x05000000, 52, 0, 0, 40, 5, 4)
    path = tmp_path / 'synthetic.so'
    path.write_bytes(blob)
    return path, hashlib.sha256(blob).hexdigest()


def _report(path, digest, size):
    return tool.inventory(path, expected_sha256=digest, expected_size=size)


def _assert_metadata_only(report):
    """Allowlist the schema, so a future disassembly/byte dump fails the test."""
    assert set(report) == {
        'symbol', 'address', 'size', 'mode', 'section', 'symtab', 'size_source',
        'window_sha256', 'code_ranges', 'data_ranges', 'schema', 'state', 'matching',
        'reference_sha256', 'capstone_version', 'instruction_count', 'code_size',
        'bl_targets', 'blx_targets', 'calls', 'unresolved_call_addresses',
        'immediate_operands', 'element_accesses', 'return_condition_shape',
        'determinant_path', 'hypotheses',
    }
    assert report['determinant_path'] == 'UNKNOWN'
    assert report['matching'] == 'NOT MATCHED'
    assert report['state'] == 'INVENTORIED'
    assert report['hypotheses'] == [
        {'label': 'INFERRED', 'field': 'element_accesses.index_offsets'}]
    allowed_keys = {
        'symtab': {'value', 'size', 'end_exclusive', 'section_index', 'mode_mapping_address'},
        'immediate_operands': {'integer_values', 'floating_values_hex', 'distinct_count'},
        'element_accesses': {'index_offsets', 'base_register_displacements',
                             'unclassified_memory_addresses', 'pc_relative_access_addresses'},
        'return_condition_shape': {'conditional_branches', 'r0_immediate_writes',
                                   'bx_lr_addresses'},
    }
    for key, keys in allowed_keys.items():
        assert set(report[key]) == keys
    row_keys = {
        'code_ranges': {'address', 'size'}, 'data_ranges': {'address', 'size'},
        'bl_targets': {'address', 'inside_window'}, 'blx_targets': {'address', 'inside_window'},
        'calls': {'address', 'target', 'inside_window'},
    }
    for key, keys in row_keys.items():
        for row in report[key]:
            assert set(row) == keys
    for row in report['element_accesses']['base_register_displacements']:
        assert set(row) == {'base_register', 'load_byte_offsets', 'store_byte_offsets'}
    condition_keys = {'address', 'condition_code', 'compare_address', 'flag_transfer_address'}
    for row in report['return_condition_shape']['conditional_branches']:
        assert set(row) == condition_keys | {'target'}
    for row in report['return_condition_shape']['r0_immediate_writes']:
        assert set(row) == condition_keys | {'immediate'}

    def visit(value):
        assert not isinstance(value, (bytes, bytearray))
        if isinstance(value, dict):
            for k, v in value.items():
                assert k not in {'bytes', 'instruction_bytes', 'disassembly', 'op_str', 'mnemonic'}
                visit(v)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(report)
    assert json.loads(json.dumps(report)) == report


def test_tiny_a32_window(tmp_path):
    a = tool.ADDRESS
    outside = a + 0x1000
    words = [0xe5902004,  # ldr r2, [r0, #4]
             0xe581203c,  # str r2, [r1, #60]
             0xe3520000,  # cmp r2, #0
             _branch(a + 12, a + 32, condition=0),
             _branch(a + 16, outside, link=True),
             _branch(a + 20, a, link=True),
             _branch(a + 24, outside, link=True),
             0xe3a00001,  # mov r0, #1
             0xe12fff1e]  # bx lr
    # A fake call in mapped DATA must never become a callee or an immediate.
    fake_target = a + 0x2000
    data = [_branch(a + 36, fake_target, link=True), 0x12345678]
    path, digest = _elf(tmp_path, words, literal_words=data)
    report = _report(path, digest, 44)
    _assert_metadata_only(report)
    assert report['symbol'] == tool.SYMBOL
    assert report['address'] == '0x000ebb08'
    assert report['reference_sha256'] == digest
    assert report['size'] == report['symtab']['size'] == 44
    assert report['size_source'] == '.symtab'
    assert report['mode'] == 'ARM' and report['section'] == '.text'
    assert report['instruction_count'] == 9 and report['code_size'] == 36
    assert report['data_ranges'] == [{'address': tool.address(a + 36), 'size': 8}]
    assert report['bl_targets'] == [
        {'address': tool.address(a), 'inside_window': True},
        {'address': tool.address(outside), 'inside_window': False}]
    assert [c['target'] for c in report['calls']] == [
        tool.address(outside), tool.address(a), tool.address(outside)]
    assert report['blx_targets'] == []
    assert report['unresolved_call_addresses'] == []
    assert report['immediate_operands'] == {
        'integer_values': [0, 1, a, a + 32, outside],
        'floating_values_hex': [], 'distinct_count': 5}
    assert report['element_accesses']['index_offsets'] == [1, 15]
    assert report['element_accesses']['base_register_displacements'] == [
        {'base_register': 0, 'load_byte_offsets': [4], 'store_byte_offsets': []},
        {'base_register': 1, 'load_byte_offsets': [], 'store_byte_offsets': [60]}]
    assert report['return_condition_shape']['conditional_branches'] == [{
        'address': tool.address(a + 12), 'condition_code': tool.arm.ARM_CC_EQ,
        'target': tool.address(a + 32), 'compare_address': tool.address(a + 8),
        'flag_transfer_address': 'UNKNOWN'}]
    assert report['return_condition_shape']['bx_lr_addresses'] == [tool.address(a + 32)]


def test_vfp_compare_transfer_and_flag_clobber(tmp_path):
    a = tool.ADDRESS
    words = [0xeeb50a40,  # vcmp.f32 s0, #0
             0xeef1fa10,  # vmrs APSR_nzcv, FPSCR
             0x03a00000,  # moveq r0, #0, without changing flags
             _branch(a + 12, a + 28, condition=0),
             0xe3520000,  # cmp r2, #0
             0xe2911001,  # adds r1, r1, #1: different flags, not a comparison
             _branch(a + 24, a + 28, condition=1),
             0xeeb70b00,  # vmov.f64 d0, #1
             0xe12fff1e]
    path, digest = _elf(tmp_path, words)
    report = _report(path, digest, 36)
    shape = report['return_condition_shape']
    assert shape['conditional_branches'][0]['compare_address'] == tool.address(a)
    assert shape['conditional_branches'][0]['flag_transfer_address'] == tool.address(a + 4)
    assert shape['r0_immediate_writes'][0]['compare_address'] == tool.address(a)
    assert shape['r0_immediate_writes'][0]['immediate'] == 0
    assert shape['conditional_branches'][1]['compare_address'] == 'UNKNOWN'
    # FP one and integer one are explicit operands of different types.
    assert report['immediate_operands']['floating_values_hex'] == [float(1).hex()]
    _assert_metadata_only(report)


def test_compare_not_carried_across_call_or_branch_join(tmp_path):
    a = tool.ADDRESS
    words = [0xe3520000, _branch(a + 4, a + 0x1000, link=True),
             _branch(a + 8, a + 20, condition=0),
             0xe3520000, _branch(a + 16, a + 20),
             _branch(a + 20, a + 24, condition=0), 0xe12fff1e]
    path, digest = _elf(tmp_path, words)
    report = _report(path, digest, 28)
    assert [b['compare_address'] for b in report['return_condition_shape']['conditional_branches']] == [
        'UNKNOWN', 'UNKNOWN']


def _cli(path, digest):
    return subprocess.run([sys.executable, str(TOOL), '--reference', str(path),
                           '--expected-sha256', digest],
                          text=True, capture_output=True, timeout=60)


def test_cli_776_is_read_from_symtab(tmp_path):
    words = [0xe320f000] * 193 + [0xe12fff1e]  # source-owned nops + return
    path, digest = _elf(tmp_path, words)
    done = _cli(path, digest)
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report['size'] == report['symtab']['size'] == 776
    assert report['symtab']['end_exclusive'] == tool.address(tool.ADDRESS + 776)
    assert report['size_source'] == '.symtab'
    _assert_metadata_only(report)
    # Identical section contents cannot rescue a different actual st_size.
    path, digest = _elf(tmp_path, words, size=772)
    done = _cli(path, digest)
    assert done.returncode != 0 and done.stdout == ''


@pytest.mark.parametrize('changes', [
    {'size': 0}, {'size': 772}, {'size': 780}, {'value': tool.ADDRESS + 4},
    {'table_name': '.dynsym'}, {'duplicate': True}, {'mapping': '$d'},
    {'mapping': '$t'}, {'machine': 62},
])
def test_unconfirmed_boundary_or_mode_exits_nonzero(tmp_path, changes):
    path, digest = _elf(tmp_path, [0xe320f000] * 194, **changes)
    done = _cli(path, digest)
    assert done.returncode != 0
    assert done.stdout == ''
    assert 'unconfirmed' in done.stderr
    assert str(path) not in done.stderr


def test_hash_guard_and_truncated_section(tmp_path):
    path, digest = _elf(tmp_path, [0xe12fff1e])
    with pytest.raises(tool.InventoryError, match='SHA-256'):
        _report(path, '0' * 64, 4)
    path, digest = _elf(tmp_path, [0xe12fff1e], size=8)
    with pytest.raises(tool.InventoryError, match='file-backed'):
        _report(path, digest, 8)


def test_fixed_reference():
    override = os.environ.get('NOVA3_PREMIUM_REFERENCE')
    candidates = [Path(override)] if override else [
        ROOT / 'analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so',
        Path('/home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so')]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        pytest.skip('local read-only Premium reference absent')
    # No hash override: this must be the fixed Premium reference, not a donor.
    report = tool.inventory(path)
    _assert_metadata_only(report)
    assert report['reference_sha256'] == tool.REFERENCE_SHA256
    assert report['symbol'] == tool.SYMBOL and report['address'] == '0x000ebb08'
    assert report['size'] == report['symtab']['size'] == 776
    assert report['size_source'] == '.symtab'
    assert report['mode'] == 'ARM' and report['section'] == '.text'
    assert report['data_ranges'] == [{'address': '0x000ebe08', 'size': 8}]
    assert report['code_size'] == 768 and report['instruction_count'] == 192
    assert report['bl_targets'] == report['blx_targets'] == report['calls'] == []
    assert report['immediate_operands']['distinct_count'] == 8
    assert report['element_accesses']['index_offsets'] == list(range(16))
    branches = report['return_condition_shape']['conditional_branches']
    assert [(b['address'], b['compare_address'], b['flag_transfer_address']) for b in branches] == [
        ('0x000ebc08', '0x000ebbfc', '0x000ebc00'),
        ('0x000ebdf4', '0x000ebdf0', 'UNKNOWN')]
