// CImageLoaderPVR::CDataInfo::getFilePitch(unsigned char) — ELF VA 0x007b897c (8 B).
// Original A32: `mov r0,#0; bx lr` — a stub returning 0 for every mip level.
#include "cimage_datainfo.h"

using nova3::recovered::video::CDataInfo;

extern "C" std::uint32_t nova3_recovered_pvr_cdatainfo_file_pitch(
    const CDataInfo* self, std::uint8_t h) {
    return self->get_file_pitch(h);
}