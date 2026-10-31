// Source-owned recovery of
//
//   glitch::core::CMatrix4<float>::getTransposed(CMatrix4<float>&) const
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   symbol   _ZNK6glitch4core8CMatrix4IfE13getTransposedERS2_
//   ELF VA   0x000eba84, 132 bytes, A32, STT_FUNC, STB_LOCAL, exact `.symtab`
//            bounds.  33 instructions: 32 transfers plus `bx lr`.
//
// Signature, from the Itanium mangling plus the prologue: a const member
// function taking one `CMatrix4<float>&`.  r0 = `this` (the source), r1 = the
// reference argument (the destination).  There is no frame: no `push`, no `sub
// sp`, no callee-saved register, and no call — so the function touches nothing
// but the two objects and reads no global.
//
// The 132 bytes are exactly 16 vldr/vstr pairs, one per element:
//
//   source offset   ->  destination offset   (both relative to r0 / r1)
//     0x00                 0x00
//     0x10                 0x04
//     0x20                 0x08
//     0x30                 0x0c
//     0x04                 0x10
//     0x14                 0x14
//     0x24                 0x18
//     0x34                 0x1c
//     0x08                 0x20
//     0x18                 0x24
//     0x28                 0x28
//     0x38                 0x2c
//     0x0c                 0x30
//     0x1c                 0x34
//     0x2c                 0x38
//     0x3c                 0x3c
//
// i.e. destination index k (0..15, written in ascending order) reads source
// index (k % 4) * 4 + k / 4.  Every one of the 16 source offsets and all 16
// destination offsets is distinct, every byte of the 64-byte destination is
// written exactly once, and the source is never written, so the operation is a
// total 4x4 index transpose and not a partial or in-place shuffle.
//
// What is OBSERVED: the offsets above, the write order, the absence of any
// other memory access, the absence of any call, `bx lr`, and the source never
// being modified.
//
// What is INFERRED: that the element mapping is row-major, i.e. that index
// r*4+c is "row r, column c".  The instruction stream cannot distinguish
// row-major from column-major storage — a column-major reading gives the same
// 16 offset pairs with the labels swapped.  Both readings agree that the
// operation is the transpose, which is why the recovered code writes the index
// arithmetic and says nothing about "row" or "column" in the emitted form.
// The only evidence for row-major is external to this window (the diagonal
// store offsets 0x00/0x14/0x28/0x3c used by both recovered CMatrix4
// constructors, which are the diagonal under a row-major reading).
//
// NOT recovered here: the `float` element type is established (vldr/vstr are
// single-precision and the class is instantiated for float at every call site),
// but no other CMatrix4 member, the inverse partner `getInverse`
// (0x000ebb08, 776 B) and every caller of this function remain external.
//
// Measured agreement: NDK r8e GCC 4.4.3 at -O3 reproduces all 33 words exactly
// (EQUAL 33 / RELOCATION_FIELD 0 / LITERAL_POOL 0 / ENCODING_DIFFERENCE 0,
// `code_window_equal=True`), because this window contains no address-dependent
// word.  -O2 and -Os emit the rolled 4-iteration loop form (0x44 = 68 bytes)
// instead, which is how the "unrolled by the original build" conclusion is
// measured rather than assumed.  See
// tools/compare/collada_matrix4_transpose_pilot.py.  A byte-equal window is a
// fact about this 132-byte range only: it is not compiler identification, not a
// flag claim and not a whole-.so SHA-256 match.
#include "collada_skin_cache_deps.h"

namespace glitch {
namespace core {

template <typename T>
void CMatrix4<T>::getTransposed(CMatrix4<T>& out) const {
    // Two nested flat loops, not one flattened single-index loop: at -O3 this
    // compiler unrolls the nested form into the reference's 16 vldr/vstr pairs
    // (measured, 132 B, 33/33 words equal) but emits a flattened loop over
    // 0..15 as a rolled 4-iteration loop (44 B).  The loop variables are
    // deliberately NOT named "row"/"column": the 16 offset pairs fix the index
    // arithmetic, not whether the storage is row-major (see the header comment).
    for (int outer = 0; outer < 4; ++outer) {
        for (int inner = 0; inner < 4; ++inner) {
            out.m_observedValues[outer * 4 + inner] =
                m_observedValues[inner * 4 + outer];
        }
    }
}

// The reference contains exactly one instantiation, for float, at 0x000eba84.
template void CMatrix4<float>::getTransposed(CMatrix4<float>& out) const;

}  // namespace core
}  // namespace glitch
