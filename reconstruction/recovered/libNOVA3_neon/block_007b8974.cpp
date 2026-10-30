// CImageLoaderPVR::CDataInfo::getFileDataSize — ELF VA 0x007b8974 (8 B).
// Original A32: `ldr r0,[r0,#0xc]; bx lr`.
#include "cimage_datainfo.h"

using nova3::recovered::video::CDataInfo;

extern "C" std::uint32_t nova3_recovered_pvr_cdatainfo_file_data_size(
    const CDataInfo* self) {
    return self->get_file_data_size();
}