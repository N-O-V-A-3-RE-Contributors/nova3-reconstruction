#!/usr/bin/env python3
"""Offline reference-only test. Never writes/extracts reference instructions.

Requires preinstalled pyelftools and Unicorn, a locally held hash-pinned ELF,
and a source-built host test DSO. See the block README for build commands.
"""
import argparse
import ctypes as C
import hashlib
import importlib.metadata
import json
import random
import struct

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_C1_C0_2,
    UC_ARM_REG_FPEXC, UC_ARM_REG_FPSCR,
)

EXPECTED_HASH = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
FUNCTIONS = (
    ('InAppBilling_ClearItemList', 0xd4dbc, 4),
    ('charToUI64', 0xd4de4, 172),
    ('JNU_ThrowByName', 0xd4f74, 80),
    ('InAppBilling_GetState', 0xd5224, 4),
)
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--reference', required=True)
p.add_argument('--candidate', required=True, help='host test shared library')
p.add_argument('--arm-object', required=True)
p.add_argument('--linked-wrappers', required=True)
a = p.parse_args()
with open(a.reference, 'rb') as f:
    if hashlib.file_digest(f, 'sha256').hexdigest() != EXPECTED_HASH:
        raise RuntimeError('reference SHA256 mismatch')

u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
with open(a.reference, 'rb') as f:
    elf = ELFFile(f)
    assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian
    for segment in elf.iter_segments():
        if segment['p_type'] != 'PT_LOAD':
            continue
        lo = segment['p_vaddr'] & ~4095
        hi = (segment['p_vaddr'] + segment['p_memsz'] + 4095) & ~4095
        u.mem_map(lo, hi - lo)
        u.mem_write(segment['p_vaddr'], segment.data())

scratch, stack, hooks = 0x02000000, 0x03000000, 0x04000000
u.mem_map(scratch, 0x20000)
u.mem_map(stack, 0x10000)
u.mem_map(hooks, 0x1000)
stop = hooks + 0xff0
u.reg_write(UC_ARM_REG_C1_C0_2, 0xf << 20)
u.reg_write(UC_ARM_REG_FPEXC, 1 << 30)
regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)


def invoke(entry, *args):
    for reg, value in zip(regs, args):
        u.reg_write(reg, value & 0xffffffff)
    u.reg_write(UC_ARM_REG_SP, stack + 0x8000)
    u.reg_write(UC_ARM_REG_LR, stop)
    u.reg_write(UC_ARM_REG_FPSCR, 0)  # nearest, no flush-to-zero/default NaN
    u.emu_start(entry, stop, count=2000000)
    assert u.reg_read(UC_ARM_REG_PC) == stop, 'instruction limit exhausted'
    assert u.reg_read(UC_ARM_REG_SP) == stack + 0x8000, 'stack imbalance'
    return u.reg_read(UC_ARM_REG_R0) | (u.reg_read(UC_ARM_REG_R1) << 32)


def return_to_caller(value=None):
    if value is not None:
        u.reg_write(UC_ARM_REG_R0, value & 0xffffffff)
    u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))


lib = C.CDLL(a.candidate)
convert = lib.charToUI64
convert.argtypes = [C.c_void_p, C.c_int32, C.c_void_p]
convert.restype = None
convert_double = lib.nova69_test_unsigned_conversion
convert_double.argtypes = [C.c_double]
convert_double.restype = C.c_uint64
host_pow = C.CDLL('libm.so.6').pow
host_pow.argtypes = [C.c_double, C.c_double]
host_pow.restype = C.c_double
pow_calls = []


def pow_hook(machine, address, size, user):
    values = [machine.reg_read(r) for r in regs]
    base, exponent = struct.unpack('<dd', struct.pack('<4I', *values))
    assert base == 10.0
    pow_calls.append(exponent)
    low, high = struct.unpack('<II', struct.pack('<d', host_pow(base, exponent)))
    machine.reg_write(UC_ARM_REG_R0, low)
    machine.reg_write(UC_ARM_REG_R1, high)
    return_to_caller()


u.hook_add(UC_HOOK_CODE, pow_hook, begin=0xd2b38, end=0xd2b38)
rng = random.Random(0x69d4dbc)
transcript = hashlib.sha256()
numeric_count = 0


