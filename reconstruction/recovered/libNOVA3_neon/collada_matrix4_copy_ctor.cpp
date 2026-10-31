// Source-owned recovery of
//
//   glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float> const&,
//                                           CMatrix4<float>::eConstructor)
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   symbol   _ZN6glitch4core8CMatrix4IfEC1ERKS2_NS2_12eConstructorE
//   ELF VA   0x00108c8c, 240 bytes, A32, STT_FUNC, exact `.symtab` bounds.
//
// Arguments, observed from the prologue: r0 = `this`, r1 = the source matrix
// reference, r2 = the enumerator.  `this` is kept in r4 and returned in r0 at
// the epilogue (C1 complete-object constructor).  The window is
//
//   sub  r2, r2, #1                        ; switch on (value - 1)
//   push {r4, r5, lr}
//   mov  r4, r0                            ; this
//   sub  sp, sp, #0x44                     ; 64-byte local + slack
//   cmp  r2, #4
//   addls pc, pc, r2, lsl #2               ; ARM PC reads as insn+8, so the five
//   b  <epilogue>                          ; entries start at the NEXT word:
//   .word -> 0x108d74  (value 1)           ;   value 1 -> copy loop
//   .word -> 0x108d40  (value 2)           ;   value 2 -> memset + diagonal
//   .word -> 0x108d64  (value 3)           ;   value 3 -> getTransposed(src, this)
//   .word -> 0x108d1c  (value 4)           ;   value 4 -> getInverse(src, this)
//   .word -> 0x108cbc  (value 5)           ;   value 5 -> inverse, then transpose
//
// Value 0 and every value >= 6 fall through the `addls` into the `b <epilogue>`
// arm, i.e. they write NOTHING at all (the same "no construct" behaviour the
// plain constructor 0x00108c54 has for 0 and 1).
//
// The three callees are resolved from `.symtab`, not guessed:
//   0x00108c54 -> the plain CMatrix4(eConstructor), called with r1 = 0
//                 (no-op) purely to introduce the 64-byte local;
//   0x000eba84 -> CMatrix4<float>::getTransposed(CMatrix4<float>&) const;
//   0x000ebb08 -> CMatrix4<float>::getInverse(CMatrix4<float>&) const;
//   0x000d2610 -> the `.rel.plt` stub that resolves through `.dynsym` to
//                 `memset` (the same stub the plain constructor calls).
//
// Per-path behaviour, as emitted:
//
//   value 1  r5 = source, into the shared 64-byte copy tail (four `ldm`/`stm`
//            pairs through r0-r3/ip): `*this = other`.
//   value 2  `memset(this, 0, 0x40)` then four `vstr` of one 1.0f constant at
//            byte offsets 0x3c, 0x00, 0x14, 0x28 -- the row-major identity.
//   value 3  `mov r0,r1 ; mov r1,r4 ; bl getTransposed` then the epilogue: the
//            transpose of the SOURCE is written into `this`.
//   value 4  `bl getInverse(src, this)`, then `subs r1, r0, #0` / `bne`
//            epilogue, and on a ZERO result `memset(this, 0, 0x40)`.  So a
//            failed inverse yields the all-zero matrix, not the identity.
//   value 5  `bl getInverse(src, this)`; a zero result jumps to the same
//            `memset` block as value 4; otherwise a local CMatrix4 is
//            constructed with value 0 (no stores), `getTransposed` writes the
//            transpose of `this` into it, and the shared 64-byte copy tail
//            moves it back -- i.e. transpose(inverse(source)), in place.
//
// OBSERVED: the switch shape and its five arms, the `(value - 1) <= 4` unsigned
// guard, the "write nothing" arm, the memset length 0x40 and its zero value,
// the four identity store offsets, the copy being 64 bytes through four
// ldm/stm pairs shared by values 1 and 5, the call targets above, and the
// zero-result test on getInverse.
//
// INFERRED (and marked in the header): the enumerator NAMES.  The reference has
// no symbol or string naming any enumerator value, so the semantic readings
// "copy" / "identity" / "transposed" / "inverse" / "inverse-transpose" come
// from the described instruction effects plus the measured in-binary call-site
// distribution for this constructor: 10 direct call sites, 8 with r2 = 1 and 2
// with r2 = 4 (tools/compare/collada_matrix4_copy_ctor_pilot.py re-derives the
// counts).  Values 2, 3 and 5 have no in-binary caller, so their names carry no
// call-site evidence at all.  `getInverse`'s return type is likewise INFERRED
// as bool from the zero test -- the Itanium mangling does not encode it.
//
// NOT recovered: the bodies of `getTransposed` and `getInverse`, the element
// layout (only "16 floats, 64 bytes, row-major diagonal at 0/5/10/15" is used
// and observed), and every other CMatrix4 member.
#include "collada_skin_cache_deps.h"

namespace glitch {
namespace core {

template <typename T>
CMatrix4<T>::CMatrix4(CMatrix4<T> const& other, eConstructor constructorValue) {
    switch (constructorValue) {
    case kObservedConstructorValue1:
        // 64-byte copy of the source (shared tail with case 5).
        *this = other;
        break;
    case kObservedConstructorValue2:
        // The identity, emitted inline: one memset plus four diagonal stores.
        __builtin_memset(m_observedValues, 0, sizeof m_observedValues);
        m_observedValues[0] = static_cast<T>(1);
        m_observedValues[5] = static_cast<T>(1);
        m_observedValues[10] = static_cast<T>(1);
        m_observedValues[15] = static_cast<T>(1);
        break;
    case kObservedConstructorValue3:
        // Transpose of the source, straight into `this`.
        other.getTransposed(*this);
        break;
    case kObservedConstructorValue4:
        // Inverse of the source; a failed inverse leaves the all-zero matrix.
        if (!other.getInverse(*this)) {
            __builtin_memset(m_observedValues, 0, sizeof m_observedValues);
        }
        break;
    case kObservedConstructorValue5:
        // Inverse of the source, then transposed in place through a local.
        if (!other.getInverse(*this)) {
            __builtin_memset(m_observedValues, 0, sizeof m_observedValues);
        } else {
            // Constructed with value 0, which stores nothing; the transpose
            // call fills all 64 bytes before the copy back.
            CMatrix4<T> transposedInverse(kObservedConstructorValue0);
            getTransposed(transposedInverse);
            *this = transposedInverse;
        }
        break;
    default:
        // Values 0 and >= 6: the reference writes nothing at all.
        break;
    }
}

// The reference contains exactly one instantiation of the complete-object
// copy-with-mode constructor (C1) for float, at 0x00108c8c.
template CMatrix4<float>::CMatrix4(CMatrix4<float> const&, eConstructor);

}  // namespace core
}  // namespace glitch
