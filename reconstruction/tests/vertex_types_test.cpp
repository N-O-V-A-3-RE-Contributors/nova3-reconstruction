// Data-verified string tables for E_INDEX_TYPE, E_BUFFER_USAGE, E_BUFFER_TYPE.
//
// The string literal values were independently read from the original ELF's
// .rodata using dd/xxd on 2026-10-30 at the addresses resolved from each
// enum's getStringsInternal trampoline.
//
// This test DOES NOT compare ARM instruction bytes — it verifies that the
// enum names match the original string table exactly.

#include "vertex_types.h"

#include <cstdio>
#include <cstring>

using namespace nova3::recovered::vertex_types;

static int g_failures = 0;

static void check_str(const char* label, const char* got, const char* want) {
    if (!got && !want) return;
    if (!got || !want || std::strcmp(got, want) != 0) {
        std::printf("FAIL %s: got \"%s\", want \"%s\"\n",
                    label, got ? got : "(null)", want ? want : "(null)");
        ++g_failures;
    }
}

static void check_null(const char* label, const char* got) {
    if (got != nullptr) {
        std::printf("FAIL %s: expected null, got \"%s\"\n", label, got);
        ++g_failures;
    }
}

// Forward declarations of the recovered trampolines (C linkage).
extern "C" const char* const* nova3_recovered_get_strings_internal_index_type(E_INDEX_TYPE*);
extern "C" const char* const* nova3_recovered_get_strings_internal_buffer_usage(E_BUFFER_USAGE*);
extern "C" const char* const* nova3_recovered_get_strings_internal_buffer_type(E_BUFFER_TYPE*);

int main() {
    // ---- E_INDEX_TYPE ----
    check_str("EIT_U8",   getIndexTypeName(EIT_U8),  "u8");
    check_str("EIT_U16",  getIndexTypeName(EIT_U16), "u16");
    check_str("EIT_U32",  getIndexTypeName(EIT_U32), "u32");
    check_null("EIT out of range", getIndexTypeName(static_cast<E_INDEX_TYPE>(3)));
    check_null("EIT out of range 99", getIndexTypeName(static_cast<E_INDEX_TYPE>(99)));

    // ---- E_BUFFER_USAGE ----
    check_str("EBU_STATIC",  getBufferUsageName(EBU_STATIC),  "Static");
    check_str("EBU_DYNAMIC", getBufferUsageName(EBU_DYNAMIC), "Dynamic");
    check_str("EBU_STREAM",  getBufferUsageName(EBU_STREAM),  "Stream");
    check_str("EBU_SYSTEM",  getBufferUsageName(EBU_SYSTEM),  "System");
    check_null("EBU out of range", getBufferUsageName(static_cast<E_BUFFER_USAGE>(4)));
    check_null("EBU out of range 99", getBufferUsageName(static_cast<E_BUFFER_USAGE>(99)));

    // ---- E_BUFFER_TYPE ----
    check_str("EBT_VERTEX",    getBufferTypeName(EBT_VERTEX),    "Vertex");
    check_str("EBT_INDEX",     getBufferTypeName(EBT_INDEX),     "Index");
    check_str("EBT_PIXEL_PACK", getBufferTypeName(EBT_PIXEL_PACK), "Pixel Pack");
    check_str("EBT_PIXEL_UNPACK", getBufferTypeName(EBT_PIXEL_UNPACK), "Pixel Unpack");
    check_str("EBT_TEXREF",    getBufferTypeName(EBT_TEXTURE_REFERENCE), "Texture Reference");
    check_str("EBT_VSC",       getBufferTypeName(EBT_VERTEX_STATE_COLLECTION),
              "Vertex State Collection");
    check_null("EBT out of range", getBufferTypeName(static_cast<E_BUFFER_TYPE>(6)));
    check_null("EBT out of range 99", getBufferTypeName(static_cast<E_BUFFER_TYPE>(99)));

    // ---- Verify getStringsInternal trampolines return non-null tables ----
    {
        auto* table = nova3_recovered_get_strings_internal_index_type(nullptr);
        if (!table) {
            std::printf("FAIL index_type table is null\n");
            ++g_failures;
        } else {
            check_str("index_type[0]", table[0], "u8");
            check_str("index_type[1]", table[1], "u16");
            check_str("index_type[2]", table[2], "u32");
            check_null("index_type[3]", table[3]);
        }
    }
    {
        auto* table = nova3_recovered_get_strings_internal_buffer_usage(nullptr);
        if (!table) {
            std::printf("FAIL buffer_usage table is null\n");
            ++g_failures;
        } else {
            check_str("buffer_usage[0]", table[0], "Static");
            check_str("buffer_usage[1]", table[1], "Dynamic");
            check_str("buffer_usage[2]", table[2], "Stream");
            check_str("buffer_usage[3]", table[3], "System");
            check_null("buffer_usage[4]", table[4]);
        }
    }
    {
        auto* table = nova3_recovered_get_strings_internal_buffer_type(nullptr);
        if (!table) {
            std::printf("FAIL buffer_type table is null\n");
            ++g_failures;
        } else {
            check_str("buffer_type[0]", table[0], "Vertex");
            check_str("buffer_type[1]", table[1], "Index");
            check_str("buffer_type[2]", table[2], "Pixel Pack");
            check_str("buffer_type[3]", table[3], "Pixel Unpack");
            check_str("buffer_type[4]", table[4], "Texture Reference");
            check_str("buffer_type[5]", table[5], "Vertex State Collection");
            check_null("buffer_type[6]", table[6]);
        }
    }

    if (g_failures == 0) {
        std::printf("vertex_types: all checks passed\n");
        return 0;
    }
    std::printf("vertex_types: %d check(s) FAILED\n", g_failures);
    return 1;
}