// Source-owned recovery of CVertexStreams array accessors.
//
// Reference: Premium libNOVA3_neon.so (ARM EABI, 32-bit LE, A32 mode)
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Recovered functions:
//   _ZNK6glitch5video14CVertexStreams22getActiveAttributeMaskEv  0x0077bd28 (84 B)
//   _ZN6glitch5video14CVertexStreams9addOffsetEi                 0x0077be04 (64 B)
//
// Both iterate the embedded 16-byte stream array starting at this+0x10
// up to the capacity bound at this+0x24, advancing by stride 16.
//
// Layout (inferred from accessor instruction offsets):
//
//   this+0x00 .. this+0x0f : header (not accessed by these functions)
//   this+0x10               : entry[0].field_00 (also used as array-base pointer)
//   this+0x14               : entry[0].active   (tested for zero)
//   this+0x18               : entry[0].data_offset (modified by addOffset)
//   this+0x1c               : entry[0].attrib_bit (halfword, used as shift)
//   this+0x20               : entry[1].field_00
//   this+0x24               : entry[1].active or capacity boundary
//   ...
//
// The empty-array guard loads [this+0x10] (the first word of the first entry
// or a sentinel pointer) and compares against &this->field_at_0x14.  When
// the array is empty, the code at 0x77bb7c stores the sentinel address at
// offset 0x10, and the empty check at 0x77bd30 catches it: if word_0x10
// equals the address of word_0x14, return 0 / no-op.

#ifndef NOVA3_RECOVERED_VERTEX_STREAMS_H
#define NOVA3_RECOVERED_VERTEX_STREAMS_H

#include <cstddef>
#include <cstdint>

namespace nova3 {
namespace recovered {
namespace vertex_streams {

// Layout of one stream entry (16 bytes, aligned).
// Field names INFERRED from the accessor instruction offsets.
struct SVertexStream {
    std::uint32_t field_00;      // +0  (entry base, used as sentinel for empty)
    std::uint32_t active;        // +4  (non-zero = active for mask)
    std::uint32_t data_offset;   // +8  (addOffset target)
    std::uint16_t attrib_bit;    // +12 (bit position for attribute mask, ldrh)
    std::uint16_t field_0e;      // +14 (unused by these accessors)
};

// Overall container layout — the first 16 bytes are header, entries follow.
// The capacity region extends to this+0x24 (36 bytes = header + up to 20
// bytes of entries = 1 full entry + 4 bytes of next entry's first word).
// Real containers are larger; these constants match the original iteration
// bounds observed in the two recovered functions.
struct CVertexStreams {
    std::uint32_t header[4];                    // +0x00
    SVertexStream  entry0;                      // +0x10 (first entry)
    // Additional entries extend past +0x20
};

// Test-sized container: header + up to N entries (16 bytes each).
template <std::size_t N>
struct CVertexStreamsN {
    std::uint32_t header[4];
    SVertexStream  entries[N];
};

// --- getActiveAttributeMask ---
//
// Iterates from this+0x10 in 16-byte steps to the capacity bound.
// For each entry where entry.active != 0: mask |= (1 << entry.attrib_bit).
// The original uses `ldrh` (unsigned halfword) for attrib_bit, so the shift
// is bounded to 0..65535; high bits are naturally masked by the 32-bit shift.

inline std::uint32_t getActiveAttributeMask(
    const void* container, std::uint32_t entry_count)
{
    auto* base = static_cast<const std::uint8_t*>(container);
    auto* ptr  = base + 0x10;
    const auto* end = ptr + entry_count * sizeof(SVertexStream);

    std::uint32_t mask = 0;
    for (; ptr < end; ptr += sizeof(SVertexStream)) {
        auto* e = reinterpret_cast<const SVertexStream*>(ptr);
        if (e->active != 0) {
            mask |= (1u << static_cast<std::uint32_t>(e->attrib_bit));
        }
    }
    return mask;
}

// --- addOffset ---
//
// Iterates the same range and adds delta to every entry's data_offset.
// The original uses unsigned 32-bit wrapping addition (ARM adds with no
// overflow check); we reproduce unsigned-wrap semantics.

inline void addOffset(
    void* container, std::uint32_t entry_count, std::int32_t delta)
{
    auto* base = static_cast<std::uint8_t*>(container);
    auto* ptr  = base + 0x10;
    const auto* end = ptr + entry_count * sizeof(SVertexStream);

    for (; ptr < end; ptr += sizeof(SVertexStream)) {
        auto* e = reinterpret_cast<SVertexStream*>(ptr);
        e->data_offset = static_cast<std::uint32_t>(
            static_cast<std::int32_t>(e->data_offset) + delta);
    }
}

}  // namespace vertex_streams
}  // namespace recovered
}  // namespace nova3

#endif  // NOVA3_RECOVERED_VERTEX_STREAMS_H