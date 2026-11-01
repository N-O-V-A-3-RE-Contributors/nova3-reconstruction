// Link-time stand-ins for the five dependencies of the recovered
// glitch::collada::detail::CSoftwareSkinTechnique::prepareCache slice, for tests
// that include the recovered translation unit but never call prepareCache.
//
// Each symbol carries the reference's exact mangled name, so the recovered TU
// links unchanged.  They abort if called: this file exists to satisfy the
// linker, not to pretend the dependencies are reconstructed.  The prepareCache
// behaviour test defines its own recording versions of the same symbols in its
// own executable, so this file is never linked alongside it.
#include <cstdio>
#include <cstdlib>

namespace {
[[noreturn]] void unreachable(const char* what) {
    std::fprintf(stderr,
                 "FAIL: %s was called, but this test links no-op dependency "
                 "stubs only\n", what);
    std::abort();
}
}  // namespace

extern "C" {

void collada_skin_cache_stub_prepare_ptr_cache(void*)
    asm("_ZN6glitch7collada6detail14ISkinTechnique15preparePtrCacheEv");
void collada_skin_cache_stub_matrix_ctor(void*, int)
    asm("_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE");
void collada_skin_cache_stub_matrix_resize(void*, unsigned int, void const*)
    asm("_ZNSt6vectorIN6glitch4core8CMatrix4IfEENS1_17SAlignedAllocatorIS3_"
        "Lh4EEEE6resizeEjS3_");
unsigned int collada_skin_cache_stub_ptr_size(void const*)
    asm("_ZNKSt6vectorIPKN6glitch4core8CMatrix4IfEENS1_10SAllocatorIS5_LNS0_6"
        "memory13E_MEMORY_HINTE0EEEE4sizeEv");
void collada_skin_cache_stub_multiply(void*, void const*, void const*)
    asm("_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_");

void collada_skin_cache_stub_prepare_ptr_cache(void*) {
    unreachable("ISkinTechnique::preparePtrCache");
}
void collada_skin_cache_stub_matrix_ctor(void*, int) {
    unreachable("CMatrix4<float>::CMatrix4(eConstructor)");
}
void collada_skin_cache_stub_matrix_resize(void*, unsigned int, void const*) {
    unreachable("vector<CMatrix4<float>>::resize");
}
unsigned int collada_skin_cache_stub_ptr_size(void const*) {
    unreachable("vector<CMatrix4<float> const*>::size");
}
void collada_skin_cache_stub_multiply(void*, void const*, void const*) {
    unreachable("glitch::core::operator*<float, SMatrix>");
}

}  // extern "C"
