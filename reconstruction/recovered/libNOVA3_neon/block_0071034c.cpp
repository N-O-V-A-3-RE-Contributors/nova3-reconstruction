// CColladaDatabase::getMaterial — ELF VA 0x0071034c (28 B).
// Original A32: ldr r3,[r0]; add r1,r1,r1,lsl#3; ldr r3,[r3,#16];
//               ldr r3,[r3,#32]; ldr r3,[r3,#124];
//               add r0,r3,r1,lsl#2; bx lr
#include "ccollada_database.h"

extern "C" std::uint32_t nova3_recovered_collada_get_material(
    const void* database, std::int32_t index) {
    return nova3::recovered::collada::get_material(database, index);
}