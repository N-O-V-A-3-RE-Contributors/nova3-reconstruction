// Declarations only: the glitch::collada / glitch::core machinery that
// CSoftwareSkinTechnique::prepareCache (0x0073f0a4) calls but that is NOT
// reconstructed here.
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// Every symbol below was resolved from the reference `.symtab`/`.dynsym` by
// address, not guessed:
//
//   bl 0x000731c58 -> _ZN6glitch7collada6detail14ISkinTechnique15preparePtrCacheEv
//                     glitch::collada::detail::ISkinTechnique::preparePtrCache()
//   bl 0x00108c54  -> _ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE
//                     glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float>::eConstructor)
//   bl 0x00775520  -> _ZNSt6vectorIN6glitch4core8CMatrix4IfEENS1_17SAlignedAllocatorIS3_Lh4EEEE6resizeEjS3_
//                     std::vector<CMatrix4<float>, SAlignedAllocator<CMatrix4<float>, (unsigned char)4> >::resize(unsigned int, CMatrix4<float>)
//   bl 0x00752470  -> _ZNKSt6vectorIPKN6glitch4core8CMatrix4IfEENS1_10SAllocatorIS5_LNS0_6memory13E_MEMORY_HINTE0EEEE4sizeEv
//                     std::vector<CMatrix4<float> const*, SAllocator<...> >::size() const
//   bl 0x006e7e60  -> _ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_
//                     glitch::core::CMatrix4<float> glitch::core::operator*<float, glitch::collada::SMatrix>(CMatrix4<float> const&, SMatrix const&)
//
// None of their bodies are recovered, so this header declares them with the
// reference's exact mangled names and our source calls them as ordinary
// functions (the ABI of each call is the one the reference emits).  The
// reconstructed translation unit therefore still needs these symbols at link
// time; they are recorded dependencies, not reimplementations.
//
// Two shape facts are established by the instructions of prepareCache and are
// the only reason the types below exist at all:
//   * the stack fill value and the multiplication result are each copied as 64
//     bytes (`ldm`/`stm` x4 of four words), and the element stride of both the
//     matrix vector and the SMatrix array is 0x40, so CMatrix4<float> is 16
//     floats (64 B) and the SMatrix elements are 64 B apart;
//   * `CMatrix4(eConstructor)` is called with r1 = 2.  The enumerator NAME for
//     that value is not recoverable from the reference, so only the observed
//     value is named here.
#ifndef NOVA3_RECOVERED_COLLADA_SKIN_CACHE_DEPS_H
#define NOVA3_RECOVERED_COLLADA_SKIN_CACHE_DEPS_H

namespace glitch {
namespace collada {
// Element stride 0x40 observed in prepareCache; the member layout and meaning
// of SMatrix are NOT reconstructed, so it stays incomplete: only references to
// it are formed here.
struct SMatrix;

namespace detail {
// The reference emits a direct (non-virtual) `bl` to this symbol with `this` in
// r0, so the callee is a member function of a base class whose body lives
// elsewhere.  The base-class relation is INFERRED from the symbol name; no
// inheritance or vtable is modelled here, so it is declared as a free function
// with the reference's mangled name.
void preparePtrCache(void* self)
    asm("_ZN6glitch7collada6detail14ISkinTechnique15preparePtrCacheEv");
}  // namespace detail
}  // namespace collada

namespace core {

// 64 B observed (see the header comment).  Layout of the 16 floats is NOT
// recovered; only the total size is used by the reconstructed code, which never
// reads or writes individual elements.
template <typename T>
class CMatrix4 {
public:
    enum eConstructor {
        kObservedConstructorValue2 = 2,  // value 2; the other enumerators are unknown
    };

    CMatrix4(eConstructor);  // 0x00108c54, NOT RECONSTRUCTED

    T m_observedValues[16];
};

// The three container helpers are std::vector / operator* instantiations whose
// bodies live in the reference library; only the call sites are recovered.
//
// matrixVectorResize is declared taking the fill matrix by const reference even
// though the reference symbol mangles as `...6resizeEjS3_` (by value).  On
// ARM32 both forms pass a pointer in r2, and this declaration reproduces the
// reference's call exactly: the fill value is constructed once, in place, and
// its address is passed.  A by-value declaration makes GCC 4.4.3 build the
// temporary and then copy all 64 bytes into the outgoing argument slot (measured:
// 0xbc instead of 0x84 stack frame, 268 B instead of 228 B for the function), so
// the by-value form cannot be what was compiled here.  The C++ type is therefore
// a modelling choice, not a recovered fact; the ABI of the call is.
void matrixVectorResize(void* vector, unsigned int count,
                        CMatrix4<float> const& value)
    asm("_ZNSt6vectorIN6glitch4core8CMatrix4IfEENS1_17SAlignedAllocatorIS3_"
        "Lh4EEEE6resizeEjS3_");

unsigned int matrixPtrVectorSize(void const* vector)
    asm("_ZNKSt6vectorIPKN6glitch4core8CMatrix4IfEENS1_10SAllocatorIS5_LNS0_6"
        "memory13E_MEMORY_HINTE0EEEE4sizeEv");

CMatrix4<float> multiply(CMatrix4<float> const& lhs,
                         collada::SMatrix const& rhs)
    asm("_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_");

}  // namespace core
}  // namespace glitch

#endif  // NOVA3_RECOVERED_COLLADA_SKIN_CACHE_DEPS_H
