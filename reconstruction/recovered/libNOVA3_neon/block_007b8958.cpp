// CImageLoaderDDS::CDataInfo::getFaceStartOffset — ELF VA 0x007b8958 (8 B).
// Original A32: `ldr r0,[r0,#0x10]; bx lr`.
#include "cimage_datainfo.h"

using nova3::recovered::video::CDataInfo;

extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_face_start_offset(
    const CDataInfo* self) {
    return self->get_face_start_offset();
}