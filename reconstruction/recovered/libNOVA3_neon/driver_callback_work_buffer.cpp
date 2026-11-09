// Source-owned recovery of vox::DriverCallbackInterface::GetWorkBuffer(int).
// Premium ELF SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Symbol: _ZN3vox23DriverCallbackInterface13GetWorkBufferEi
// Exact ELF window [0x009d4828, 0x009d4890), A32: 92 code bytes + 12 literals.
// Window SHA-256:
// bdefc716fba0137aeb4044bf8cb13d1734034c0985ac1818b4979a65d8a20a1a
//
// Bounded static analysis, independent of the name and Init's inferred layout:
// * The argument occupies r0 and survives the calls; r1 is not an input.
// * All three literal bases resolve to 0x00db9d90. Accesses select +0x34
//   (work count) and +0x38 (work data); no mixing-buffer access occurs.
// * 0x009d483c compares signed counts; >= skips all allocation and stores.
// * 0x009d4848 tests the old pointer; non-null calls VoxFree at 0x00451be4.
// * 0x009d4858 calls VoxAlloc(unsigned) at 0x00452090 with the unscaled
//   request bits: no rounding, multiplication, addition, or overflow step.
// * Both calls observe the old record. On return from allocation, data is
//   stored first (0x009d4868), then count (0x009d4870). Null sets count to 0;
//   it does not retain the previous pointer/count or suppress future retries.
// * Every path returns the record address 0x00db9dc4, not the heap pointer.
// Original return-type spelling is not encoded by the symbol/ABI; see header.
// This is not a claim of original C++ source or instruction-window matching.
#include "driver_callback.h"

// Global C++ linkage is established by the reference callee symbols.
void VoxFree(void*);
void* VoxAlloc(unsigned);

namespace vox {

DriverCallbackInterface::BufferRef* DriverCallbackInterface::GetWorkBuffer(int bytes) {
    if (m_sWorkBuffer.size < bytes) {
        if (m_sWorkBuffer.data != 0) {
            VoxFree(m_sWorkBuffer.data);
        }
        void* data = VoxAlloc(static_cast<unsigned>(bytes));
        m_sWorkBuffer.data = data;
        m_sWorkBuffer.size = data != 0 ? bytes : 0;
    }
    return &m_sWorkBuffer;
}

} // namespace vox
