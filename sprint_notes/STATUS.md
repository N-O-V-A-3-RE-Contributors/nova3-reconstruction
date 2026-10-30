# Sprint STATUS — Pass 12 of 12

Sprint: 20261030-163856
Pass completed: 2026-10-30 (AEDT)
Branch: automation/gated-20261030-163856 (isolated worktree)

## What this pass did

Two things, both data-verified against the linked ELF:

1. **Recovered three additional glitch::video enum string tables** adjacent
   to the already-recovered E_PRIMITIVE_TYPE cluster in .data.rel.ro
   (0xd365fc..0xd36668).  Each getStringsInternal trampoline was
   disassembled, its PC-relative literal addend was extracted, and the
   resulting table base was read directly from the ELF file with xxd.
   Every string pointer was independently resolved to its .rodata target
   and read with dd.

2. **Recovered CVertexStreams::getStream** — both const (0x0077bd7c) and
   non-const (0x0077bdc0) overloads, which are byte-identical except for
   the const qualifier in the mangled name.  The function searches a
   sorted array of 16-byte SVertexStream entries by the 16-bit attribute
   at offset +8.  On no-match it falls back to the container's first
   embedded entry (this+0x10).

   A known discrepancy is NOTED: addOffset (0x0077be04) accesses the
   same offset +8 as a full 32-bit word (ldr/str), while getStream
   reads it as a 16-bit halfword (ldrh) and uses signed comparison
   (blt).  This implies a union or overlay at that field.  The
   reconstruction preserves the low half as the attribute discriminator
   and the high half as opaque data, so both access patterns coexist.

## New recovered / data-verified items

### Enum string tables (DATA-VERIFIED from .data.rel.ro)

| Enum | Helper VA | Table VA | Entries |
|------|-----------|----------|---------|
| E_BUFFER_TYPE | 0x0077bf44 | 0xd3664c | 6 (+ null) |
| E_BUFFER_USAGE | 0x0077bf54 | 0xd36634 | 4 (+ 2 nulls) |
| E_INDEX_TYPE | 0x0077bf64 | 0xd36624 | 3 (+ null) |
| E_PRIMITIVE_TYPE | 0x0077bf74 | 0xd365fc | 9 (+ null) — pass 10 |

### getStream functions (BEHAVIOURAL, host-tested)

| Function | ELF VA | Size | Result |
|----------|--------|------|--------|
| getStream (const) | 0x0077bd7c | 68 B | BEHAVIOURAL — 12 test scenarios |
| getStream (non-const) | 0x0077bdc0 | 68 B | (identical body to const) |

No ARM byte comparison performed (clang vs GCC codegen gap unchanged).

## Evidence

- ELF SHA-256: `84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`
- Enum string tables: each pointer at the table VAs was read with xxd,
  each string at its .rodata target was read with dd.  See the
  documentation in `vertex_types.h` for exact addresses.
- getStream: disassembly compared via diff confirms const/non-const are
  byte-identical (differing only in target addresses within the function
  body).

## Host test results

CTest **28/28 passed** (was 26/26).

- `vertex_types`: 33 string-table checks (9 name-match + 9 out-of-range
  null + 15 trampoline-table checks including 3 null sentinels).
- `vertex_stream_getstream`: 24 pointer-match + value-match checks
  covering empty array, exact match, key-too-low (gap) fallback, key
  larger than all, signed negative-value sorting, const/non-const
  variants, and high-half preservation.

Negative control: modifying set_attrib to swap the halfword order causes
the sorted-search tests to fail at the "single match" check — proving
the test guards the reconstruction.

## Files changed (this pass)

New files:
1. `reconstruction/recovered/libNOVA3_neon/vertex_types.h` — NEW
   (E_INDEX_TYPE, E_BUFFER_USAGE, E_BUFFER_TYPE enums + name helpers)
2. `reconstruction/recovered/libNOVA3_neon/block_0077bf64.cpp` — NEW
   (getStringsInternal for E_INDEX_TYPE)
