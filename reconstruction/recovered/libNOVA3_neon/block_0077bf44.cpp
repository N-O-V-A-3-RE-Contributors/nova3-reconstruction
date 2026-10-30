// glitch::video::getStringsInternal(E_BUFFER_TYPE*) — Premium ELF VA
// 0x0077bf44 (16 B, A32).
//
//   ldr r0, [pc, #4]    @ literal 0x005ba6fc from 0x77bf50
//   add r0, pc, r0      @ r0 = 0x77bf50 + 0x005ba6fc = 0x00d3664c
//   bx  lr
//
// Table at 0xd3664c (.data.rel.ro): 6 valid entries and a null sentinel
// (7 * 4 = 0x1c bytes, matching symbol-table size).  Verified 2026-10-30
// via independent .rodata reads.
#include "vertex_types.h"

extern "C" const char* const* nova3_recovered_get_strings_internal_buffer_type(
    nova3::recovered::vertex_types::E_BUFFER_TYPE* /*unused*/) {
    static const char* const kNames[] = {
        "Vertex",                    // EBT_VERTEX = 0 (at 0xc79bc4)
        "Index",                     // EBT_INDEX  = 1 (at 0xc536b8)
        "Pixel Pack",                // EBT_PIXEL_PACK = 2
        "Pixel Unpack",              // EBT_PIXEL_UNPACK = 3
        "Texture Reference",         // EBT_TEXTURE_REFERENCE = 4
        "Vertex State Collection",   // EBT_VERTEX_STATE_COLLECTION = 5
        nullptr,
    };
    return kNames;
}