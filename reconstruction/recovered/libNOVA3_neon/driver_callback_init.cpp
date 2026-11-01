// Source-owned recovery of vox::DriverCallbackInterface::Init(void*).
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   _ZN3vox23DriverCallbackInterface4InitEPv
//   ELF VA 0x009d53f4, 48 bytes, A32, STT_FUNC (not stripped)
//
// Reference shape (48 B = 11 instructions + 1 literal word): push {r4, lr};
// load a PIC base, zero r3, `add r4, pc, r4`; four word stores of r3 at
// [r4,#0x40], [r4,#0x34], [r4,#0x38], [r4,#0x3c]; one `bl` to the recovered
// DetectNeon at 0x009dad14; `strb r0,[r4,#0x44]`; pop {r4, pc}.  No other
// instruction reads r0 (`this`) or r1 (the void* argument).
//
// Resolved statics (ELF VA, .symtab): the PIC base addend 0x003e4988 added to
// the `add`'s PC resolves to 0x00db9d90, which is exactly
// _ZN3vox29DriverCallbackSourceInterface20s_listenerParametersE, and every
// store offset lands on a named object:
//
//   base+0x34 = 0x00db9dc4 _ZN3vox23DriverCallbackInterface13m_sWorkBufferE
//   base+0x38 = 0x00db9dc8   same object, +4  (its `data` word)
//   base+0x3c = 0x00db9dcc _ZN3vox23DriverCallbackInterface15m_sMixingBufferE
//   base+0x40 = 0x00db9dd0   same object, +4
//   base+0x44 = 0x00db9dd4 _ZN3vox29DriverCallbackSourceInterface10enableNeonE
//
// So the 16 zeroed bytes are the two 8-byte buffer references and the single
// byte store is the 1-byte NEON flag.  GetWorkBuffer (0x009d4828) uses the same
// pair with the same field split (count at +0, heap pointer at +4, freed with
// VoxFree(void*), reallocated with VoxAlloc(unsigned)), which is where the
// BufferRef shape comes from.
//
// NOT recovered / underdetermined:
//   * `this` and the void* argument are both unread, so Init uses neither and
//     the parameter's meaning stays unknown.  It is kept opaque rather than
//     guessed.
//   * Whether the buffers are cleared by field stores or by value
//     initialisation (`m_sWorkBuffer = BufferRef();`) is not distinguishable at
//     this size; both compile to the same four zero stores (measured).
//   * Source statement order is not recoverable: GCC 4.4.3 schedules the four
//     stores itself, and its emitted order here is +0x40, +0x34, +0x38, +0x3c.
//   * The reference's PIC base is s_listenerParameters because the original
//     translation unit's static anchor began there.  A module that declares
//     only the objects this function touches therefore puts them at different
//     offsets; tools/compare/driver_callback_init_pilot.py measures that the
//     difference is one constant (0x34) for every access, not a semantic gap.
#include "driver_callback.h"

namespace vox {

unsigned char DriverCallbackSourceInterface::s_listenerParameters[48];
int DriverCallbackSourceInterface::s_enable3dSimulation;
bool DriverCallbackSourceInterface::enableNeon;

DriverCallbackInterface::BufferRef DriverCallbackInterface::m_sWorkBuffer;
DriverCallbackInterface::BufferRef DriverCallbackInterface::m_sMixingBuffer;

// vox::DriverCallbackInterface::Init, ELF VA 0x009d53f4, 48 bytes.
// Clears the callback's work and mixing buffer references, then records whether
// the NEON path may be used.
void DriverCallbackInterface::Init(void*) {
    m_sWorkBuffer.size = 0;
    m_sWorkBuffer.data = 0;
    m_sMixingBuffer.size = 0;
    m_sMixingBuffer.data = 0;
    DriverCallbackSourceInterface::enableNeon = DriverCallbackSourceInterface::DetectNeon();
}

} // namespace vox