def check_number(data, length=None, overlap=None):
    global numeric_count
    if length is None:
        length = len(data)
    before = bytes([0xa5]) * 32 + data + bytes([0x5a]) * 32
    address = scratch + 0x100
    u.mem_write(address, before)
    owned = C.create_string_buffer(before, len(before))
    if overlap is None:
        out = scratch + 0x10000
        out_host = C.c_uint64(0xfedcba9876543210)
        out_ptr = C.addressof(out_host)
        u.mem_write(out - 8, b'\x9c' * 24)
    else:
        assert (address + 32 + overlap) % 8 == 0
        out = address + 32 + overlap
        out_ptr = C.addressof(owned) + 32 + overlap
    pow_calls.clear()
    invoke(0xd4de4, out, length, 0 if length <= 0 else address + 32)
    convert(out_ptr, length, None if length <= 0 else C.addressof(owned) + 32)
    original = struct.unpack('<Q', bytes(u.mem_read(out, 8)))[0]
    candidate = C.c_uint64.from_address(out_ptr).value
    assert original == candidate, ('conversion mismatch', data.hex(), length, original, candidate)
    assert bytes(u.mem_read(address, len(before))) == owned.raw, 'input/alias memory mismatch'
    if overlap is None:
        assert bytes(u.mem_read(out - 8, 8)) == b'\x9c' * 8
        assert bytes(u.mem_read(out + 8, 8)) == b'\x9c' * 8
    assert pow_calls == list(map(float, range(max(length, 0) - 1, -1, -1)))
    transcript.update(struct.pack('<iiQ', length, overlap if overlap is not None else -1000, original))
    transcript.update(data)
    numeric_count += 1


for length in (0, -1, -2, -2147483648):
    check_number(b'', length)
for n in range(256):
    check_number(bytes([n]))
for data in (b'12345', b'00001', b'1\0', b'1/', b'/1', b'9007199254740993',
             b'18446744073709551615', b'18446744073709551616',
             b'10000000000000000001', bytes(range(256))):
    check_number(data)
for length in (2, 3, 8, 15, 16, 17, 18, 19, 20, 21, 31, 32, 64, 128, 308, 309, 310, 400):
    for byte in (0, 47, 48, 49, 57, 127, 128, 255):
        check_number(bytes([byte]) * length)
for _ in range(400):
    length = rng.randrange(1, 65)
    check_number(rng.randbytes(length))
    check_number(bytes(rng.randrange(48, 58) for _ in range(length)))
for overlap in (-8, 0, 8, 16):
    for _ in range(16):
        check_number(rng.randbytes(32), overlap=overlap)

# Independently exercise the real ARM conversion helper, including specials.
conversion_values = [0.0, -0.0, 0.5, -0.5, -1.0, float('nan'),
                     float('inf'), -float('inf')]
for exponent in (0, 1, 31, 32, 52, 53, 63, 64, 65, 100, 1023):
    for delta in (-4096, -1, 0, 1, 4096):
        conversion_values += [float(2**exponent + delta), -float(2**exponent + delta)]
conversion_values += [struct.unpack('<d', rng.randbytes(8))[0] for _ in range(2000)]
for value in conversion_values:
    lo, hi = struct.unpack('<II', struct.pack('<d', value))
    original = invoke(0xbe323c, lo, hi)
    candidate = convert_double(value)
    assert original == candidate, ('unsigned conversion helper mismatch', value, original, candidate)
    transcript.update(struct.pack('<dQ', value, original))

# JNI dependencies are synthetic; original helper instructions execute intact.
env, table = scratch + 0x4000, scratch + 0x5000
name, message = scratch + 0x6000, scratch + 0x6100
find_addr, throw_addr, delete_addr = hooks + 0x10, hooks + 0x20, hooks + 0x30
put32 = lambda addr, value: u.mem_write(addr, struct.pack('<I', value))
put32(env, table)
for offset, target in ((24, find_addr), (56, throw_addr), (92, delete_addr)):
    put32(table + offset, target)
u.mem_write(name, b'java/lang/RuntimeException\0')
u.mem_write(message, b'recovered message\0')
native_events, source_events = [], []
class_token, throw_status, expected_message = 0, 0, 0


def jni_hook(machine, address, size, user):
    r0, r1, r2 = [machine.reg_read(r) for r in regs[:3]]
    assert r0 == env
    if address == find_addr:
        assert r1 == name
        native_events.append(('find',))
        return_to_caller(class_token)
    elif address == throw_addr:
        assert r1 == class_token and r2 == expected_message
        native_events.append(('throw', class_token, bool(r2)))
        return_to_caller(throw_status)
    else:
        assert address == delete_addr and r1 == class_token
        native_events.append(('delete', class_token))
        return_to_caller(0xdeadbeef)  # return register is irrelevant (void).


for addr in (find_addr, throw_addr, delete_addr):
    u.hook_add(UC_HOOK_CODE, jni_hook, begin=addr, end=addr)
Find = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)
Throw = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)
Delete = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)
source_name = C.create_string_buffer(b'java/lang/RuntimeException')
source_message = C.create_string_buffer(b'recovered message')
source_table = (C.c_void_p * 234)()
source_env = C.c_void_p(C.addressof(source_table))


@Find
def find_callback(e, n):
    assert e == C.addressof(source_env) and n == C.addressof(source_name)
    source_events.append(('find',))
    return class_token or None


@Throw
def throw_callback(e, c, m):
    assert e == C.addressof(source_env) and c == class_token
    assert m == (C.addressof(source_message) if expected_message else None)
    source_events.append(('throw', c, bool(m)))
    return throw_status


