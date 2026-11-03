// Source-owned recovery of
//
//   glitch::core::CMatrix4<float>::getInverse(CMatrix4<float>& out) const
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   symbol   _ZNK6glitch4core8CMatrix4IfE10getInverseERS2_
//   ELF VA   0x000ebb08, 776 bytes, A32, STT_FUNC, exact `.symtab` bounds.
//
// WHAT THE MACHINE CODE ESTABLISHES (all measured from the 776-byte window):
//
//   * Cramer's rule over eleven 2x2 minors.  The window loads all sixteen floats
//     of `*this` and forms the minors as FLOAT products, converting each pair's
//     difference to double (vmul.f32 ... vcvt.f64.f32), then combines the minors
//     in DOUBLE (vmul.f64 / vmla.f64 / vmls.f64).  So the source difference is
//     `double(f[a] * f[b] - f[c] * f[d])`, not a double product.
//   * The determinant test loads the double constant at 0xebe08 = 1e-08 and does
//     `vabs.f64` + `vcmpe.f64` with a `movls r0, #0` failure return, i.e.
//     `fabs(det) <= 1e-08` -> false.  In `glitch/core/math.h` that is
//     `iszero(det)` with `ROUNDING_ERROR_64`, and 1e-08 is the *non*-fast-math
//     value, so GLITCH_FAST_MATH is NOT defined for this build.
//   * No instruction anywhere in the window reads or writes a byte at offset
//     +0x40, and `CMatrix4<float>` is 64 bytes at every observed stride, so
//     GLITCH_MATRIX4_REMOVE_IDENTITY_BOOL IS defined: the source's
//     `getDefinitelyIdentityMatrix()` identity short-cut and its
//     `out.setDefinitelyIdentityMatrix(...)` epilogue are eliminated code.
//   * The function ends with a 16-element loop that loads each result float,
//     converts to double, multiplies by `1.0 / det`, converts back to float and
//     stores: that is the `#if !defined(GLITCH_FAST_MATH)` scaling loop
//     `out[i] = T(out[i] * det)`.  A fast-math build has no such loop.
//   * Return type: the two callers test r0 for zero before branching, and the
//     success path is a distinct block, so a scalar is observed; `bool` is the
//     INFERRED type consistent with that (the Itanium mangling does not carry a
//     return type).
//   * The result is written only through `out`, never into `*this`, and the
//     window never reads memory beyond the 16 floats of the source.
//
// WHAT IS NOT CLAIMED: original commit equality, compiler flags beyond the ones
// the instruction window demonstrates, or a whole-.so match.  Instruction
// agreement for this 776-byte range is measured separately with NDK r8e
// GCC 4.4.3 by tools/compare/gcc443_window_check.py.
//
// LINEAGE: the algorithm is the classic cofactor/determinant inverse used by
// Irrlicht's zlib-licensed CMatrix4, of which glitch's matrix4 is a derivative.
// This body is written from the arithmetic above and the standard algorithm; it
// is not copied from the private reference tree.
#include "collada_skin_cache_deps.h"

namespace glitch {
namespace core {

namespace {

// 0xebe08 in the reference window: ROUNDING_ERROR_64 for the non-fast-math
// configuration of glitch/core/math.h.
const double kInverseDetTolerance = 1e-08;

// `__builtin_fabs` is used instead of <cmath>/<math.h> so this translation unit
// compiles with the NDK r8e GCC 4.4.3 toolchain (which has no C++ library
// headers on the ARM include path) and so the comparison is the compiler's own
// `vabs.f64` + `vcmpe.f64` sequence, which is what the reference window does.
inline bool isZeroDeterminant(double value) {
    return __builtin_fabs(value) <= kInverseDetTolerance;
}

}  // namespace

template <typename T>
bool CMatrix4<T>::getInverse(CMatrix4<T>& out) const {
    // The reference passes `this` and `out` as distinct objects; the source's
    // assertion on that precondition is not present in the compiled window.
    const T* m = m_observedValues;

    const double t0 = double(m[10] * m[15] - m[11] * m[14]);
    const double t1 = double(m[6] * m[15] - m[7] * m[14]);
    const double t2 = double(m[6] * m[11] - m[7] * m[10]);
    const double t3 = double(m[2] * m[15] - m[3] * m[14]);
    const double t4 = double(m[2] * m[11] - m[3] * m[10]);
    const double t5 = double(m[2] * m[7] - m[3] * m[6]);

    const double t6 = double(m[8] * m[13] - m[9] * m[12]);
    const double t7 = double(m[4] * m[13] - m[5] * m[12]);
    const double t8 = double(m[4] * m[9] - m[5] * m[8]);
    const double t9 = double(m[0] * m[13] - m[1] * m[12]);
    const double t10 = double(m[0] * m[9] - m[1] * m[8]);
    const double t11 = double(m[0] * m[5] - m[1] * m[4]);

    double det = t0 * t11 - t1 * t10 + t2 * t9 + t3 * t8 - t4 * t7 + t5 * t6;
    if (isZeroDeterminant(det)) {
        return false;
    }

    // No GLITCH_FAST_MATH: the minors are converted to T and the reciprocal is
    // applied to the finished result below.
    const T ft0 = T(t0);
    const T ft1 = T(t1);
    const T ft2 = T(t2);
    const T ft3 = T(t3);
    const T ft4 = T(t4);
    const T ft5 = T(t5);
    const T ft6 = T(t6);
    const T ft7 = T(t7);
    const T ft8 = T(t8);
    const T ft9 = T(t9);
    const T ft10 = T(t10);
    const T ft11 = T(t11);

    out.m_observedValues[0] = m[5] * ft0 - m[9] * ft1 + m[13] * ft2;
    out.m_observedValues[1] = m[9] * ft3 - m[1] * ft0 - m[13] * ft4;
    out.m_observedValues[2] = m[1] * ft1 - m[5] * ft3 + m[13] * ft5;
    out.m_observedValues[3] = m[5] * ft4 - m[1] * ft2 - m[9] * ft5;

    out.m_observedValues[4] = m[8] * ft1 - m[4] * ft0 - m[12] * ft2;
    out.m_observedValues[5] = m[0] * ft0 - m[8] * ft3 + m[12] * ft4;
    out.m_observedValues[6] = m[4] * ft3 - m[0] * ft1 - m[12] * ft5;
    out.m_observedValues[7] = m[0] * ft2 - m[4] * ft4 + m[8] * ft5;

    out.m_observedValues[8] = m[7] * ft6 - m[11] * ft7 + m[15] * ft8;
    out.m_observedValues[9] = m[11] * ft9 - m[3] * ft6 - m[15] * ft10;
    out.m_observedValues[10] = m[3] * ft7 - m[7] * ft9 + m[15] * ft11;
    out.m_observedValues[11] = m[7] * ft10 - m[3] * ft8 - m[11] * ft11;

    out.m_observedValues[12] = m[10] * ft7 - m[6] * ft6 - m[14] * ft8;
    out.m_observedValues[13] = m[2] * ft6 - m[10] * ft9 + m[14] * ft10;
    out.m_observedValues[14] = m[6] * ft9 - m[2] * ft7 - m[14] * ft11;
    out.m_observedValues[15] = m[2] * ft8 - m[6] * ft10 + m[10] * ft11;

    det = 1.0 / det;
    for (int i = 0; i < 16; ++i) {
        out.m_observedValues[i] = T(out.m_observedValues[i] * det);
    }

    return true;
}

// The reference contains exactly one instantiation, for float, at 0x000ebb08.
template bool CMatrix4<float>::getInverse(CMatrix4<float>& out) const;

}  // namespace core
}  // namespace glitch
