// Source-owned recovery of ITexture::computeSizeInBytes() const
// — Premium ELF VA 0x0077d99c, 128 bytes, A32.
// Symbol: _ZNK6glitch5video8ITexture18computeSizeInBytesEv
// Reference SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Disassembly (Capstone, A32):
//   push {r4,r5,r6,lr}
//   ldr  r3, [r0, #0x10]       ; r3 = this->descriptor
//   mov  r4, r0                 ; r4 = this
//   ldr  r5, [r3, #0x24]       ; r5 = descriptor[0x24]
//   ldrb r2, [r3, #0x2c]       ; r2 = descriptor[0x2c]
//   and  r5, r5, #7            ; r5 = descriptor[0x24] & 7
//   cmp  r5, #3
//   moveq r5, #5               ; if low 3 bits == 3: r5 = 5 (cubemap)
//   movne r5, #0               ; else: r5 = 0
//   tst  r2, #2                ; test descriptor[0x2c] bit 1
//   bne  fast_path
//
// Path A — Slow (descriptor[0x2c] & 2 == 0):
//   ldr  r2, [r3, #0x10]       ; r2 = descriptor[0x10]  (inner pointer)
//   ldrb r3, [r3, #0x2f]       ; r3 = descriptor[0x2f]  (index byte)
//   ldr  r3, [r2, r3, lsl #2]  ; r3 = ((uint32_t*)r2)[r3]
//   add  r0, r3, #0x7f
//   bic  r0, r0, #0x7f         ; r0 = align_up(r3, 128)
//   mla  r0, r0, r5, r3        ; r0 = r5 * aligned + raw
//   pop  {r4,r5,r6,pc}
//
// Path B — Fast (descriptor[0x2c] & 2 != 0):
//   mov  r1, #0
//   ldr  r3, [r0]              ; r3 = *this = vtable
//   mov  lr, pc
//   ldr  pc, [r3, #0x14]       ; A = vtable[0x14](this, 0)  — call 1
//   ldr  r3, [r4]              ; reload vtable
//   mov  r1, #0
//   mov  r6, r0                ; r6 = A
//   mov  r0, r4                ; r0 = this (reloaded)
//   mov  lr, pc
//   ldr  pc, [r3, #0x14]       ; B = vtable[0x14](this, 0)  — call 2
//   add  r0, r0, #0x7f
//   bic  r0, r0, #0x7f         ; aligned_B = align_up(B, 128)
//   mla  r0, r5, r0, r6        ; r0 = r5 * aligned_B + A
//   pop  {r4,r5,r6,pc}
//
// The two vtable calls return A and B respectively.  They call the same
// vtable slot (0x14) with the same arguments, but are modelled as
// potentially distinct if the first call has side effects.
//
// For cubemaps (descriptor[0x24]&7==3): the aligned value is weighted by 5
// (representing 5 additional faces), and added to the base value.
// For non-cubemaps: the aligned value is multiplied by 0, so only the
// raw/base value contributes.
//
// Field layout (ITexture):
//   +0x10: pointer to descriptor/configuration struct
//
// Descriptor struct layout (INFERRED):
//   +0x10: pointer to an inner array of uint32_t values
//   +0x24: uint32_t — bits [2:0] = cubemap flag (3 → 5x, else 0x)
//   +0x2c: uint8_t  — bit 1 = use external-size path (calls vtable)
//   +0x2f: uint8_t  — index into the inner array

#include <cstdint>

namespace nova3::recovered::itexture {

// Round up to the next multiple of 128.  Original: add r0,#0x7f; bic r0,#0x7f
inline std::uint32_t align_up_128(std::uint32_t x) noexcept {
    return (x + 0x7fu) & ~0x7fu;
}

// Cubemap weight: 5 if low 3 bits of descriptor word == 3, else 0.
// Original: and r5,r5,#7; cmp r5,#3; moveq r5,#5; movne r5,#0
inline std::uint32_t cubemap_weight(std::uint32_t descriptor_word) noexcept {
    return ((descriptor_word & 7u) == 3u) ? 5u : 0u;
}

} // namespace nova3::recovered::itexture

using namespace nova3::recovered::itexture;

// Represents vtable[0x14](this, 0) for both calls.
// A = first call result, B = second call result.
// Both are the same vtable slot with same (this, 0) args; they may differ
// if the first call has side effects on the object.
using vtable_call_fn = std::uint32_t (*)(const void* self, std::uint32_t arg);

extern "C"
std::uint32_t nova3_recovered_itexture_compute_size_in_bytes(
    const void* self,           // this pointer (ITexture*)
    vtable_call_fn  call1,      // first  vtable[0x14] dispatch
    vtable_call_fn  call2,      // second vtable[0x14] dispatch
    std::uint32_t   descriptor_word,   // descriptor[+0x24]
    std::uint8_t    descriptor_byte_2c, // descriptor[+0x2c]
    const std::uint32_t* inner_table,   // descriptor[+0x10] points here
    std::uint8_t    inner_index)       // descriptor[+0x2f]
    noexcept
{
    const std::uint32_t weight = cubemap_weight(descriptor_word);
    const bool use_fast_path = (descriptor_byte_2c & 2u) != 0u;

    if (!use_fast_path) {
        // Path A: single value from inner table, align + weight
        const std::uint32_t raw = inner_table[inner_index];
        const std::uint32_t aligned = align_up_128(raw);
        return weight * aligned + raw;
    }

    // Path B: two vtable calls, align second, weight + first
    const std::uint32_t A = call1(self, 0);
    const std::uint32_t B = call2(self, 0);
    const std::uint32_t aligned_B = align_up_128(B);

    return weight * aligned_B + A;
}