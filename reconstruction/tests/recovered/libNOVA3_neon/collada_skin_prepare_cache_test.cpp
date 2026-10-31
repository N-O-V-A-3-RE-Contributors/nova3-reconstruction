// Standalone source-only regression for the recovered
// glitch::collada::detail::CSoftwareSkinTechnique::prepareCache slice
// (ELF VA 0x0073f0a4, 228 B, A32).
//
// The recovered translation unit is included unchanged.  Its five unresolved
// dependencies (the base pointer-cache fill, the CMatrix4 constructor, the
// matrix-vector resize, the pointer-vector size and the matrix*SMatrix operator)
// are defined here with the reference's exact mangled names, so the call sites
// under test are the ones the reference emits and deleting one breaks the link
// instead of silently passing.
//
// What is asserted, all of it read off the reference instructions:
//   * bit 0 of the flag word gates everything: clear -> no call, no store, the
//     other 31 bits untouched;
//   * the base pointer-cache fill runs first, then the resize, then the size
//     query, then one multiply per collected pointer;
//   * the resize count is the bind object's +0x4c field, which is independent of
//     the pointer-vector size;
//   * the fill value handed to the resize is the constructor's value 2 built in
//     place;
//   * each iterate multiplies sources[i] by the SMatrix at i*0x40 and stores the
//     64-byte product at destination[i] (destination read *after* the resize,
//     which is why the destination buffer is replaced by the resize stub);
//   * bit 0 is cleared afterwards, and a second call does nothing.
//
// Machine-code agreement with the reference is measured separately by
// tools/compare/collada_skin_prepare_cache_pilot.py and is NOT claimed here: the
// reference window is 228 B and the -O2/-O3 candidate is 224 B.
#define NOVA3_RECOVERED_TEST_HOOKS 1

#include "../../../recovered/libNOVA3_neon/collada_skin_technique.cpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

using glitch::collada::detail::SBindInputs;
using glitch::collada::detail::SMatrixCache;
using glitch::collada::detail::CSoftwareSkinTechnique;
using glitch::core::CMatrix4;

// The reference is ARM32 with 4-byte pointers and `this`+0x00 is untouched, so
// the host never asserts absolute offsets; it asserts the behaviour instead.

constexpr unsigned kSMatrixStride = 0x40;
constexpr unsigned kElementSize = 64;
constexpr unsigned kMaxItems = 8;

void require(bool condition, const char* what) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        std::abort();
    }
}

// ---------------------------------------------------------------- stub state
unsigned g_prepare_ptr_cache_calls = 0;
unsigned g_ctor_calls = 0;
int g_ctor_kind = -1;
unsigned g_resize_calls = 0;
unsigned g_resize_count = 0;
alignas(64) unsigned char g_resize_value_bytes[64];
unsigned g_size_calls = 0;
unsigned g_multiply_calls = 0;
unsigned g_sequence = 0;
unsigned g_prepare_seq = 0, g_resize_seq = 0, g_size_seq = 0;
unsigned g_multiply_seq[kMaxItems];

unsigned g_ptr_count = 0;
CMatrix4<float>* g_sources[kMaxItems];

alignas(64) unsigned char g_matrix_pool[kMaxItems * kElementSize];
// Not the pool: if the source read the destination before the resize, the test
// would see writes here instead of at the pool.
alignas(64) unsigned char g_decoy_destination[kElementSize];

// Recorded arguments of the last multiply call.
CMatrix4<float> const* g_last_lhs = nullptr;
unsigned char g_last_rhs[kElementSize];

}  // namespace