@Delete
def delete_callback(e, c):
    assert e == C.addressof(source_env) and (c or 0) == class_token
    source_events.append(('delete', c or 0))


for index, callback in ((6, find_callback), (14, throw_callback), (23, delete_callback)):
    source_table[index] = C.cast(callback, C.c_void_p).value
throw_by_name = lib.JNU_ThrowByName
throw_by_name.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p]
throw_by_name.restype = None
jni_count = 0
for class_token in (0, 0x12345678, 0xfedcba98):
    for throw_status in (0, 1, -1, -2147483648, 2147483647):
        for expected_message in (0, message):
            native_events.clear()
            source_events.clear()
            invoke(0xd4f74, env, name, expected_message)
            throw_by_name(C.byref(source_env), C.addressof(source_name),
                          C.addressof(source_message) if expected_message else None)
            assert native_events == source_events
            transcript.update(json.dumps(native_events).encode())
            jni_count += 1

# Test only the wrappers: intercept each unreconstructed billing target.
billing_value, billing_seen = 0, []


def billing_hook(machine, address, size, user):
    assert machine.reg_read(UC_ARM_REG_LR) == stop, 'veneer was not a tail branch'
    billing_seen.append(address)
    return_to_caller(billing_value)


for addr in (0xd4d58, 0xd5148):
    u.hook_add(UC_HOOK_CODE, billing_hook, begin=addr, end=addr)
reset = lib.nova69_test_billing_reset
reset.argtypes = [C.c_int32]
calls = lib.nova69_test_billing_calls
calls.argtypes = [C.c_int]
calls.restype = C.c_int
billing_count = 0
for symbol, entry, target, is_state in (
    ('InAppBilling_ClearItemList', 0xd4dbc, 0xd4d58, 0),
    ('InAppBilling_GetState', 0xd5224, 0xd5148, 1),
):
    wrapper = getattr(lib, symbol)
    wrapper.argtypes = []
    wrapper.restype = C.c_int32
    for billing_value in (0, 1, -1, -2147483648, 2147483647):
        billing_seen.clear()
        reset(billing_value)
        result = invoke(entry) & 0xffffffff
        assert result == (wrapper() & 0xffffffff) == (billing_value & 0xffffffff)
        assert billing_seen == [target]
        assert calls(is_state) == 1 and calls(1 - is_state) == 0
        transcript.update(struct.pack('<II', entry, result))
        billing_count += 1


def symbol_region(path, name):
    with open(path, 'rb') as f:
        obj = ELFFile(f)
        assert obj['e_machine'] == 'EM_ARM' and obj.elfclass == 32 and obj.little_endian
        sym, = obj.get_section_by_name('.symtab').get_symbol_by_name(name)
        assert not (sym['st_value'] & 1), 'comparison requires A32'
        section = obj.get_section(sym['st_shndx'])
        offset = sym['st_value'] - section['sh_addr']
        return sym['st_value'], section.data()[offset:offset + sym['st_size']]


comparisons = []
for symbol, entry, length in FUNCTIONS:
    is_wrapper = symbol.startswith('InAppBilling_')
    path = a.linked_wrappers if is_wrapper else a.arm_object
    address, candidate = symbol_region(path, symbol)
    reference = bytes(u.mem_read(entry, length))
    equal = candidate == reference
    if is_wrapper:
        assert address == entry
    comparisons.append(dict(
        symbol=symbol, reference_address=hex(entry), mode='A32',
        candidate_size=len(candidate), reference_size=length,
        candidate_region_sha256=hashlib.sha256(candidate).hexdigest(),
        reference_region_sha256=hashlib.sha256(reference).hexdigest(),
        exact_region_bytes=equal,
        result='MATCHED WRAPPER ONLY' if equal and is_wrapper else 'NOT MATCHED',
        method='raw linked region equality at original address; only external target addresses resolved'
               if is_wrapper else 'raw unlinked region equality; no relocation masking',
    ))

print(json.dumps(dict(
    result='PASS', reference_sha256=EXPECTED_HASH,
    numeric_cases=numeric_count, conversion_helper_cases=len(conversion_values),
    jni_cases=jni_count, billing_wrapper_cases=billing_count,
    transcript_sha256=transcript.hexdigest(),
    versions={n: importlib.metadata.version(n) for n in ('unicorn', 'pyelftools')},
    assembly_comparisons=comparisons,
    exclusions=[
        'pow import supplied by host glibc libm, not original Android libm',
        'JNI calls and billing targets are test doubles; no VM or billing service executed',
        'No original code patched, packaged, linked or used by production source',
        'Void return types for charToUI64/JNU_ThrowByName inferred; incidental return register ignored',
        'Finite sampled emulator tests; no universal, device, ABI or game parity claim',
    ],
), sort_keys=True))
