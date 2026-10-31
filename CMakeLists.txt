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

# Behavioural regression for the reconstructed cpu-features TU.  The test
# compiles the recovered TU into itself so its file-scope helpers can be
# exercised over fixture buffers; it needs no proprietary input and no game
# code.  Machine-code equivalence is measured separately by
# tools/compare/cpufeatures_pilot.py and is not claimed here.
if(BUILD_TESTING)
    add_executable(cpu_features_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/cpu_features_test.c)
    set_property(TARGET cpu_features_behaviour_test PROPERTY C_STANDARD 99)
    target_compile_options(cpu_features_behaviour_test PRIVATE -UNDEBUG -D_GNU_SOURCE)
    find_package(Threads REQUIRED)
    target_link_libraries(cpu_features_behaviour_test PRIVATE Threads::Threads)
    add_test(NAME cpu_features_behaviour COMMAND cpu_features_behaviour_test)

    # End-to-end behaviour of android_cpuInit and the getters.  The recovered
    # source keeps its fixed /proc and /sys paths, so this target redirects
    # open/read/close at link time (--wrap) to an in-memory fixture table; the
    # translation unit under test is unchanged.  See the test file header.
    add_executable(cpu_features_init_test
        reconstruction/tests/recovered/libNOVA3_neon/cpu_features_init_test.c)
    set_property(TARGET cpu_features_init_test PROPERTY C_STANDARD 99)
    target_compile_options(cpu_features_init_test PRIVATE -UNDEBUG -D_GNU_SOURCE)
    target_link_libraries(cpu_features_init_test PRIVATE Threads::Threads
        -Wl,--wrap=open -Wl,--wrap=read -Wl,--wrap=close)
    add_test(NAME cpu_features_behaviour_init COMMAND cpu_features_init_test)

    # Behaviour of the recovered NEON capability probe
    # (vox::DriverCallbackSourceInterface::DetectNeon, ELF VA 0x009dad14).  The
    # test includes the recovered TU unchanged and supplies counting stubs for
    # the two cpufeatures getters, so the probe's short-circuit, single-bit mask
    # test and call order are asserted on the host.  Code-window agreement is
    # measured by tools/compare/detect_neon_pilot.py, not here.
    add_executable(detect_neon_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/driver_callback_detect_neon_test.cpp)
    set_property(TARGET detect_neon_behaviour_test PROPERTY CXX_STANDARD 17)
    target_include_directories(detect_neon_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(detect_neon_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME detect_neon_behaviour COMMAND detect_neon_behaviour_test)
endif()
