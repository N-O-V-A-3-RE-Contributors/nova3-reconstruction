// Source-owned recovery of four glitch::collada::detail::CSoftwareSkinTechnique
// methods.
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Three of the reference windows are 8 bytes and contain no relocation:
//
//   0x00710b50  checkAvailability  mov r0,#1 ; bx lr
//   0x00710b48  needOutputBuffer   mov r0,#1 ; bx lr
//   0x00710b58  attach             str r1,[r0,#0xc] ; bx lr
//
// The fourth is prepareCache, 228 B at 0x0073f0a4; its evidence is written next
// to the body.
//
// Provenance of the signatures: the Itanium mangled names in the Premium .symtab
// carry the namespaces, the class, the argument types and the const qualifier.
// `checkAvailability(video::STechnique const&, unsigned) const` and
// `needOutputBuffer() const` both write only r0 = 1 and never read r0..r3, so the
// return is a constant bool and both arguments are unused.  `attach(ISceneNode*)`
// writes nothing to r0 before returning, so its return type is void (INFERRED
// from the absence of a return-value write).
//
// NOT recovered:
//   * the class body, its vtable (0x00d68ce0), base classes and every member
//     other than the +0x0c pointer `attach` stores through;
//   * why `checkAvailability` is unconditionally true — no predicate is left in
//     the window, so any "technique supported" condition was constant-folded and
//     cannot be recovered from these bytes;
//   * source text order, and whether the original bodies were written inline in
//     the class or out of line: the mangling and code are identical either way.
#include "collada_skin_technique.h"

namespace glitch {
namespace collada {
namespace detail {

// ELF VA 0x00710b50, 8 bytes.
bool CSoftwareSkinTechnique::checkAvailability(video::STechnique const&,
                                              unsigned) const {
    return true;
}

// ELF VA 0x00710b48, 8 bytes.
bool CSoftwareSkinTechnique::needOutputBuffer() const {
    return true;
}

// ELF VA 0x00710b58, 8 bytes.
void CSoftwareSkinTechnique::attach(scene::ISceneNode* node) {
    m_node = node;
}

// Element stride of the SMatrix array read by prepareCache, observed as
// `add r2, r2, r5, lsl #6` on a base taken from `this->+0x04` + 4.
enum { kSMatrixStride = 0x40 };

// ELF VA 0x0073f0a4, 228 bytes.
//
// Instruction evidence, in the order the reference executes it:
//
//   ldr r3,[r0,#8] / ldr r3,[r3] / tst r3,#1 / bne continue
//       -> the object at this+0x08 is dereferenced twice and bit 0 of that word
//          gates the whole body; the false path is a bare epilogue (no call, no
//          store), so it is a plain early return.
//   bl 0x000731c58   (r0 = this)
//       -> collada::detail::ISkinTechnique::preparePtrCache(), a direct call: the
//          pointer vector read below is filled by it, not by this function.
//   ldmib r6,{r3,r7} / ldr r5,[r3,#4] ... nothing else is derived from r3
//       -> this+0x04 is a separate pointer; +0x4c (r5) is loaded once and is the
//          resize count.  The guard word is at this+0x08, NOT this+0x04, so the
//          two pointers differ; both are re-read inside the loop.
//   CMatrix4<float>(2) at sp+0x40; bl 0x00775520 with r0 = this+0x08 + 4,
//   r1 = count, r2 = &temporary
//       -> std::vector<CMatrix4<float>>::resize(count, CMatrix4<float>(2)).  The
//          stack frame is 0x84 = 64 (multiplication result) + 64 (the by-value
//          argument slot) + 4, which is why the matrix is written as a temporary
//          argument: a named local plus the parameter copy would need 0xc4.
//   bl 0x00752470 with r0 = this+0x08 + 0x10
//       -> vector<CMatrix4<float> const*>::size() gives the loop bound (sl).
//   loop: r1 = source pointer vector[i] (base = [this+0x08 + 0x10]),
//         r8 = destination vector data + i*64 (base = [this+0x08 + 4]),
//         r2 = SMatrix base ([this+0x04 + 4]) + i*64,
//         bl 0x006e7e60 (sret = sp) = operator*(CMatrix4<float> const&, SMatrix
//         const&) on (*src[i], smat[i]); the 64-byte result at sp is copied to r8
//         by four ldm/stm pairs -> destination[i] = *src[i] * smat[i].
//   ldr r3,[r6,#8] / ldr r2,[r3] / bic r2,r2,#1 / str r2,[r3]
//       -> the same flag word has bit 0 cleared after the fill.
//
// NOT recovered: the flag word's other 31 bits, the bodies of the five callees,
// the SMatrix and CMatrix4 element layouts, and whether `count` and the vector
// size can legitimately differ (no comparison between them appears here).
void CSoftwareSkinTechnique::prepareCache() {
    if ((m_matrixCache->m_flags & 1u) == 0u) {
        return;
    }

    preparePtrCache(this);

    core::matrixVectorResize(
        &m_matrixCache->m_matrixBegin, m_bindInputs->m_elementCount,
        core::CMatrix4<float>(
            core::CMatrix4<float>::kObservedConstructorValue2));

    unsigned int const count =
        core::matrixPtrVectorSize(&m_matrixCache->m_matrixPtrBegin);

    // The reference re-reads both members inside the loop (`ldr ip,[r6,#8]` and
    // `ldr r2,[r6,#4]` at the top of every iteration), so the accesses stay in
    // the loop body instead of being hoisted into locals.
    for (unsigned int i = 0; i < count; ++i) {
        static_cast<core::CMatrix4<float>*>(
            m_matrixCache->m_matrixBegin)[i] = core::multiply(
            *static_cast<core::CMatrix4<float> const* const*>(
                m_matrixCache->m_matrixPtrBegin)[i],
            *reinterpret_cast<collada::SMatrix const*>(
                static_cast<unsigned char const*>(
                    m_bindInputs->m_smatrixArray) +
                i * kSMatrixStride));
    }

    m_matrixCache->m_flags &= ~1u;
}

}  // namespace detail
}  // namespace collada
}  // namespace glitch
