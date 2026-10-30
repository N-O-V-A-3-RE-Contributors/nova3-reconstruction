// glitch::video::getStringsInternal(E_INDEX_TYPE*) — Premium ELF VA
// 0x0077bf64 (16 B, A32).  Identical trampoline pattern to the three
// neighbouring helpers:
//
//   ldr r0, [pc, #4]    @ literal 0x005ba6b4 from 0x77bf70
//   add r0, pc, r0      @ r0 = 0x77bf70 + 0x005ba6b4 = 0x00d36624
//   bx  lr
//
// Table at 0xd36624 (.data.rel.ro): 3 valid const char* entries followed
// by a null sentinel (4 * 4 = 0x10 bytes, matching the symbol-table size).
// The pointer targets were verified 2026-10-30 via independent .rodata reads.
#include "vertex_types.h"

extern "C" const char* const* nova3_recovered_get_strings_internal_index_type(
    nova3::recovered::vertex_types::E_INDEX_TYPE* /*unused*/) {
    static const char* const kNames[] = {
        "u8",   // EIT_U8  = 0
        "u16",  // EIT_U16 = 1
        "u32",  // EIT_U32 = 2
        nullptr,
    };
    return kNames;
}