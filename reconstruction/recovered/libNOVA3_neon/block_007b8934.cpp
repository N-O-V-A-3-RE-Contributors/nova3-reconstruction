// CImageLoaderDDS::CDataInfo::getFilePitch — ELF VA 0x007b8934 (36 B).
// Original A32: cmp r1,#0; bne ret0; ldr r3,[r0,#4]; ldr r2,[r3,#4];
//               tst r2,#8; ldrne r0,[r3,#16]; bxne lr;
//               ret0: mov r0,#0; bx lr
#include "cimage_datainfo.h"

using nova3::recovered::video::CDataInfo;

extern "C" std::uint32_t nova3_recovered_dds_cdatainfo_file_pitch(
    const CDataInfo* self, std::uint8_t level) {
    return self->get_file_pitch_dds(level);
}