// --------------------------------------------------------------- the stubs
// Each definition carries the reference symbol name, so the recovered TU's call
// resolves to it and nothing else.
extern "C" {

void stub_prepare_ptr_cache(void* self)
    asm("_ZN6glitch7collada6detail14ISkinTechnique15preparePtrCacheEv");
void stub_matrix_ctor(void* self, int kind)
    asm("_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE");
void stub_matrix_resize(void* vector, unsigned int count,
                        CMatrix4<float> const& value)
    asm("_ZNSt6vectorIN6glitch4core8CMatrix4IfEENS1_17SAlignedAllocatorIS3_"
        "Lh4EEEE6resizeEjS3_");
unsigned int stub_ptr_size(void const* vector)
    asm("_ZNKSt6vectorIPKN6glitch4core8CMatrix4IfEENS1_10SAllocatorIS5_LNS0_6"
        "memory13E_MEMORY_HINTE0EEEE4sizeEv");
void stub_multiply(CMatrix4<float>* out, CMatrix4<float> const& lhs,
                   void const* rhs)
    asm("_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_");

void stub_prepare_ptr_cache(void* self) {
    require(self != nullptr, "preparePtrCache received a null this");
    ++g_prepare_ptr_cache_calls;
    g_prepare_seq = ++g_sequence;
}

// The observed constructor writes 64 bytes; the pattern is this test's, not the
// original's, so only "kind travels" and "64 bytes were written" are checked.
void stub_matrix_ctor(void* self, int kind) {
    ++g_ctor_calls;
    g_ctor_kind = kind;
    CMatrix4<float>* matrix = static_cast<CMatrix4<float>*>(self);
    for (int i = 0; i < 16; ++i) {
        matrix->m_observedValues[i] = static_cast<float>(kind * 100 + i);
    }
}

void stub_matrix_resize(void* vector, unsigned int count,
                        CMatrix4<float> const& value) {
    ++g_resize_calls;
    g_resize_seq = ++g_sequence;
    g_resize_count = count;
    std::memcpy(g_resize_value_bytes, &value, sizeof g_resize_value_bytes);
    require(count <= kMaxItems, "resize count exceeds the stub pool");
    // A real vector reallocates; the recovered code must read the destination
    // pointer *after* this call, so a decoy is installed before it runs.
    std::memset(g_matrix_pool, 0, sizeof g_matrix_pool);
    *static_cast<void**>(vector) = count ? g_matrix_pool : nullptr;
}

unsigned int stub_ptr_size(void const* vector) {
    ++g_size_calls;
    g_size_seq = ++g_sequence;
    require(vector != nullptr, "size() received a null vector");
    return g_ptr_count;
}

void stub_multiply(CMatrix4<float>* out, CMatrix4<float> const& lhs,
                   void const* rhs) {
    require(g_multiply_calls < kMaxItems, "too many multiply calls");
    g_multiply_seq[g_multiply_calls] = ++g_sequence;
    ++g_multiply_calls;
    g_last_lhs = &lhs;
    std::memcpy(g_last_rhs, rhs, kElementSize);
    // Deterministic, input-dependent output, so both the stored destination and
    // the operand identity can be checked.
    const unsigned char* bytes = reinterpret_cast<const unsigned char*>(&lhs);
    for (int i = 0; i < 16; ++i) {
        out->m_observedValues[i] =
            lhs.m_observedValues[i] + static_cast<float>(bytes[0]) + i;
    }
}

}  // extern "C"

namespace glitch {
namespace collada {
namespace detail {

// The only privileged access the test needs: the two layout views are private.
struct CSoftwareSkinTechniqueTestAccess {
    static SBindInputs*& bind(CSoftwareSkinTechnique& technique) {
        return technique.m_bindInputs;
    }
    static SMatrixCache*& cache(CSoftwareSkinTechnique& technique) {
        return technique.m_matrixCache;
    }
};

}  // namespace detail
}  // namespace collada
}  // namespace glitch

namespace {

using glitch::collada::detail::CSoftwareSkinTechniqueTestAccess;

struct Fixture {
    SBindInputs bind{};
    SMatrixCache cache{};
    CSoftwareSkinTechnique technique{};
    alignas(64) unsigned char smatrices[kMaxItems * kSMatrixStride];

