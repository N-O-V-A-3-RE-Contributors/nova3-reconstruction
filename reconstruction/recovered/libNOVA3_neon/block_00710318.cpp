// CColladaDatabase::getImage — ELF VA 0x00710318 (24 B).
// Original A32: ldr r3,[r0]; ldr r3,[r3,#16]; ldr r3,[r3,#32];
//               ldr r0,[r3,#108]; add r0,r0,r1,lsl#4; bx lr
#include "ccollada_database.h"

extern "C" std::uint32_t nova3_recovered_collada_get_image(
    const void* database, std::int32_t index) {
    return nova3::recovered::collada::get_image(database, index);
}