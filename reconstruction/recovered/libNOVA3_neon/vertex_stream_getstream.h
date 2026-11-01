// Source-owned recovery of CVertexStreams::getStream (const and non-const).
//
// Reference: Premium libNOVA3_neon.so (ARM EABI, 32-bit LE, A32 mode)
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Recovered functions (structurally identical, 68 B each, A32):
//   const:  _ZNK6glitch5video14CVertexStreams9getStreamENS0_18E_VERTEX_ATTRIBUTEEPKNS0_13SVertexStreamES5_  0x0077bd7c
//   non-const: _ZN6glitch5video14CVertexStreams9getStreamENS0_18E_VERTEX_ATTRIBUTEEPNS0_13SVertexStreamES4_  0x0077bdc0
//
// Both functions search a sorted array of SVertexStream entries for one
// whose 16-bit attribute (entry+8) matches the given key.  Entries are
// sorted ascending by this attribute.  On failure they return the
// container's first-entry pointer (this+0x10), which is also the sentinel
// when the array is empty.
//
// The original struct SVertexStream is 16 bytes.  The ldrh at [entry+8]
// reads the attribute as an unsigned halfword; the cmp/blt uses signed
// comparison (blt = branch if signed less-than).  For small positive
// attribute values the distinction is irrelevant.  addOffset at 0x77be04
// accesses the SAME offset +8 as a full 32-bit word (ldr/str), indicating
// a union or layered layout — this is classified as a known discrepancy
// (see sprint_notes/STATUS.md for details).
//
// The container's embedded array starts at this+0x10 with stride 16 and
// capacity sentinel at this+0x24 (36 bytes into the object).  The
// begin/end pointers are passed explicitly by the caller; the fallback
// [this+0x10] is the first entry in that embedded array.

#ifndef NOVA3_RECOVERED_VERTEX_STREAM_GETSTREAM_H
#define NOVA3_RECOVERED_VERTEX_STREAM_GETSTREAM_H

#include <cstdint>

namespace nova3 {
namespace recovered {
namespace vertex_streams {

// 16-byte vertex-stream entry, aligned.  Layout inferred from the four
// recovered CVertexStreams accessors (getActiveAttributeMask, addOffset,
// and both getStream overloads).  The ldrh at +8 in getStream vs the
// ldr/str 32-bit access at +8 in addOffset are NOTED but not
// structurally resolved: the original likely uses a union or overlay.
struct SVertexStream {
    std::uint32_t field_00;      // +0
    std::uint32_t active;        // +4  (non-zero = contributes to mask)
    std::uint32_t data_offset;   // +8  (addOffset target, 32-bit; ALSO read
                                  //      as attrib halfword by getStream)
    std::uint16_t attrib_bit;    // +12 (bit position for attribute mask)
    std::uint16_t field_0e;      // +14 (padding)
};

// Get the 16-bit E_VERTEX_ATTRIBUTE discriminator stored at
// entry->data_offset (the low half).  The original loads this via ldrh.
inline std::uint16_t getStreamAttrib(const SVertexStream* entry) {
    return static_cast<std::uint16_t>(entry->data_offset & 0xFFFFu);
}

// Const getStream: search sorted [begin, end) for attribute key.
// Returns pointer to matched entry, or fallback (container's first entry)
// when not found.  r0=container, r1=key, r2=begin, r3=end.
inline const SVertexStream* getStream(
    const void* container,
    std::uint32_t key,
    const SVertexStream* begin,
    const SVertexStream* end)
{
    if (begin == end) goto fallback_check;

    for (;;) {
        std::uint16_t a = getStreamAttrib(begin);
        if (static_cast<std::int32_t>(a) >= static_cast<std::int32_t>(key)) {
            // attrib >= key — check for exact match
            if (a == static_cast<std::uint16_t>(key))
                return begin;       // found
            else
                goto fallback;      // attrib > key, not found
        }
        ++begin;   // stride 16
        if (begin == end) break;
    }

fallback_check:
    {
        // Final check on the element at end (or begin==end case).
        // The original checks [begin+8] one last time, then returns
        // the container's first entry on mismatch.
        std::uint16_t a = getStreamAttrib(begin);
        if (a == static_cast<std::uint16_t>(key))
            return begin;
    }

fallback:
    // load fallback from container+0x10: the first embedded entry
    return reinterpret_cast<const SVertexStream*>(
        static_cast<const std::uint8_t*>(container) + 0x10);
}

// Non-const overload — identical logic, different return type.
inline SVertexStream* getStream(
    void* container,
    std::uint32_t key,
    SVertexStream* begin,
    SVertexStream* end)
{
    if (begin == end) goto fallback_check;

    for (;;) {
        std::uint16_t a = getStreamAttrib(begin);
        if (static_cast<std::int32_t>(a) >= static_cast<std::int32_t>(key)) {
            if (a == static_cast<std::uint16_t>(key))
                return begin;
            else
                goto fallback;
        }
        ++begin;
        if (begin == end) break;
    }

fallback_check:
    {
        std::uint16_t a = getStreamAttrib(begin);
        if (a == static_cast<std::uint16_t>(key))
            return begin;
    }

fallback:
    return reinterpret_cast<SVertexStream*>(
        static_cast<std::uint8_t*>(container) + 0x10);
}

}  // namespace vertex_streams
}  // namespace recovered
}  // namespace nova3

#endif  // NOVA3_RECOVERED_VERTEX_STREAM_GETSTREAM_H