    Fixture() {
        std::memset(&bind, 0, sizeof bind);
        std::memset(&cache, 0, sizeof cache);
        std::memset(smatrices, 0, sizeof smatrices);
        CSoftwareSkinTechniqueTestAccess::bind(technique) = &bind;
        CSoftwareSkinTechniqueTestAccess::cache(technique) = &cache;
    }

    void reset_counters() {
        g_prepare_ptr_cache_calls = 0;
        g_ctor_calls = 0;
        g_ctor_kind = -1;
        g_resize_calls = 0;
        g_resize_count = 0;
        g_size_calls = 0;
        g_multiply_calls = 0;
        g_sequence = 0;
        g_prepare_seq = g_resize_seq = g_size_seq = 0;
        g_last_lhs = nullptr;
        std::memset(g_last_rhs, 0, sizeof g_last_rhs);
    }
};

// Raw storage: the recovered CMatrix4 declares only the constructor the
// reference calls, so the test never default-constructs one.
alignas(64) unsigned char g_source_storage[kMaxItems * kElementSize];

CMatrix4<float>* source_at(unsigned index) {
    return reinterpret_cast<CMatrix4<float>*>(
        g_source_storage + index * kElementSize);
}

void set_sources(unsigned count, float base) {
    g_ptr_count = count;
    std::memset(g_source_storage, 0, sizeof g_source_storage);
    for (unsigned i = 0; i < count; ++i) {
        CMatrix4<float>* matrix = source_at(i);
        for (int j = 0; j < 16; ++j) {
            matrix->m_observedValues[j] = base + static_cast<float>(i * 16 + j);
        }
        g_sources[i] = matrix;
    }
}

}  // namespace

