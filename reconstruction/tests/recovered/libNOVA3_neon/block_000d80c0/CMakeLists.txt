cmake_minimum_required(VERSION 3.16)
project(nova3_task70_forwarders LANGUAGES CXX)

enable_testing()
get_filename_component(NOVA3_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../../.." ABSOLUTE)
add_executable(block_000d80c0_test
    "${NOVA3_ROOT}/reconstruction/recovered/libNOVA3_neon/block_000d80c0.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../block_000d80c0_test.cpp")
target_compile_features(block_000d80c0_test PRIVATE cxx_std_17)
set_target_properties(block_000d80c0_test PROPERTIES CXX_EXTENSIONS OFF)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(block_000d80c0_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME task70_billing_forwarders COMMAND block_000d80c0_test)
