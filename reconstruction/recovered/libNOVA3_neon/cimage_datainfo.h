// Shared CDataInfo layout for the glitch::video image-loader family.
//
// Reference ELF: Premium retail lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Field offsets are recovered from the A32 accessor windows around
// ELF VA 0x007b892c..0x007b8980 (CImageLoaderDDS::CDataInfo and
// CImageLoaderPVR::CDataInfo):
//
//   getFileDataSize   DDS 0x007b892c  `ldr r0,[r0,#0xc]; bx lr`
//   getFileDataSize   PVR 0x007b8974  `ldr r0,[r0,#0xc]; bx lr`
//   getFaceStartOffset DDS 0x007b8958 `ldr r0,[r0,#0x10]; bx lr`
//   isLittleEndian    DDS 0x007b8960  `mov r0,#1; bx lr`
//   needsFlip         DDS 0x007b8968  `mov r0,#0; bx lr`
//   getFilePitch      PVR 0x007b897c  `mov r0,#0; bx lr`  (stub; ignores h)
//
// The offset 0x04 pointer (recovered from the longer DDS getFilePitch at
// 0x007b8934, which performs `ldr r3,[r0,#0x4]; ... ldr r0,[r3,#0x10]`)
// is a second, more complex accessor implemented below.

#pragma once

#include <cstddef>
#include <cstdint>

namespace nova3::recovered::video {

// Image-loader CDataInfo record shared by the DDS and PVR loaders.
// Only the fields directly evidenced by the accessor windows above are
// named; the remaining 32-bit slots are opaque.
struct CDataInfo {
    // 0x00
    std::uint32_t opaque_00;
    // 0x04: pointer word to image-header metadata (see DDS getFilePitch
    // chain).  Stored as a 32-bit word so the layout is identical on the
    // ILP32 ARMv7 target and the LP64 host used for behavioural tests.
    std::uint32_t header_word;
    // 0x08
    std::uint32_t opaque_08;
    // 0x0c: file data size in bytes (DDS + PVR getFileDataSize).
    std::uint32_t file_data_size;
    // 0x10: face start offset in bytes (DDS getFaceStartOffset).
    std::uint32_t face_start_offset;

    std::uint32_t get_file_data_size() const noexcept { return file_data_size; }
    std::uint32_t get_face_start_offset() const noexcept { return face_start_offset; }
    bool          is_little_endian() const noexcept { return true; }
    bool          needs_flip() const noexcept { return false; }
    // DDS overload: non-trivial, level-gated, flags-dependent.  Reads the
    // header_word pointer, checks a flags bit at header+0x04, and returns
    // the pitch at header+0x10 when bit 3 is set.
    // See ELF VA 0x007b8934 (36 B) for the original A32 window:
    //   cmp r1,#0; bne ret0; ldr r3,[r0,#4]; ldr r2,[r3,#4];
    //   tst r2,#8; ldrne r0,[r3,#16]; bxne lr; ret0: mov r0,#0; bx lr
    std::uint32_t get_file_pitch_dds(std::uint8_t level) const noexcept {
        if (level != 0) return 0;
        const std::uint32_t* hdr = reinterpret_cast<const std::uint32_t*>(
            static_cast<std::uintptr_t>(header_word));
        const std::uint32_t flags = hdr[1];   // word at hdr+0x04
        if ((flags & 8u) == 0u) return 0;
        return hdr[4];                        // word at hdr+0x10
    }
    // PVR overload: documented stub that returns 0 for every mip level.
    std::uint32_t get_file_pitch(std::uint8_t /*h*/) const noexcept { return 0; }
};

static_assert(offsetof(CDataInfo, header_word) == 0x04, "header_word offset");
static_assert(offsetof(CDataInfo, file_data_size) == 0x0c, "file_data_size offset");
static_assert(offsetof(CDataInfo, face_start_offset) == 0x10, "face_start_offset offset");

}  // namespace nova3::recovered::video

// Source-owned extern "C" wrappers used for controlled ARM ET_REL compilation
// and comparison; declarations kept here so the behavioural test and the
// wrapper translation units share one ABI.
extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_file_data_size(const nova3::recovered::video::CDataInfo*);
extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_face_start_offset(const nova3::recovered::video::CDataInfo*);
extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_is_little_endian(const nova3::recovered::video::CDataInfo*);
extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_needs_flip(const nova3::recovered::video::CDataInfo*);
extern "C" void         nova3_recovered_dds_cdatainfo_destructor(nova3::recovered::video::CDataInfo*);
extern "C" std::uint32_t nova3_recovered_pvr_cdatainfo_file_data_size(const nova3::recovered::video::CDataInfo*);
extern "C" std::uint32_t nova3_recovered_pvr_cdatainfo_file_pitch(const nova3::recovered::video::CDataInfo*, std::uint8_t);
extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_file_pitch(const nova3::recovered::video::CDataInfo*, std::uint8_t);