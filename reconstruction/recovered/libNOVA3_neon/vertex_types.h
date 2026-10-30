// Source-owned recovery of glitch::video vertex-related enum types and
// their getStringsInternal helpers, verified from Premium libNOVA3_neon.so.
//
// Reference: Premium libNOVA3_neon.so (ARM EABI, 32-bit LE, A32 mode)
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// This header adds three enum families adjacent to E_PRIMITIVE_TYPE in the
// .data.rel.ro string-table cluster (0xd365fc..0xd36668).  Each table was
// confirmed by disassembling its getStringsInternal trampoline and reading
// the R_ARM_RELATIVE-relocated pointers.
//
//   getStringsInternal(E_BUFFER_TYPE*)   0x0077bf44 → table at 0xd3664c (7 entries)
//   getStringsInternal(E_BUFFER_USAGE*)  0x0077bf54 → table at 0xd36634 (6 entries)
//   getStringsInternal(E_INDEX_TYPE*)    0x0077bf64 → table at 0xd36624 (4 entries)
//   getStringsInternal(E_PRIMITIVE_TYPE*) 0x0077bf74 → table at 0xd365fc (10 entries)
//
// Each helper is a 16-byte ldr/add/bx trampoline identical in form to the
// already-recovered E_PRIMITIVE_TYPE helper.  The string pointers were
// independently read with xxd/dd at each .rodata target address, 2026-10-30.

#ifndef NOVA3_RECOVERED_VERTEX_TYPES_H
#define NOVA3_RECOVERED_VERTEX_TYPES_H

#include <cstdint>

namespace nova3 {
namespace recovered {
namespace vertex_types {

// =========================================================================
// E_INDEX_TYPE — data type of index-buffer elements
// Table at 0xd36624 (size 0x10 = 4 entries, 0..2 valid + null sentinel)
// =========================================================================

enum E_INDEX_TYPE : std::uint32_t {
    EIT_U8  = 0,   // "u8"
    EIT_U16 = 1,   // "u16"
    EIT_U32 = 2,   // "u32"
    // value 3 is the null terminator in the table
};

inline const char* getIndexTypeName(E_INDEX_TYPE t) {
    switch (t) {
        case EIT_U8:  return "u8";
        case EIT_U16: return "u16";
        case EIT_U32: return "u32";
        default:      return nullptr;
    }
}

// =========================================================================
// E_BUFFER_USAGE — GPU buffer usage hint
// Table at 0xd36634 (size 0x18 = 6 entries, 0..3 valid)
// =========================================================================

enum E_BUFFER_USAGE : std::uint32_t {
    EBU_STATIC  = 0,  // "Static"
    EBU_DYNAMIC = 1,  // "Dynamic"
    EBU_STREAM  = 2,  // "Stream"
    EBU_SYSTEM  = 3,  // "System"  (0xcb0684 in .rodata)
    // values 4,5 are null terms in the table
};

inline const char* getBufferUsageName(E_BUFFER_USAGE u) {
    switch (u) {
        case EBU_STATIC:  return "Static";
        case EBU_DYNAMIC: return "Dynamic";
        case EBU_STREAM:  return "Stream";
        case EBU_SYSTEM:  return "System";
        default:          return nullptr;
    }
}

// =========================================================================
// E_BUFFER_TYPE — what kind of GPU buffer (vertex/index/pixel/etc.)
// Table at 0xd3664c (size 0x1c = 7 entries, 0..5 valid + null sentinel)
// =========================================================================

enum E_BUFFER_TYPE : std::uint32_t {
    EBT_VERTEX                = 0,  // "Vertex"       (0xc79bc4)
    EBT_INDEX                 = 1,  // "Index"        (0xc536b8)
    EBT_PIXEL_PACK            = 2,  // "Pixel Pack"   (0xc7d50c)
    EBT_PIXEL_UNPACK          = 3,  // "Pixel Unpack" (0xc7d518)
    EBT_TEXTURE_REFERENCE     = 4,  // "Texture Reference" (0xc7d528)
    EBT_VERTEX_STATE_COLLECTION = 5,  // "Vertex State Collection" (0xc7d53c)
    // value 6 is the null terminator
};

inline const char* getBufferTypeName(E_BUFFER_TYPE t) {
    switch (t) {
        case EBT_VERTEX:                 return "Vertex";
        case EBT_INDEX:                  return "Index";
        case EBT_PIXEL_PACK:             return "Pixel Pack";
        case EBT_PIXEL_UNPACK:           return "Pixel Unpack";
        case EBT_TEXTURE_REFERENCE:      return "Texture Reference";
        case EBT_VERTEX_STATE_COLLECTION: return "Vertex State Collection";
        default:                         return nullptr;
    }
}

}  // namespace vertex_types
}  // namespace recovered
}  // namespace nova3

#endif  // NOVA3_RECOVERED_VERTEX_TYPES_H