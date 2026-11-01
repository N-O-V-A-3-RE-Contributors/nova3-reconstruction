// CColladaDatabase::getMaterial by name — ELF VA 0x007146ac (96 B).
// Original A32: push {r4-r8,lr}; 3-level walk to collada_root;
//               ldrsb count@+0x78; loop: strcmp id ptr vs name, stride 36.
// External dependency: strcmp (PLT relocation in original).
#include "ccollada_database.h"

extern "C" std::uint32_t nova3_recovered_collada_get_material_by_name(
    const void* database, const char* name) {
    return nova3::recovered::collada::get_material_by_name(database, name);
}