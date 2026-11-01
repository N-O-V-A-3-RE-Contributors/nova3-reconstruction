// Declarations for the recovered glitch::collada CSoftwareSkinTechnique slices.
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Only what the reference bytes and .symtab establish is declared here:
//
//   _ZNK6glitch7collada6detail22CSoftwareSkinTechnique17checkAvailabilityERKNS_5video10STechniqueEj
//       ELF VA 0x00710b50,   8 B, A32, STT_FUNC, STB_LOCAL
//   _ZNK6glitch7collada6detail22CSoftwareSkinTechnique16needOutputBufferEv
//       ELF VA 0x00710b48,   8 B, A32, STT_FUNC, STB_LOCAL
//   _ZN6glitch7collada6detail22CSoftwareSkinTechnique6attachEPNS_5scene10ISceneNodeE
//       ELF VA 0x00710b58,   8 B, A32, STT_FUNC, STB_LOCAL
//   _ZN6glitch7collada6detail22CSoftwareSkinTechnique12prepareCacheEv
//       ELF VA 0x0073f0a4, 228 B, A32, STT_FUNC, STB_LOCAL
//
// The class body is deliberately not reconstructed.  The reference type has a
// vtable (0x00d68ce0) and many members that none of these functions touch, so no
// base classes, virtuals or full member list are asserted here.  What IS declared
// is exactly the storage these four functions address:
//
//   +0x00  never read or written by any of them (one opaque word here)
//   +0x04  pointer, dereferenced by prepareCache: +0x04 = SMatrix array base,
//          +0x4c = element count handed to the matrix-vector resize
//   +0x08  pointer, dereferenced by prepareCache: +0x00 = flag word whose bit 0
//          gates the whole body, +0x04 = std::vector<CMatrix4<float> > object,
//          +0x10 = std::vector<CMatrix4<float> const*> object
//   +0x0c  the pointer attach stores (and nothing else in these slices)
//
// Offsets are ARM32 (4-byte pointers).  The recovered code never assumes them
// numerically except through these declared members, so a 64-bit host build lays
// the class out differently and still runs the same source.
#ifndef NOVA3_RECOVERED_COLLADA_SKIN_TECHNIQUE_H
#define NOVA3_RECOVERED_COLLADA_SKIN_TECHNIQUE_H

#include "collada_skin_cache_deps.h"

namespace glitch {
namespace video {
// Opaque: only referenced by signature, never dereferenced by these slices.
struct STechnique;
}  // namespace video
namespace scene {
// Opaque: only referenced by signature; attach stores the pointer, nothing here
// reads through it.
class ISceneNode;
}  // namespace scene

namespace collada {
namespace detail {

// Layout view of the object addressed by `this->+0x04`.  Only the two words
// prepareCache reads are named; everything else is opaque storage of the
// observed width.  The real type, its name and its remaining members are NOT
// recovered.
struct SBindInputs {
    unsigned int m_unobserved00;           // +0x00
    void* m_smatrixArray;                  // +0x04  base of SMatrix[0x40 stride]
    unsigned char m_unobserved08[0x44];    // +0x08..+0x4b
    unsigned int m_elementCount;           // +0x4c  resize count
};

// Layout view of the object addressed by `this->+0x08`.  As above: the two
// containers are modelled only by the data pointer prepareCache reads from each
// one; their size/capacity words are never read here and stay opaque.
struct SMatrixCache {
    unsigned int m_flags;                  // +0x00  bit 0 = "needs prepare"
    void* m_matrixBegin;                   // +0x04  vector<CMatrix4<float> >::begin
    unsigned char m_unobserved08[0x08];    // +0x08..+0x0f  end, capacity
    void* m_matrixPtrBegin;                // +0x10  vector<CMatrix4<float> const*>::begin
};

class CSoftwareSkinTechnique {
public:
    // 0x00710b50, 8 B: `mov r0,#1; bx lr` — a constant true, both arguments
    // unused.  const-qualified, so `this` is unused too.
    bool checkAvailability(video::STechnique const& technique,
                           unsigned flag) const;

    // 0x00710b48, 8 B: `mov r0,#1; bx lr` — constant true, `this` unused.
    bool needOutputBuffer() const;

    // 0x00710b58, 8 B: `str r1,[r0,#0xc]; bx lr` — stores the argument at +0xc
    // and returns nothing (r0 is never written).  The field name is ours; the
    // reference .symtab has no member names for this class.
    void attach(scene::ISceneNode* node);

    // 0x0073f0a4, 228 B: guard on bit 0 of the cache flag word, call the base
    // pointer-cache fill, resize the output matrix vector, then for every
    // collected source matrix store `*src[i] * smat[i]`, and finally clear the
    // guard bit.  See collada_skin_technique.cpp for the instruction evidence.
    void prepareCache();

private:
    // Test-only accessor declaration used by the behaviour test to populate the
    // two opaque layout views above.  Production builds never define
    // NOVA3_RECOVERED_TEST_HOOKS, so the API and layout are unchanged.
#ifdef NOVA3_RECOVERED_TEST_HOOKS
    friend struct CSoftwareSkinTechniqueTestAccess;
#endif

    unsigned int m_unobserved0;      // +0x00, untouched by the recovered slices
    SBindInputs* m_bindInputs;       // +0x04
    SMatrixCache* m_matrixCache;     // +0x08
    scene::ISceneNode* m_node;       // +0x0c, from attach's store displacement
};

}  // namespace detail
}  // namespace collada
}  // namespace glitch

#endif  // NOVA3_RECOVERED_COLLADA_SKIN_TECHNIQUE_H
