// Source-owned recovery of three glitch::collada::detail::CSoftwareSkinTechnique
// methods.
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Each of the three reference windows is 8 bytes and contains no relocation:
//
//   0x00710b50  checkAvailability  mov r0,#1 ; bx lr
//   0x00710b48  needOutputBuffer   mov r0,#1 ; bx lr
//   0x00710b58  attach             str r1,[r0,#0xc] ; bx lr
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

}  // namespace detail
}  // namespace collada
}  // namespace glitch
