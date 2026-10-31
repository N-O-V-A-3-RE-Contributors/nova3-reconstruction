// Source-owned recovery of
//
//   glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float>::eConstructor)
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   symbol   _ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE
//   ELF VA   0x00108c54, 56 bytes, A32, STT_FUNC, exact `.symtab` bounds.
//
// The whole 56-byte body, in order (see
// tools/compare/collada_matrix4_ctor_pilot.py, which re-derives each fact from
// the reference bytes instead of trusting this comment):
//
//   cmp  r1, #1              ; compare the enumerator value
//   push {r4, lr}
//   mov  r4, r0              ; keep `this`
//   bls  <epilogue>          ; UNSIGNED <= 1  => no store at all
//   mov  r1, #0
//   mov  r2, #0x40
//   bl   <memset>            ; memset(this, 0, 64)
//   vmov.f32 s15, #1.0
//   vstr s15, [r4, #0x3c]    ; float 15
//   vstr s15, [r4]           ; float 0
//   vstr s15, [r4, #0x14]    ; float 5
//   vstr s15, [r4, #0x28]    ; float 10
//   mov  r0, r4              ; return `this` (C1 complete-object constructor)
//   pop  {r4, pc}
//
// The `bl` does not target a local function: 0x00108c54's own window calls PLT
// stub 0xd2610, which is `.rel.plt` entry 137 and resolves through `.dynsym` to
// `memset`.  So the body is: for enumerator values 0 and 1 nothing is written;
// for any value >= 2 the 64 bytes are zeroed and the four diagonal floats
// (0, 5, 10, 15) are set to 1.0f.
//
// OBSERVED: the predicate (`unsigned <= 1` with no store), the memset length
// (0x40), the four store offsets, the 1.0f constant, that the stores happen
// after the memset, and that `this` is returned in r0.
//
// INFERRED, and labelled as such in the header: the semantic name
// `kObservedConstructorValue2 = 2` means "identity", because zeroing and setting
// the diagonal of a 16-float matrix is the identity -- the same value the
// recovered operator* (0x006e7e60) passes when it builds its result object.  The
// reference contains no string or symbol that names this enumerator, so *which*
// values the original enumerators had beyond 0/1/2 is not recoverable from this
// function; 74 in-binary call sites exist and their argument values are measured
// by the pilot.
//
// The store order in the source below is the reference's (15, 0, 5, 10) but the
// compiler is free to reorder constant stores; the pilot measures what this
// toolchain actually emits and does not treat store order as a recovered fact
// unless the window agrees.
//
// NOT recovered: the remaining enumerators (values 3, 4, ... take the same
// identity path in these bytes, which is why nothing here claims a
// "transposed"/"inverse" case exists), the copy constructor at 0x00108c8c
// (240 B), and every other CMatrix4 member.
#include "collada_skin_cache_deps.h"

namespace glitch {
namespace core {

template <typename T>
CMatrix4<T>::CMatrix4(eConstructor constructorValue) {
    // Unsigned comparison is what the reference emits (`cmp r1,#1` / `bls`);
    // values 0 and 1 leave the object completely untouched.
    if (static_cast<unsigned int>(constructorValue) > 1u) {
        // The reference calls `memset` through a PLT stub (`.rel.plt` entry 137
        // -> `.dynsym` `memset`), so the zeroing is kept as one 64-byte memset
        // rather than a hand-written store sequence.  The builtin spelling is
        // used because this toolchain's default include path carries no libc
        // headers (no `--sysroot`), and the recovered TU should not depend on a
        // declaration it cannot see.
        __builtin_memset(m_observedValues, 0, sizeof m_observedValues);
        // Diagonal of the 4x4, row-major: floats 0, 5, 10, 15.
        m_observedValues[0] = static_cast<T>(1);
        m_observedValues[5] = static_cast<T>(1);
        m_observedValues[10] = static_cast<T>(1);
        m_observedValues[15] = static_cast<T>(1);
    }
}

// The reference contains exactly one instantiation of the complete-object
// constructor (C1) for float, at 0x00108c54.
template CMatrix4<float>::CMatrix4(eConstructor);

}  // namespace core
}  // namespace glitch
