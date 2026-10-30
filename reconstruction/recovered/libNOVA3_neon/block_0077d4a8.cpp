// Source-owned recovery of CVirtualTexture::getSizeInBytes(unsigned char)
// — Premium ELF VA 0x0077d4a8, 96 bytes, A32.
// Symbol: _ZNK6glitch5video15CVirtualTexture14getSizeInBytesEh
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Disassembly (Capstone, A32):
//   77d4a8: push {r4, r5, r6, lr}
//   77d4ac: ldr  r6, [r0, #0x1c]      ; r6 = this->mipCount (CVirtualTexture+0x1c)
//   77d4b0: mov  r4, r0                ; r4 = this
//   77d4b4: sub  sp, sp, #8           ; stack frame for 5th/6th args
//   77d4b8: mov  r5, r1                ; r5 = targetLevel (unsigned char)
//   77d4bc: mov  r2, r6                ; r2 = mipCount (3rd arg to getMipmapCount)
//   77d4c0: ldr  r1, [r0, #0x18]      ; r1 = height
//   77d4c4: ldr  r0, [r0, #0x14]      ; r0 = width
//   77d4c8: bl   #0x7b84e0            ; level = getMipmapCount(w, h, cnt)
//   77d4cc: ldr  ip, [r4, #0x10]      ; ip = this->descriptor
//   77d4d0: ldr  r2, [r4, #0x18]      ; r2 = height
//   77d4d4: mov  lr, #0                ; lr = 0 (skipWidthShift=false)
//   77d4d8: ldr  r1, [r4, #0x14]      ; r1 = width
//   77d4dc: mov  r3, r6                ; r3 = mipCount (depth param)
//   77d4e0: ldr  ip, [ip, #0x24]      ; ip = descriptor->word_0x24
//   77d4e4: str  lr, [sp, #4]         ; stack[1] = 0 (skipWidthShift)
//   77d4e8: uxtb r0, r0               ; zero-extend level
//   77d4ec: cmp  r0, r5                ; compare level vs targetLevel
//   77d4f0: strls r0, [sp]            ; stack[0] = min(level, targetLevel)
//   77d4f4: strhi r5, [sp]
//   77d4f8: ubfx r0, ip, #6, #6       ; r0 = extract format bits
//   77d4fc: bl   #0x77d470            ; computeMipmapSizeInBytes(6-arg)
//   77d500: add  sp, sp, #8
//   77d504: pop  {r4, r5, r6, pc}
//
// The arithmetic, descriptor layout, and helpers live in pixel_format.h;
// this file provides the extern "C" dispatch for ARM compilation/comparison.

#include "pixel_format.h"

using namespace nova3::recovered::pixel_format;

// CVirtualTexture field layout (observed from disassembly):
//   +0x10: pointer to format descriptor struct
//   +0x14: width  (uint32_t)
//   +0x18: height (uint32_t)
//   +0x1c: mipCount (uint32_t — also used as depth/slices)
//
// Field names are INFERRED from callers; original source names are UNKNOWN.
// The descriptor pointer is passed separately to avoid pointer-truncation
// issues on 64-bit hosts (on ARM32, all fields are 32 bits naturally).
struct CVirtualTextureFields {
    std::uint32_t width;      // +0x14
    std::uint32_t height;     // +0x18
    std::uint32_t mip_count;  // +0x1c
};

// Extract the E_PIXEL_FORMAT enum value from the descriptor word at +0x24.
// Original: ldr ip,[ip,#0x24]; ubfx r0,ip,#6,#6
// The 6-bit field (bits 6–11) is the format index into the 40-entry table.
inline std::uint32_t extract_format_index(const void* descriptor) noexcept {
    const auto* words = static_cast<const std::uint32_t*>(descriptor);
    const std::uint32_t word = words[0x24 / sizeof(std::uint32_t)];
    return (word >> 6) & 0x3Fu;
}

extern "C" std::uint32_t nova3_recovered_cvirtual_texture_get_size_in_bytes(
    const CVirtualTextureFields* vt,
    const void* descriptor,
    std::uint8_t target_level,
    const PixelFormatDescriptor* table)
{
    const std::uint32_t level =
        get_mipmap_count(vt->width, vt->height, vt->mip_count);

    const std::uint32_t clamped_level =
        (level <= static_cast<std::uint32_t>(target_level)) ? level
                                                            : static_cast<std::uint32_t>(target_level);

    const std::uint32_t format_index = extract_format_index(descriptor);

    return compute_mipmap_size_in_bytes_slices(
        table, format_index,
        vt->width, vt->height, vt->mip_count,
        static_cast<std::uint8_t>(clamped_level),
        false); // skipWidthShift observed as 0 at 0x77d4d4
}