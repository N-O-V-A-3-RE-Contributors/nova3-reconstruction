cmake_minimum_required(VERSION 3.18)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  project(nova3_zlib_standalone VERSION 1.3.2 LANGUAGES C)
endif()

# Unchanged upstream source subset, pinned by zlib_manifest.json.
add_library(nova3_zlib STATIC
  zlib-1.3.2/adler32.c
  zlib-1.3.2/crc32.c
  zlib-1.3.2/inflate.c
  zlib-1.3.2/inffast.c
  zlib-1.3.2/inftrees.c
  zlib-1.3.2/zutil.c
)
target_include_directories(nova3_zlib PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/zlib-1.3.2")
set_target_properties(nova3_zlib PROPERTIES POSITION_INDEPENDENT_CODE ON)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  enable_testing()
  add_executable(nova3_zlib_check check_inflate.c)
  target_link_libraries(nova3_zlib_check PRIVATE nova3_zlib)
  add_test(NAME zlib_source_build_integration COMMAND nova3_zlib_check)
endif()
