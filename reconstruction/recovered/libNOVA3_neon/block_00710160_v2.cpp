// CColladaDatabase::linkInstanceMaterial — ELF VA 0x00710160 (28 B).
// Original A32: ldr r3,[r0]; ldr r3,[r3,#16]; ldr r3,[r3,#32];
//               ldr r2,[r3,#8]; str r2,[r1,#64]; str r1,[r3,#8]; bx lr
#include "ccollada_database.h"

extern "C" void nova3_recovered_collada_link_instance_material(
    void* database, void* instance) {
    nova3::recovered::collada::link_instance_material(database, instance);
}