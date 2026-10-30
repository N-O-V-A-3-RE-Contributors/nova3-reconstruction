// Source-owned recovery of CVirtualTexture::getSizeInBytes() const
// — Premium ELF VA 0x007863e4, 680 bytes, A32.
// Symbol: _ZNK6glitch5video15CVirtualTexture14getSizeInBytesEv
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Disassembly (Capstone, A32) — 680 bytes, 4 loop variants:
//
//  0x7863e4: push {r4-r9,sb,sl,fp,lr}; sub sp,sp,#0x24
//  0x7863f0: mov r5,r0               ; r5 = this
//  0x7863e8: ldr r3,[r0,#0x10]       ; r3 = this->descriptor
//  0x7863f4: ldrb r6,[r3,#0x2c]      ; r6 = descriptor[0x2c]
//  0x7863f8: and r6,r6,#2
//  0x786400: cmp r6,#0
//  0x786404: beq 0x78643c            ; if !(descriptor[0x2c] & 2) goto slow
//
//  Fast path (descriptor[0x2c] & 2 != 0) — 0x786408:
//    size = vtable[0x14](this, 0)    ; virtual call, arg=0
//    factor = (descriptor[0x24]&7)==3 ? 6 : 1
//    return size * factor
//
//  Slow path — 0x78643c:
//    r4 = this->mipCount          ; +0x1c
//    level = getMipmapCount(w, h, mipCount)   ; bl 0x7b84e0
//    r3,sb,sl = this[0x10..0x1c]  ; LDM: descriptor, width, height
//
//    if level == 0: return 0 * factor = 0
//
//    fmt_idx = (descriptor[0x24] >> 6) & 0x3F
//    Load PixelFormatDescriptor fields:
//      fp   = block_height   [0x25]
//      sp03 = block_width    [0x24]
//      sp02 = min_size       [0x27]
//
//    Loop lvl = level-1 down to 0:
//      w_s = max(1, width  >> lvl)
//      h_s = max(1, height >> lvl)
//      d_s = max(1, depth  >> lvl)
//
//      Four paths based on block_width/block_height compression:
//        both >1:  bpb * ceil(w_s/bw) * ceil(h_s/bh)  clamped, * d_s
//        bh<=1,bw>1: bpb * ceil(w_s/bw) * h_s          clamped, * d_s
//        bh>1,bw<=1: ((bpp*w_s)>>3) * ceil(h_s/bh)     clamped, * d_s
//        both<=1:    ((bpp*w_s)>>3) * h_s               clamped, * d_s
//      total += per_level_size
//
//    factor = (descriptor[0x24] & 7) == 3 ? 6 : 1
//    return total * factor
//
// This function sums the byte size of every mip level in the chain.
// It is the no-arg const overload; the single-arg overload at 0x0077d4a8
// computes only a single target mip level.
//
// Field layout (CVirtualTexture):
//   +0x10: pointer to format/configuration descriptor struct
//   +0x14: width  (uint32_t)
//   +0x18: height (uint32_t)
//   +0x1c: mipCount (uint32_t — also used as depth/slice count)
//
// Descriptor struct (pointed to by field +0x10):
//   +0x24: uint32_t — bits [11:6] = pixel format index,
//                     bits [2:0]  = cubemap flag (3 → x6, else x1)
//   +0x2c: uint8_t  — bit 1 = use external data-size path
//
// The vtable call at +0x14 takes argument 0 and returns a uint32_t size.
// Its original name is UNKNOWN (INFERRED); it is not itself reconstructed.
// Expose it as a parameter so tests can supply the expected value.

#include "pixel_format.h"

#include <cstdint>

namespace nova3::recovered::virtual_texture {

// Portion of CVirtualTexture accessed by this function.
// Field names INFERRED from callers; original source names are UNKNOWN.
struct CVirtualTextureHeader {
    std::uint32_t width;      // +0x14
    std::uint32_t height;     // +0x18
    std::uint32_t mip_count;  // +0x1c — also depth parameter
};

// Extract the 6-bit pixel format index from descriptor config word.
// Original: ubfx r0, ip, #6, #6  → bits [11:6]
inline std::uint32_t extract_format_index(std::uint32_t config_word) noexcept {
    return (config_word >> 6) & 0x3Fu;
}

// Extract cubemap face multiplier: 6 if low 3 bits == 3, else 1.
// Original: and r6,r6,#7; cmp r6,#3; moveq r6,#6; movne r6,#1
inline std::uint32_t cubemap_factor(std::uint32_t config_word) noexcept {
    return ((config_word & 7u) == 3u) ? 6u : 1u;
}

} // namespace nova3::recovered::virtual_texture

using namespace nova3::recovered::pixel_format;
using VT = nova3::recovered::virtual_texture::CVirtualTextureHeader;

extern "C"
std::uint32_t nova3_recovered_cvirtual_texture_get_size_in_bytes_noarg(
    const VT* vt,
    std::uint32_t descriptor_config_word,        // descriptor[+0x24]
    bool use_external_size,                      // (descriptor[+0x2c] & 2) != 0
    std::uint32_t external_data_size,            // vtable[0x14](this, 0) result
    const PixelFormatDescriptor* table) noexcept
{
    using namespace nova3::recovered::virtual_texture;

    // --- fast path: descriptor[0x2c] bit 1 set → use pre-computed size ---
    if (use_external_size) {
        return external_data_size * cubemap_factor(descriptor_config_word);
    }

    // --- slow path: compute mipmap chain size from dimensions ---

    const std::uint32_t level_count =
        get_mipmap_count(vt->width, vt->height, vt->mip_count);
    if (level_count == 0) {
        return 0;
    }

    const std::uint32_t fmt_idx = extract_format_index(descriptor_config_word);
    const std::uint32_t factor  = cubemap_factor(descriptor_config_word);

    std::uint32_t total = 0;

    // Original loop processes levels from (level_count - 1) down to 0
    // (coarsest to base mip).  Summation is commutative; iterate 0..N-1.
    for (std::uint32_t lvl = 0; lvl < level_count; ++lvl) {
        const std::uint32_t w_s = mip_shift(vt->width,  static_cast<std::uint8_t>(lvl));
        const std::uint32_t h_s = mip_shift(vt->height, static_cast<std::uint8_t>(lvl));
        const std::uint32_t d_s = mip_shift(vt->mip_count,
                                             static_cast<std::uint8_t>(lvl));

        const std::uint32_t size = compute_size_in_bytes(table, fmt_idx, w_s, h_s);
        total += size * d_s;
    }

    return total * factor;
}