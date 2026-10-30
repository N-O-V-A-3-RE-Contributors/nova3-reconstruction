// Host behavioural tests for the CImageLoaderDDS/PVR CDataInfo accessors.
// These verify the recovered field offsets and constant returns against the
// A32 accessor windows in Premium libNOVA3_neon.so (SHA-256
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf):
//   getFileDataSize   -> word at +0x0c
//   getFaceStartOffset-> word at +0x10
//   isLittleEndian    -> 1
//   needsFlip         -> 0
//   getFilePitch(PVR) -> 0 (stub)
//   getFilePitch(DDS) -> level 0 reads header_word->(+0x04 flags & 8)->(+0x10);
//                          non-zero level returns 0.
//
// The offsetof assertions are the primary guard: a reconstruction that puts
// file_data_size at the wrong offset fails at compile time, while a wrong
// constant in isLittleEndian/needsFlip fails at run time.
//
// DDS getFilePitch requires a 32-bit-addressable header struct; the test
// creates one in static storage, similar to the collada getVersion pattern.
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "cimage_datainfo.h"

using nova3::recovered::video::CDataInfo;

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        std::fprintf(stderr, "FAIL: %s\n", msg); \
        ++failures; \
    } \
} while (0)

// Test header struct for DDS getFilePitch: the header_word pointer in the
// CDataInfo record points here.  The layout below mirrors the offsets
// recovered from the 0x007b8934 A32 window.
//   header[1] = +0x04: flags word (bit 3 is the "has pitch" predicate)
//   header[4] = +0x10: file_pitch value returned when bit 3 is set
struct TestDDSHeader {
    std::uint32_t opaque_00;
    std::uint32_t flags;       // +0x04
    std::uint32_t opaque_08;
    std::uint32_t opaque_0c;
    std::uint32_t file_pitch;  // +0x10  (header[4])
};

int main() {
    // Guard: this test requires the header struct to be 32-bit addressable
    // for the uint32_t header_word pointer to work on LP64.
    TestDDSHeader test_header;
    if ((std::uintptr_t)&test_header > 0xFFFFFFFFu) {
        std::fprintf(stderr, "SKIP: test header not 32-bit addressable\n");
        return 0;
    }

    CDataInfo info;
    std::memset(&info, 0, sizeof(info));

    // Sentinels: choose values that would collide if the field were read from
    // a neighbouring offset (0x08 or 0x10 for a +0x0c misread).
    info.opaque_08 = 0xDEADBEEFu;
    info.file_data_size = 0x12345678u;
    info.face_start_offset = 0x0BADC0DEu;

    CHECK(offsetof(CDataInfo, file_data_size) == 0x0c, "file_data_size at 0x0c");
    CHECK(offsetof(CDataInfo, face_start_offset) == 0x10, "face_start_offset at 0x10");
    CHECK(offsetof(CDataInfo, header_word) == 0x04, "header_word at 0x04");

    // getFileDataSize returns exactly the word at +0x0c (DDS and PVR share it).
    CHECK(nova3_recovered_dds_cdatainfo_file_data_size(&info) == 0x12345678u,
          "DDS file data size reads +0x0c");
    CHECK(nova3_recovered_dds_cdatainfo_file_data_size(&info) ==
              nova3_recovered_pvr_cdatainfo_file_data_size(&info),
          "DDS and PVR file data size agree");

    // getFaceStartOffset returns the word at +0x10.
    CHECK(nova3_recovered_dds_cdatainfo_face_start_offset(&info) == 0x0BADC0DEu,
          "face start offset reads +0x10");

    // Constant accessors.
    CHECK(nova3_recovered_dds_cdatainfo_is_little_endian(&info) == 1u,
          "isLittleEndian == 1");
    CHECK(nova3_recovered_dds_cdatainfo_needs_flip(&info) == 0u,
          "needsFlip == 0");

    // PVR getFilePitch stub returns 0 for every mip level.
    for (std::uint32_t h = 0; h < 16; ++h) {
        CHECK(nova3_recovered_pvr_cdatainfo_file_pitch(&info, (std::uint8_t)h) == 0u,
              "PVR getFilePitch == 0");
    }

    // Destructor is a no-op: invoking it must not corrupt the structure.
    nova3_recovered_dds_cdatainfo_destructor(&info);
    CHECK(info.file_data_size == 0x12345678u,
          "trivial destructor leaves fields intact");

    // ----- DDS getFilePitch (0x007b8934) -----
    std::memset(&test_header, 0, sizeof(test_header));

    // Non-zero level always returns 0, even with a valid header ptr + flags.
    info.header_word = (std::uint32_t)(std::uintptr_t)&test_header;
    test_header.flags = 8;   // bit 3 set: "has pitch"
    test_header.file_pitch = 0xCAFEBABEu;
    CHECK(nova3_recovered_dds_cdatainfo_file_pitch(&info, 1) == 0u,
          "DDS getFilePitch level=1 returns 0");
    CHECK(nova3_recovered_dds_cdatainfo_file_pitch(&info, 255) == 0u,
          "DDS getFilePitch level=255 returns 0");

    // Level 0 with flags bit 3 set: returns file_pitch.
    CHECK(nova3_recovered_dds_cdatainfo_file_pitch(&info, 0) == 0xCAFEBABEu,
          "DDS getFilePitch level=0 flags=8 returns file_pitch");

    // Level 0 with flags bit 3 clear: returns 0.
    test_header.flags = 0;
    test_header.file_pitch = 0xCAFEBABEu;
    CHECK(nova3_recovered_dds_cdatainfo_file_pitch(&info, 0) == 0u,
          "DDS getFilePitch level=0 flags=0 returns 0");

    // Level 0 with only bit 3 set, others clear: still returns pitch.
    test_header.flags = 8;
    CHECK(nova3_recovered_dds_cdatainfo_file_pitch(&info, 0) == 0xCAFEBABEu,
          "DDS getFilePitch level=0 flags=8 isolated returns pitch");

    // Level 0 with bit 3 set among other bits: still returns pitch.
    test_header.flags = 0xFFFFFFFFu;
    CHECK(nova3_recovered_dds_cdatainfo_file_pitch(&info, 0) == 0xCAFEBABEu,
          "DDS getFilePitch level=0 flags=all set returns pitch");

    // A different sentinel to ensure we're reading the right slot.
    test_header.flags = 8;
    test_header.file_pitch = 0x31415926u;
    CHECK(nova3_recovered_dds_cdatainfo_file_pitch(&info, 0) == 0x31415926u,
          "DDS getFilePitch reads correct +0x10 word");

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("cimage_datainfo: all tests passed\n");
    return 0;
}