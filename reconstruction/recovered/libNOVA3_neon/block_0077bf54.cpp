// glitch::video::getStringsInternal(E_BUFFER_USAGE*) — Premium ELF VA
// 0x0077bf54 (16 B, A32).
//
//   ldr r0, [pc, #4]    @ literal 0x005ba6d4 from 0x77bf60
//   add r0, pc, r0      @ r0 = 0x77bf60 + 0x005ba6d4 = 0x00d36634
//   bx  lr
//
// Table at 0xd36634 (.data.rel.ro): 4 valid entries ("Static".."System")
// and 2 null sentinels (6 * 4 = 0x18 bytes, matching symbol-table size).
// Verified 2026-10-30 via independent .rodata reads.
#include "vertex_types.h"

extern "C" const char* const* nova3_recovered_get_strings_internal_buffer_usage(
    nova3::recovered::vertex_types::E_BUFFER_USAGE* /*unused*/) {
    static const char* const kNames[] = {
        "Static",   // EBU_STATIC  = 0
        "Dynamic",  // EBU_DYNAMIC = 1
        "Stream",   // EBU_STREAM  = 2
        "System",   // EBU_SYSTEM  = 3  (at 0xcb0684, between SSL strings)
        nullptr,
        nullptr,
    };
    return kNames;
}