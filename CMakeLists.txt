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

    # Behaviour of the recovered initializer
    # (vox::DriverCallbackInterface::Init, ELF VA 0x009d53f4).  The test includes
    # the recovered TU unchanged and stubs only DetectNeon, so the statics Init
    # clears, the 1-byte NEON flag it writes, and the fact that it reads neither
    # `this` nor its void* argument are asserted on the host.  Instruction-shape
    # and anchor-offset agreement with the reference is measured by
    # tools/compare/driver_callback_init_pilot.py, not here.
    add_executable(driver_callback_init_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/driver_callback_init_test.cpp)
    set_property(TARGET driver_callback_init_behaviour_test PROPERTY CXX_STANDARD 17)
    target_include_directories(driver_callback_init_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(driver_callback_init_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME driver_callback_init_behaviour
             COMMAND driver_callback_init_behaviour_test)

    # Behaviour of three recovered CSoftwareSkinTechnique leaves
    # (checkAvailability 0x00710b50, needOutputBuffer 0x00710b48, attach
    # 0x00710b58; 8 B each, A32, no relocation).  The test includes the recovered
    # TU unchanged and asserts the constant predicates and the single +0x0c
    # pointer store over sentinel-filled objects.  Byte equality with the
    # reference is measured by tools/compare/collada_skin_trivial_pilot.py, not
    # here.
    add_executable(collada_skin_technique_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/collada_skin_technique_test.cpp
        reconstruction/tests/recovered/libNOVA3_neon/collada_skin_cache_stubs.cpp)
    set_property(TARGET collada_skin_technique_behaviour_test
                 PROPERTY CXX_STANDARD 17)
    target_include_directories(collada_skin_technique_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(collada_skin_technique_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME collada_skin_technique_behaviour
             COMMAND collada_skin_technique_behaviour_test)

    # Behaviour of the recovered CSoftwareSkinTechnique::prepareCache
    # (ELF VA 0x0073f0a4, 228 B, A32).  The test includes the recovered TU
    # unchanged and defines the five dependencies it calls with the reference's
    # mangled names, so the real call sites are exercised: the flag-bit guard,
    # the prepare -> resize -> size -> multiply order, the resize count from the
    # bind object's +0x4c field, the in-place fill value, one 64-byte product per
    # collected pointer at stride 0x40, and the bit-0 clear.  Instruction
    # agreement is measured by tools/compare/collada_skin_prepare_cache_pilot.py
    # (228 B reference vs 224 B candidate), not here.
    add_executable(collada_skin_prepare_cache_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/collada_skin_prepare_cache_test.cpp)
    set_property(TARGET collada_skin_prepare_cache_behaviour_test
                 PROPERTY CXX_STANDARD 17)
    target_include_directories(collada_skin_prepare_cache_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(collada_skin_prepare_cache_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME collada_skin_prepare_cache_behaviour
             COMMAND collada_skin_prepare_cache_behaviour_test)

    # Behaviour of the recovered glitch::core::operator*<float,
    # collada::SMatrix>(CMatrix4<float> const&, SMatrix const&)
    # (ELF VA 0x006e7e60, 564 B, A32).  The test includes the recovered TU
    # unchanged and supplies only the CMatrix4(eConstructor) body, so the real
    # arithmetic and the real constructor call are exercised: the 3x3 part and the
    # translation row are checked against an independently written generic 4x4
    # product, the result's fourth column is proved to be the literal 0.0f/1.0f
    # (the CMatrix4's (3,3) element is never read), and the SMatrix's fourth float
    # per row is proved unread with both a large sentinel and a NaN.
    # Instruction agreement is measured by tools/compare/collada_matrix_multiply_pilot.py.
    add_executable(collada_matrix_multiply_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/collada_matrix_multiply_test.cpp)
    set_property(TARGET collada_matrix_multiply_behaviour_test
                 PROPERTY CXX_STANDARD 17)
    target_include_directories(collada_matrix_multiply_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(collada_matrix_multiply_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME collada_matrix_multiply_behaviour
             COMMAND collada_matrix_multiply_behaviour_test)

    # Behaviour of the recovered glitch::core::CMatrix4<float>::CMatrix4(
    # eConstructor) (ELF VA 0x00108c54, 56 B, A32).  The test includes the
    # recovered TU unchanged and stubs nothing.  Asserted: values 0 and 1 write
    # no byte at all; every value >= 2 zeroes the 64-byte object and sets floats
    # 0/5/10/15 to 1.0f (the 4x4 identity, with off-diagonals exactly +0.0f); no
    # store lands outside the object; reconstruction is idempotent and a later
    # value <= 1 does not disturb an already-built identity.
    # Instruction agreement is measured by tools/compare/collada_matrix4_ctor_pilot.py.
    add_executable(collada_matrix4_ctor_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_ctor_test.cpp)
    set_property(TARGET collada_matrix4_ctor_behaviour_test
                 PROPERTY CXX_STANDARD 17)
    target_include_directories(collada_matrix4_ctor_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(collada_matrix4_ctor_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME collada_matrix4_ctor_behaviour
             COMMAND collada_matrix4_ctor_behaviour_test)

    # Behaviour of the recovered glitch::core::CMatrix4<float>::CMatrix4(
    # CMatrix4<float> const&, eConstructor) (ELF VA 0x00108c8c, 240 B, A32).
    # The test includes both recovered constructor TUs unchanged and supplies
    # only the two unrecovered dependencies (getTransposed 0x000eba84,
    # getInverse 0x000ebb08) as instrumented stand-ins.  Asserted per enumerator
    # value: 1 copies the source, 2 builds the identity, 3 writes the transpose
    # of the source into `this`, 4 stores the inverse and zeroes the object on
    # failure, 5 stores transpose(inverse(source)) and skips the transpose when
    # the inverse fails, 0 and >= 6 write nothing at all; guard bytes prove no
    # store lands outside the 64-byte object.
    # Instruction agreement is measured by
    # tools/compare/collada_matrix4_copy_ctor_pilot.py.
    add_executable(collada_matrix4_copy_ctor_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_copy_ctor_test.cpp)
    set_property(TARGET collada_matrix4_copy_ctor_behaviour_test
                 PROPERTY CXX_STANDARD 17)
    target_include_directories(collada_matrix4_copy_ctor_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(collada_matrix4_copy_ctor_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME collada_matrix4_copy_ctor_behaviour
             COMMAND collada_matrix4_copy_ctor_behaviour_test)

    # Behaviour of the recovered glitch::core::CMatrix4<float>::getTransposed(
    # CMatrix4<float>&) const (ELF VA 0x000eba84, 132 B, A32).  The recovered TU
    # is included unchanged and nothing is stubbed: the reference window calls
    # nothing and reads no global.  Asserted: the exact slot mapping
    # destination k <- source (k % 4) * 4 + k / 4, the source never modified, the
    # four diagonal slots fixed and every other slot moved, the mapping an
    # involution, a junk destination fully overwritten, bit patterns moved
    # unchanged (no arithmetic), and the observed element-wise in-place behaviour
    # when out == *this.
    # Instruction agreement is measured by
    # tools/compare/collada_matrix4_transpose_pilot.py.
    add_executable(collada_matrix4_transpose_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_transpose_test.cpp)
    set_property(TARGET collada_matrix4_transpose_behaviour_test
                 PROPERTY CXX_STANDARD 17)
    target_include_directories(collada_matrix4_transpose_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(collada_matrix4_transpose_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME collada_matrix4_transpose_behaviour
             COMMAND collada_matrix4_transpose_behaviour_test)

    # Link path: the three recovered Collada matrix TUs together (plain
    # constructor, copy-with-mode constructor, getTransposed), with only the
    # still-unrecovered getInverse stubbed.  This is what turns the copy
    # constructor's value-3 and value-5 paths from an instrumented stand-in into
    # the recovered transpose arithmetic, so it asserts those two results slot by
    # slot against an independently written transpose and checks that the
    # inverse path is unchanged.
    add_executable(collada_matrix4_transpose_link_path_test
        reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_transpose_link_path_test.cpp)
    set_property(TARGET collada_matrix4_transpose_link_path_test
                 PROPERTY CXX_STANDARD 17)
    target_include_directories(collada_matrix4_transpose_link_path_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(collada_matrix4_transpose_link_path_test PRIVATE -UNDEBUG)
    add_test(NAME collada_matrix4_transpose_link_path
             COMMAND collada_matrix4_transpose_link_path_test)

    # getInverse (0x000ebb08, 776 B) is the last dangling dependency of the
    # skinning group (`collada_skin_cache_deps.h` declares it).  The recovered TU
    # is included unchanged together with the recovered plain constructor, whose
    # body every test object is built through; nothing is stubbed.  Asserted:
    # identity and diagonal inverses, the analytic inverse of a rigid transform
    # in the engine's last-row translation convention, agreement with an
    # independently written Gauss-Jordan inverse, source * inverse = identity, a
    # singular input reporting failure while writing nothing, the measured
    # 1e-08 determinant tolerance, and untouched source storage.
    # Instruction agreement for the 776-byte window is measured separately with
    # tools/compare/gcc443_window_check.py (NDK r8e GCC 4.4.3).
    add_executable(collada_matrix4_inverse_behaviour_test
        reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_inverse_test.cpp)
    set_property(TARGET collada_matrix4_inverse_behaviour_test
                 PROPERTY CXX_STANDARD 17)
    target_include_directories(collada_matrix4_inverse_behaviour_test PRIVATE
        reconstruction/recovered/libNOVA3_neon)
    target_compile_options(collada_matrix4_inverse_behaviour_test PRIVATE -UNDEBUG)
    add_test(NAME collada_matrix4_inverse_behaviour
             COMMAND collada_matrix4_inverse_behaviour_test)
endif()

# ---------------------------------------------------------------------------
# Functions recovered on main before this merge (retained from the mainline
# series): FPS counter, vertex stream layout, texture size arithmetic and the
# Collada database accessor cluster.  The merged source files now live under
# reconstruction/recovered/libNOVA3_neon/.
# ---------------------------------------------------------------------------
if(BUILD_TESTING)
set(NOVA3_RECOVERED reconstruction/recovered/libNOVA3_neon)
function(nova3_recovered_test name test_source)
        add_executable(${name} ${test_source} ${ARGN})
        target_include_directories(${name} PRIVATE ${NOVA3_RECOVERED})
        set_property(TARGET ${name} PROPERTY CXX_STANDARD 17)
        target_compile_options(${name} PRIVATE -UNDEBUG)
        add_test(NAME ${name} COMMAND ${name})
endfunction()

nova3_recovered_test(compute_pitch_test
        reconstruction/tests/compute_pitch_test.cpp ${NOVA3_RECOVERED}/block_0077d37c.cpp)
nova3_recovered_test(compute_size_in_bytes_test
        reconstruction/tests/compute_size_in_bytes_test.cpp
        ${NOVA3_RECOVERED}/block_0077d3cc.cpp ${NOVA3_RECOVERED}/block_0077d43c.cpp)
nova3_recovered_test(virtual_texture_get_size_in_bytes_test
        reconstruction/tests/virtual_texture_get_size_in_bytes_test.cpp ${NOVA3_RECOVERED}/block_0077d4a8.cpp)
nova3_recovered_test(compute_mipmap_size_in_bytes_test
        reconstruction/tests/compute_mipmap_size_in_bytes_test.cpp
        ${NOVA3_RECOVERED}/block_0077d450.cpp ${NOVA3_RECOVERED}/block_0077d470.cpp)
nova3_recovered_test(virtual_texture_get_size_in_bytes_noarg_test
        reconstruction/tests/virtual_texture_get_size_in_bytes_noarg_test.cpp ${NOVA3_RECOVERED}/block_007863e4.cpp)
nova3_recovered_test(itexture_compute_size_test
        reconstruction/tests/itexture_compute_size_test.cpp ${NOVA3_RECOVERED}/block_0077d99c.cpp)
nova3_recovered_test(primitive_count_test
        reconstruction/tests/primitive_count_test.cpp
        ${NOVA3_RECOVERED}/block_0077bf84.cpp ${NOVA3_RECOVERED}/block_0077c1e0.cpp
        ${NOVA3_RECOVERED}/block_0077c250.cpp ${NOVA3_RECOVERED}/block_0077bf74.cpp)
nova3_recovered_test(fps_counter_ctor_test
        reconstruction/tests/fps_counter_ctor_test.cpp ${NOVA3_RECOVERED}/block_0077bb7c.cpp)
nova3_recovered_test(vertex_types_test
        reconstruction/tests/vertex_types_test.cpp ${NOVA3_RECOVERED}/block_0077bf64.cpp
        ${NOVA3_RECOVERED}/block_0077bf54.cpp ${NOVA3_RECOVERED}/block_0077bf44.cpp)
nova3_recovered_test(vertex_streams_test
        reconstruction/tests/vertex_streams_test.cpp ${NOVA3_RECOVERED}/block_0077bd28.cpp ${NOVA3_RECOVERED}/block_0077be04.cpp)
nova3_recovered_test(vertex_stream_getstream_test
        reconstruction/tests/vertex_stream_getstream_test.cpp
        ${NOVA3_RECOVERED}/block_0077bd7c.cpp ${NOVA3_RECOVERED}/block_0077bdc0.cpp)
nova3_recovered_test(ccollada_accessors_test
        reconstruction/tests/ccollada_accessors_test.cpp
        ${NOVA3_RECOVERED}/block_0071017c.cpp
        ${NOVA3_RECOVERED}/block_00710160_v2.cpp
        ${NOVA3_RECOVERED}/block_00710318.cpp
        ${NOVA3_RECOVERED}/block_0071034c.cpp
        ${NOVA3_RECOVERED}/block_00710368_v2.cpp)
nova3_recovered_test(ccollada_by_name_test
        reconstruction/tests/ccollada_by_name_test.cpp
        ${NOVA3_RECOVERED}/block_0071017c.cpp
        ${NOVA3_RECOVERED}/block_007146ac.cpp
        ${NOVA3_RECOVERED}/block_0071476c.cpp)
nova3_recovered_test(ccollada_get_version_test
        reconstruction/tests/ccollada_get_version_test.cpp
        ${NOVA3_RECOVERED}/block_0071017c.cpp)
nova3_recovered_test(cimage_datainfo_test
        reconstruction/tests/cimage_datainfo_test.cpp
        ${NOVA3_RECOVERED}/block_007b892c.cpp
        ${NOVA3_RECOVERED}/block_007b8934.cpp
        ${NOVA3_RECOVERED}/block_007b8958.cpp
        ${NOVA3_RECOVERED}/block_007b8960.cpp
        ${NOVA3_RECOVERED}/block_007b8968.cpp
        ${NOVA3_RECOVERED}/block_007b8970.cpp
        ${NOVA3_RECOVERED}/block_007b8974.cpp
        ${NOVA3_RECOVERED}/block_007b897c.cpp)
endif()
