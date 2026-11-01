// CImageLoaderDDS::CDataInfo::isLittleEndian — ELF VA 0x007b8960 (8 B).
// Original A32: `mov r0,#1; bx lr`.
#include "cimage_datainfo.h"

using nova3::recovered::video::CDataInfo;

extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_is_little_endian(
    const CDataInfo* self) {
    return self->is_little_endian() ? 1u : 0u;
}