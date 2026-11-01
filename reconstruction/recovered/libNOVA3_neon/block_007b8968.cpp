// CImageLoaderDDS::CDataInfo::needsFlip — ELF VA 0x007b8968 (8 B).
// Original A32: `mov r0,#0; bx lr`.
#include "cimage_datainfo.h"

using nova3::recovered::video::CDataInfo;

extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_needs_flip(
    const CDataInfo* self) {
    return self->needs_flip() ? 1u : 0u;
}