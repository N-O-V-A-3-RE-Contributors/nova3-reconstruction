// Host behavioural tests for the CImageLoaderDDS/PVR CDataInfo accessors.
// These verify the recovered field offsets and constant returns against the
// A32 accessor windows in Premium libNOVA3_neon.so (SHA-256
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf):
//   getFileDataSize   -> word at +0x0c
//   getFaceStartOffset-> word at +0x10
//   isLittleEndian    -> 1
//   needsFlip         -> 0
//   getFilePitch(PVR) -> 0 (stub)
//
// The offsetof assertions are the primary guard: a reconstruction that puts
// file_data_size at the wrong offset fails at compile time, while a wrong
// constant in isLittleEndian/needsFlip fails at run time.
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

int main() {
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
    CHECK(info.file_data_size == 0x12345678u, "trivial destructor leaves fields intact");

    if (failures) {
        std::fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    std::printf("cimage_datainfo: all tests passed\n");
    return 0;
}