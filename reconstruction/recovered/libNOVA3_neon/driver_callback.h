// Shared declarations for the recovered vox driver-callback slices.
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Only the symbol names, signatures and static-object sizes below are
// evidence-backed.  Class bodies, base classes, vtables and every member this
// slice does not touch are deliberately absent: they are NOT reconstructed.
#ifndef NOVA3_RECOVERED_DRIVER_CALLBACK_H
#define NOVA3_RECOVERED_DRIVER_CALLBACK_H

namespace vox {

class DriverCallbackSourceInterface {
public:
    // ELF VA 0x009dad14, 32 B, A32, STT_FUNC.  Declared static because its only
    // recovered call site (DriverCallbackInterface::Init, 0x009d53f4) reaches
    // it with one `bl` and never sets up a receiver in r0; a non-static member
    // call would have to supply one.  The Itanium mangling is identical for
    // both spellings, so this is caller-side evidence, not a name-table fact.
    static bool DetectNeon();

    // Static objects whose existence, address and size come from the Premium
    // .symtab.  Their C++ types are INFERRED from those sizes and from the uses
    // recorded in driver_callback_init.cpp; they are declared here only so the
    // recovered slices address the same objects the reference does.
    static unsigned char s_listenerParameters[48];  // .bss 0x00db9d90, 48 B
    static int s_enable3dSimulation;               // .bss 0x00db9dc0, 4 B
    static bool enableNeon;                        // .bss 0x00db9dd4, 1 B
};

class DriverCallbackInterface {
public:
    // Eight-byte buffer reference at .bss 0x00db9dc4 and 0x00db9dcc.  Shape
    // INFERRED from DriverCallbackInterface::GetWorkBuffer (0x009d4828): the
    // word at +0 is compared, signed, against a requested element count and
    // afterwards receives that count; the word at +4 is passed to
    // VoxFree(void*) and replaced by the result of VoxAlloc(unsigned).  Member
    // names are ours; no original field names survive.
    struct BufferRef {
        int size;
        void* data;
    };

    static BufferRef m_sWorkBuffer;    // .bss 0x00db9dc4, 8 B
    static BufferRef m_sMixingBuffer;  // .bss 0x00db9dcc, 8 B

    // ELF VA 0x009d4828, 104 B, A32.  The signed int argument is in r0,
    // not r1: this is static, with no receiver.  Returns the address of the
    // work BufferRef, NOT its data pointer; never selects the mixing buffer.
    // BufferRef* is our ABI-compatible spelling; original pointer/reference
    // return spelling and original record type name are NOT recovered.
    static BufferRef* GetWorkBuffer(int bytes);

    // ELF VA 0x009d53f4, 48 B, A32, STT_FUNC.  The parameter type comes from
    // the mangled name (_ZN3vox23DriverCallbackInterface4InitEPv); its meaning
    // is NOT recovered.
    void Init(void*);
};

} // namespace vox

#endif // NOVA3_RECOVERED_DRIVER_CALLBACK_H
