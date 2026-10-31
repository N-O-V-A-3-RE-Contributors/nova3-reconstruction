// Declarations for the recovered glitch::collada CSoftwareSkinTechnique slices.
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Only what the reference bytes and .symtab establish is declared here:
//
//   _ZNK6glitch7collada6detail22CSoftwareSkinTechnique17checkAvailabilityERKNS_5video10STechniqueEj
//       ELF VA 0x00710b50,  8 B, A32, STT_FUNC, STB_LOCAL
//   _ZNK6glitch7collada6detail22CSoftwareSkinTechnique16needOutputBufferEv
//       ELF VA 0x00710b48,  8 B, A32, STT_FUNC, STB_LOCAL
//   _ZN6glitch7collada6detail22CSoftwareSkinTechnique6attachEPNS_5scene10ISceneNodeE
//       ELF VA 0x00710b58,  8 B, A32, STT_FUNC, STB_LOCAL
//
// The class body is deliberately not reconstructed.  The reference type has a
// vtable (0x00d68ce0) and many members that none of these three functions touch,
// so no base classes, virtuals or member list are asserted here; the only layout
// fact declared is the one word `attach` stores through (+0x0c).
#ifndef NOVA3_RECOVERED_COLLADA_SKIN_TECHNIQUE_H
#define NOVA3_RECOVERED_COLLADA_SKIN_TECHNIQUE_H

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

private:
    // +0x00..+0x0b are never touched by these three functions.  They are named
    // as opaque storage only so m_node lands at the observed offset; their real
    // members are NOT reconstructed.
    unsigned char m_unobserved[0x0c];
    scene::ISceneNode* m_node;  // +0x0c, from attach's store displacement
};

}  // namespace detail
}  // namespace collada
}  // namespace glitch

#endif  // NOVA3_RECOVERED_COLLADA_SKIN_TECHNIQUE_H