3. `reconstruction/recovered/libNOVA3_neon/block_0077bf54.cpp` — NEW
   (getStringsInternal for E_BUFFER_USAGE)
4. `reconstruction/recovered/libNOVA3_neon/block_0077bf44.cpp` — NEW
   (getStringsInternal for E_BUFFER_TYPE)
5. `reconstruction/recovered/libNOVA3_neon/vertex_stream_getstream.h` — NEW
   (both getStream overloads + SVertexStream layout update)
6. `reconstruction/recovered/libNOVA3_neon/block_0077bd7c.cpp` — NEW
   (const getStream trampoline)
7. `reconstruction/recovered/libNOVA3_neon/block_0077bdc0.cpp` — NEW
   (non-const getStream trampoline)
8. `reconstruction/tests/vertex_types_test.cpp` — NEW
9. `reconstruction/tests/vertex_stream_getstream_test.cpp` — NEW

Modified:
10. `CMakeLists.txt` — +2 test targets, vertex_streams block preserved

## Honest scope and limitations

- **0 new MATCHED_ARM.**  All new functions are BEHAVIOURAL or DATA-VERIFIED.
- The getStream SVertexStream layout has a known discrepancy with
  addOffset sharing offset +8 for different data types (32-bit vs 16-bit
  access).  This is documented but not resolved.
- The original struct field names (mData, mActive, mAttribIndex, etc.)
  are not recovered — our names are descriptive placeholders.
- No ARM object comparison attempted for getStream (same clang/GCC gap).
- The string tables are data, not code — no ARM instructions were
  compared.

## Known discrepancy: SVertexStream offset +8 dual access

addOffset at 0x77be04 uses `ldr r3, [r0, #24]` / `str r3, [r0, #24]`
(32-bit access at entry+8), while getStream uses `ldrh ip, [r2, #8]`
(16-bit access at entry+8).  The original likely uses a union where
the low 16 bits are the E_VERTEX_ATTRIBUTE (for search/sort) and the
full 32 bits form a data offset.  Our reconstruction preserves the high
16 bits when setting the attribute discriminator.

## Cumulative statistics

| Measure | Before pass 12 | After pass 12 |
|---------|---------------|---------------|
| Host CTest | 26/26 | 28/28 |
| MATCHED_ARM functions | 7 | 7 (unchanged) |
| COMPILED+BEHAVIOURAL | 5 | 7 (+getStream const, getStream non-const) |
| DATA-VERIFIED enum tables | 1 (E_PRIMITIVE_TYPE) | 4 (+E_INDEX_TYPE, E_BUFFER_USAGE, E_BUFFER_TYPE) |
| Total source-owned recovered functions | 21 | 23 |
| DATA-VERIFIED string tables (enums) | 1 | 4 |

## Next functions (priority order)

1. E_VERTEX_ATTRIBUTE / E_VERTEX_ATTRIBUTE_VALUE_TYPE string tables —
   these are also in the .data.rel.ro cluster (getStringsInternal at
   0x0070fe28, table at 0xd89020 in .data; and helper at 0x0070fe38,
   table at 0xd35664).  16+ entries each.
2. CVertexStreams::updateStates (0x0077bf40, wraps updateStatesInternal
   at 0x0077bea8 which was already exported) — thin wrapper.
3. SCompareType functor at 0x0077cb30 — the pointer stored at 0xd365f8
   (before E_PRIMITIVE_TYPE table) is a comparison predicate for
   E_BUFFER_TYPE entries.
4. ARM byte comparison for a small non-switch function once a viable
   GCC 4.4.3 toolchain is established (blocker unchanged).

## Current blockers (unchanged)

1. Historical GCC 4.4.3 ARM toolchain still not established.
2. Whole-`.so` SHA equality not established (not attempted).
3. Switch/jump-table functions show the largest codegen gap under clang.
4. ADB has no attached device; Android APK build not tested.