int main() {
    // 1. Guard clear: nothing runs and the other 31 flag bits survive.
    {
        Fixture fixture;
        fixture.reset_counters();
        fixture.cache.m_flags = 0xFFFFFFFEu;  // bit 0 clear
        fixture.bind.m_elementCount = 7;
        set_sources(3, 1.0f);
        fixture.cache.m_matrixPtrBegin = g_sources;
        fixture.cache.m_matrixBegin = static_cast<void*>(g_decoy_destination);

        fixture.technique.prepareCache();

        require(g_prepare_ptr_cache_calls == 0,
                "preparePtrCache ran although the flag bit was clear");
        require(g_resize_calls == 0, "resize ran although the flag bit was clear");
        require(g_size_calls == 0, "size ran although the flag bit was clear");
        require(g_multiply_calls == 0, "multiply ran although the flag bit was clear");
        require(fixture.cache.m_flags == 0xFFFFFFFEu,
                "the flag word changed without the bit being set");
    }

    // 2. Guard set with no collected pointers: the fill still happens, the loop
    //    does not, and bit 0 alone is cleared.
    {
        Fixture fixture;
        fixture.reset_counters();
        fixture.cache.m_flags = 0x80000001u;  // bit 0 set, bit 31 also set
        fixture.bind.m_elementCount = 7;
        fixture.bind.m_smatrixArray = fixture.smatrices;
        fixture.cache.m_matrixBegin = static_cast<void*>(g_decoy_destination);
        fixture.cache.m_matrixPtrBegin = nullptr;
        set_sources(0, 0.0f);
        g_ptr_count = 0;

        fixture.technique.prepareCache();

        require(g_prepare_ptr_cache_calls == 1,
                "preparePtrCache was not called exactly once");
        require(g_ctor_calls == 1, "the fill matrix was not constructed once");
        require(g_ctor_kind == 2, "the fill matrix was not constructed with value 2");
        require(g_resize_calls == 1, "the matrix vector was not resized once");
        require(g_resize_count == 7,
                "the resize count is not the bind object's +0x4c field");
        const CMatrix4<float>& fill =
            *reinterpret_cast<CMatrix4<float> const*>(g_resize_value_bytes);
        require(fill.m_observedValues[0] == 200.0f &&
                    fill.m_observedValues[15] == 215.0f,
                "the fill value is not the object the constructor wrote");
        require(g_size_calls == 1, "the pointer-vector size was not queried once");
        require(g_multiply_calls == 0, "the loop ran with zero collected pointers");
        require(g_prepare_seq < g_resize_seq && g_resize_seq < g_size_seq,
                "calls did not run in prepare -> resize -> size order");
        require(fixture.cache.m_flags == 0x80000000u,
                "only bit 0 should have been cleared");
        require(fixture.cache.m_matrixBegin == g_matrix_pool,
                "the destination pointer was not re-read after the resize");
    }

    // 3. Three collected pointers: one multiply each, in order, storing the
    //    64-byte product at destination[i] with the SMatrix at i*0x40.
    {
        Fixture fixture;
        fixture.reset_counters();
        fixture.cache.m_flags = 1u;
        fixture.bind.m_elementCount = 7;  // deliberately != pointer count
        fixture.bind.m_smatrixArray = fixture.smatrices;
        for (unsigned i = 0; i < kMaxItems; ++i) {
            fixture.smatrices[i * kSMatrixStride] = static_cast<unsigned char>(0x40u + i);
        }
        fixture.cache.m_matrixBegin = static_cast<void*>(g_decoy_destination);
        set_sources(3, 11.0f);
        fixture.cache.m_matrixPtrBegin = g_sources;

        fixture.technique.prepareCache();

        require(g_multiply_calls == 3, "expected one multiply per collected pointer");
        require(g_resize_count == 7,
                "the resize count must stay the bind field, not the pointer count");
        require(g_ptr_count == 3, "the test's pointer count changed unexpectedly");
        for (unsigned i = 0; i < 3; ++i) {
            CMatrix4<float> const* stored =
                reinterpret_cast<CMatrix4<float> const*>(
                    static_cast<unsigned char const*>(g_matrix_pool) +
                    i * kElementSize);
            CMatrix4<float> const& source = *source_at(i);
            const unsigned char* source_bytes =
                reinterpret_cast<const unsigned char*>(&source);
            for (int j = 0; j < 16; ++j) {
                require(stored->m_observedValues[j] ==
                            source.m_observedValues[j] +
                                static_cast<float>(source_bytes[0]) + j,
                        "a product was not stored at destination[i]");
            }
        }
        // Operand identity and stride, checked on the last call.
        require(g_last_lhs == source_at(2),
                "multiply did not receive sources[i] as its left operand");
        require(g_last_rhs[0] == 0x42,
                "multiply did not receive the SMatrix at i*0x40");
        require(g_multiply_seq[0] < g_multiply_seq[1] &&
                    g_multiply_seq[1] < g_multiply_seq[2],
                "the multiply calls did not run in ascending index order");
        require(g_size_seq < g_multiply_seq[0],
                "the size query did not precede the loop");
        require(fixture.cache.m_flags == 0u, "bit 0 was not cleared after the fill");
    }

    // 4. A second call is a no-op once the bit has been cleared.
    {
        Fixture fixture;
        fixture.reset_counters();
        fixture.cache.m_flags = 1u;
        fixture.bind.m_elementCount = 2;
        fixture.bind.m_smatrixArray = fixture.smatrices;
        set_sources(1, 5.0f);
        fixture.cache.m_matrixPtrBegin = g_sources;

        fixture.technique.prepareCache();
        require(g_multiply_calls == 1, "the first call did not fill the cache");

        fixture.reset_counters();
        fixture.technique.prepareCache();
        require(g_prepare_ptr_cache_calls == 0 && g_resize_calls == 0 &&
                    g_size_calls == 0 && g_multiply_calls == 0,
                "a second prepareCache ran after bit 0 was cleared");
        require(fixture.cache.m_flags == 0u, "the flag word changed on the second call");
    }

    std::printf("ok: recovered prepareCache behaves as recovered "
                "(guard, prepare -> resize -> size -> multiply order, "
                "resize count from +0x4c, in-place fill, 64-byte products at "
                "stride 0x40, bit-0 clear)\n");
    return 0;
}
