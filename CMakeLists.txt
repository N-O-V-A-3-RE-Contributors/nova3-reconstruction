cmake_minimum_required(VERSION 3.20)
project(nova3_reconstruction LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
option(BUILD_SHARED_LIBS "Build source-owned core as a shared library" OFF)
add_library(nova3_core reconstruction/engine/crc32.cpp reconstruction/engine/scramble.cpp reconstruction/engine/zip_local.cpp reconstruction/engine/zip_payload.cpp reconstruction/engine/zip_lookup.cpp reconstruction/engine/zip_archive.cpp reconstruction/engine/bres.cpp reconstruction/engine/collada_mesh.cpp reconstruction/engine/pvr_texture.cpp reconstruction/engine/texture_bundle.cpp reconstruction/engine/etc1.cpp reconstruction/engine/texture_path.cpp reconstruction/engine/scrambled_zip.cpp)
target_include_directories(nova3_core PUBLIC reconstruction/engine)
target_compile_options(nova3_core PRIVATE -Wall -Wextra -Wpedantic -Werror)
add_executable(nova3_crc reconstruction/tests/crc_cli.cpp)
target_link_libraries(nova3_crc PRIVATE nova3_core)
include(CTest)
if(BUILD_TESTING)
    add_executable(crc32_test reconstruction/tests/crc32_test.cpp)
    target_link_libraries(crc32_test PRIVATE nova3_core)
    add_test(NAME crc32_vectors COMMAND crc32_test)
endif()

add_executable(nova3_scramble reconstruction/tests/scramble_cli.cpp)
target_link_libraries(nova3_scramble PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(scramble_test reconstruction/tests/scramble_test.cpp)
    target_link_libraries(scramble_test PRIVATE nova3_core)
    add_test(NAME scramble_vectors COMMAND scramble_test)
endif()

add_executable(nova3_zip_header reconstruction/tests/zip_header_cli.cpp)
target_link_libraries(nova3_zip_header PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(zip_local_test reconstruction/tests/zip_local_test.cpp)
    target_link_libraries(zip_local_test PRIVATE nova3_core)
    add_test(NAME zip_local_headers COMMAND zip_local_test)
endif()
add_executable(nova3_zip_index reconstruction/tests/zip_index_cli.cpp)
target_link_libraries(nova3_zip_index PRIVATE nova3_core)

add_subdirectory(reconstruction/third_party)
target_link_libraries(nova3_core PRIVATE nova3_zlib)
add_executable(nova3_zip_extract reconstruction/tests/zip_extract_cli.cpp)
target_link_libraries(nova3_zip_extract PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(zip_payload_test reconstruction/tests/zip_payload_test.cpp)
    target_link_libraries(zip_payload_test PRIVATE nova3_core)
    add_test(NAME zip_payload_decode COMMAND zip_payload_test)
endif()

add_executable(nova3_bres_relocate reconstruction/tests/bres_cli.cpp)
target_link_libraries(nova3_bres_relocate PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(bres_test reconstruction/tests/bres_test.cpp)
    target_link_libraries(bres_test PRIVATE nova3_core)
    add_test(NAME bres_relocation COMMAND bres_test)
    add_executable(zip_lookup_test reconstruction/tests/zip_lookup_test.cpp)
    target_link_libraries(zip_lookup_test PRIVATE nova3_core)
    add_test(NAME zip_native_lookup COMMAND zip_lookup_test)
endif()
add_executable(nova3_resource reconstruction/tests/resource_cli.cpp)
target_link_libraries(nova3_resource PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(zip_archive_test reconstruction/tests/zip_archive_test.cpp)
    target_link_libraries(zip_archive_test PRIVATE nova3_core)
    add_test(NAME zip_archive_resources COMMAND zip_archive_test)
endif()
add_executable(nova3_zip_lookup reconstruction/tests/zip_lookup_cli.cpp)
target_link_libraries(nova3_zip_lookup PRIVATE nova3_core)
add_executable(nova3_package_probe reconstruction/tests/package_probe_cli.cpp)
target_link_libraries(nova3_package_probe PRIVATE nova3_core)

add_executable(nova3_mesh reconstruction/tests/mesh_cli.cpp)
target_link_libraries(nova3_mesh PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(collada_mesh_test reconstruction/tests/collada_mesh_test.cpp)
    target_link_libraries(collada_mesh_test PRIVATE nova3_core)
    add_test(NAME collada_mesh_decode COMMAND collada_mesh_test)
endif()

add_executable(nova3_texture reconstruction/tests/texture_cli.cpp)
target_link_libraries(nova3_texture PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(pvr_texture_test reconstruction/tests/pvr_texture_test.cpp)
    target_link_libraries(pvr_texture_test PRIVATE nova3_core)
    add_test(NAME pvr_texture_headers COMMAND pvr_texture_test)
endif()
if(BUILD_TESTING)
    add_executable(texture_bundle_test reconstruction/tests/texture_bundle_test.cpp)
    target_link_libraries(texture_bundle_test PRIVATE nova3_core)
    add_test(NAME texture_split_bundle COMMAND texture_bundle_test)
endif()

add_executable(nova3_etc1 reconstruction/tests/etc1_cli.cpp)
target_link_libraries(nova3_etc1 PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(etc1_test reconstruction/tests/etc1_test.cpp)
    target_link_libraries(etc1_test PRIVATE nova3_core)
    add_test(NAME etc1_pixels COMMAND etc1_test)
endif()

# Assertions in regression executables must also run in clean Release builds.
if(BUILD_TESTING)
    foreach(test_target crc32_test scramble_test zip_local_test zip_payload_test bres_test zip_lookup_test zip_archive_test collada_mesh_test pvr_texture_test texture_bundle_test etc1_test)
        target_compile_options(${test_target} PRIVATE -UNDEBUG)
    endforeach()
endif()

add_executable(nova3_texture_path reconstruction/tests/texture_path_cli.cpp)
target_link_libraries(nova3_texture_path PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(texture_path_test reconstruction/tests/texture_path_test.cpp)
    target_link_libraries(texture_path_test PRIVATE nova3_core)
    target_compile_options(texture_path_test PRIVATE -UNDEBUG)
    add_test(NAME texture_filename_policy COMMAND texture_path_test)
endif()

add_executable(nova3_scrambled_zip reconstruction/tests/scrambled_zip_cli.cpp)
target_link_libraries(nova3_scrambled_zip PRIVATE nova3_core)
if(BUILD_TESTING)
    add_executable(scrambled_zip_test reconstruction/tests/scrambled_zip_test.cpp)
    target_link_libraries(scrambled_zip_test PRIVATE nova3_core)
    target_compile_options(scrambled_zip_test PRIVATE -UNDEBUG)
    add_test(NAME scrambled_zip_headers COMMAND scrambled_zip_test)
endif()

# Recovered function: computePitch (0x0077d37c)
if(BUILD_TESTING)
    add_executable(compute_pitch_test
        reconstruction/tests/compute_pitch_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_0077d37c.cpp)
    target_include_directories(compute_pitch_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(compute_pitch_test PRIVATE -UNDEBUG)
    add_test(NAME compute_pitch_arithmetic COMMAND compute_pitch_test)
endif()

# Recovered function: computeSizeInBytes (0x0077d3cc) + slices overload (0x0077d43c)
if(BUILD_TESTING)
    add_executable(compute_size_in_bytes_test
        reconstruction/tests/compute_size_in_bytes_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_0077d3cc.cpp
        reconstruction/recovered/libNOVA3_neon/block_0077d43c.cpp)
    target_include_directories(compute_size_in_bytes_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(compute_size_in_bytes_test PRIVATE -UNDEBUG)
    add_test(NAME compute_size_in_bytes_arithmetic COMMAND compute_size_in_bytes_test)
endif()

# Recovered function: CVirtualTexture::getSizeInBytes(unsigned char) (0x0077d4a8)
if(BUILD_TESTING)
    add_executable(virtual_texture_get_size_in_bytes_test
        reconstruction/tests/virtual_texture_get_size_in_bytes_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_0077d4a8.cpp)
    target_include_directories(virtual_texture_get_size_in_bytes_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(virtual_texture_get_size_in_bytes_test PRIVATE -UNDEBUG)
    add_test(NAME virtual_texture_get_size_in_bytes COMMAND virtual_texture_get_size_in_bytes_test)
endif()

# Recovered functions: computeMipmapSizeInBytes (0x0077d450) + slices overload (0x0077d470)
if(BUILD_TESTING)
    add_executable(compute_mipmap_size_in_bytes_test
        reconstruction/tests/compute_mipmap_size_in_bytes_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_0077d450.cpp
        reconstruction/recovered/libNOVA3_neon/block_0077d470.cpp)
    target_include_directories(compute_mipmap_size_in_bytes_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(compute_mipmap_size_in_bytes_test PRIVATE -UNDEBUG)
    add_test(NAME compute_mipmap_size_in_bytes_arithmetic COMMAND compute_mipmap_size_in_bytes_test)
endif()

# Recovered function: CVirtualTexture::getSizeInBytes() no-arg (0x007863e4)
if(BUILD_TESTING)
    add_executable(virtual_texture_get_size_in_bytes_noarg_test
        reconstruction/tests/virtual_texture_get_size_in_bytes_noarg_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_007863e4.cpp)
    target_include_directories(virtual_texture_get_size_in_bytes_noarg_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(virtual_texture_get_size_in_bytes_noarg_test PRIVATE -UNDEBUG)
    add_test(NAME virtual_texture_get_size_in_bytes_noarg COMMAND virtual_texture_get_size_in_bytes_noarg_test)
endif()

# Recovered function: ITexture::computeSizeInBytes() const (0x0077d99c)
if(BUILD_TESTING)
    add_executable(itexture_compute_size_test
        reconstruction/tests/itexture_compute_size_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_0077d99c.cpp)
    target_compile_options(itexture_compute_size_test PRIVATE -UNDEBUG)
    add_test(NAME itexture_compute_size COMMAND itexture_compute_size_test)
endif()

# Recovered CDataInfo accessors: DDS/PVR CImageLoader CDataInfo family
#   getFilePitch DDS 0x007b8934
#   getFileDataSize DDS 0x007b892c, PVR 0x007b8974
#   getFaceStartOffset DDS 0x007b8958
#   isLittleEndian DDS 0x007b8960, needsFlip DDS 0x007b8968
#   ~CDataInfo DDS 0x007b8970, getFilePitch PVR 0x007b897c
if(BUILD_TESTING)
    add_executable(cimage_datainfo_test
        reconstruction/tests/cimage_datainfo_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_007b892c.cpp
        reconstruction/recovered/libNOVA3_neon/block_007b8934.cpp
        reconstruction/recovered/libNOVA3_neon/block_007b8958.cpp
        reconstruction/recovered/libNOVA3_neon/block_007b8960.cpp
        reconstruction/recovered/libNOVA3_neon/block_007b8968.cpp
        reconstruction/recovered/libNOVA3_neon/block_007b8970.cpp
        reconstruction/recovered/libNOVA3_neon/block_007b8974.cpp
        reconstruction/recovered/libNOVA3_neon/block_007b897c.cpp)
    target_include_directories(cimage_datainfo_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(cimage_datainfo_test PRIVATE -UNDEBUG)
    add_test(NAME cimage_datainfo COMMAND cimage_datainfo_test)
endif()

# Recovered function: CColladaDatabase::getVersion (0x0071017c)
# Linked non-PIE so the four-level 32-bit pointer walk is addressable on LP64.
if(BUILD_TESTING)
    add_executable(ccollada_get_version_test
        reconstruction/tests/ccollada_get_version_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_0071017c.cpp)
    target_include_directories(ccollada_get_version_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(ccollada_get_version_test PRIVATE -UNDEBUG -fno-pie)
    target_link_options(ccollada_get_version_test PRIVATE -no-pie)
    add_test(NAME ccollada_get_version COMMAND ccollada_get_version_test)
endif()

# Recovered Collada named-parameter accessor cluster:
# getMaterial by name (0x007146ac), getImage by name (0x0071476c).
# Both call strcmp, which carries a relocation in ARM ET_REL.
if(BUILD_TESTING)
    add_executable(ccollada_by_name_test
        reconstruction/tests/ccollada_by_name_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_0071017c.cpp
        reconstruction/recovered/libNOVA3_neon/block_007146ac.cpp
        reconstruction/recovered/libNOVA3_neon/block_0071476c.cpp)
    target_include_directories(ccollada_by_name_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(ccollada_by_name_test PRIVATE -UNDEBUG -fno-pie)
    target_link_options(ccollada_by_name_test PRIVATE -no-pie)
    add_test(NAME ccollada_by_name COMMAND ccollada_by_name_test)
endif()
if(BUILD_TESTING)
    add_executable(ccollada_accessors_test
        reconstruction/tests/ccollada_accessors_test.cpp
        reconstruction/recovered/libNOVA3_neon/block_0071017c.cpp
        reconstruction/recovered/libNOVA3_neon/block_00710160_v2.cpp
        reconstruction/recovered/libNOVA3_neon/block_00710318.cpp
        reconstruction/recovered/libNOVA3_neon/block_0071034c.cpp
        reconstruction/recovered/libNOVA3_neon/block_00710368_v2.cpp)
    target_include_directories(ccollada_accessors_test PRIVATE reconstruction/recovered/libNOVA3_neon)
    target_compile_options(ccollada_accessors_test PRIVATE -UNDEBUG -fno-pie)
    target_link_options(ccollada_accessors_test PRIVATE -no-pie)
    add_test(NAME ccollada_accessors COMMAND ccollada_accessors_test)
endif()
