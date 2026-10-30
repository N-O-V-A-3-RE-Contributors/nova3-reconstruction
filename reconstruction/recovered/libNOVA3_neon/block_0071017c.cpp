// CColladaDatabase::getVersion — ELF VA 0x0071017c (20 B).
// Original A32 is a four-level 32-bit pointer walk:
//   ldr r3,[r0] ; ldr r3,[r3,#0x10] ; ldr r3,[r3,#0x20] ; ldr r0,[r3] ; bx lr
#include "ccollada_database.h"

extern "C" std::uint32_t nova3_recovered_collada_database_version(
    const void* database) {
    return nova3::recovered::collada::get_version(database);
}