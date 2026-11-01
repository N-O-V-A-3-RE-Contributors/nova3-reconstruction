// Source-owned recovery of
//
//   glitch::core::operator*<float, glitch::collada::SMatrix>(
//       glitch::core::CMatrix4<float> const&, glitch::collada::SMatrix const&)
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   symbol   _ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_
//   ELF VA   0x006e7e60, 564 bytes, A32, STT_FUNC, exact `.symtab` bounds.
//
// ABI, read off the prologue: the result is a 64-byte object returned through
// the sret pointer, r0 is copied to r6 and never read again, r1 (the
// CMatrix4<float>) goes to r4 and r2 (the SMatrix) goes to r5.  The first call is
// `CMatrix4<float>::CMatrix4(eConstructor)` with this = the sret pointer and
// r1 = 0; the constructor's 16 floats are then all overwritten.
//
// ELEMENT MAPPING, taken instruction by instruction.  Stores go to the result in
// strictly row-major order, 0x00..0x3c, so `result(row, column)` is at
// float offset row*4+column:
//
//   result(0,0) = S(0,0)*A(0,0) + S(0,1)*A(1,0) + S(0,2)*A(2,0)
//   result(0,1) = S(0,0)*A(0,1) + S(0,1)*A(1,1) + S(0,2)*A(2,1)
//   result(0,2) = S(0,0)*A(0,2) + S(0,1)*A(1,2) + S(0,2)*A(2,2)
//   result(0,3) = 0.0f                     (literal, loaded once as s14 = 0.0)
//   ... rows 1 and 2 identical in shape with S(1,*) / S(2,*)
//   result(3,0) = S(3,0)*A(0,0) + S(3,1)*A(1,0) + S(3,2)*A(2,0) + A(3,0)
//   result(3,1) = ... + A(3,1)
//   result(3,2) = ... + A(3,2)
//   result(3,3) = 1.0f                     (vmov.f32 s15,#1.0, then vstr)
//
// where S(row,column) is the SMatrix element at float offset row*4+column
// (columns 0..2 only; the fourth float of every row is never loaded) and
// A(row,column) is the CMatrix4 element at float offset row*4+column (row 3 is
// the translation at 0x30/0x34/0x38; A(3,3) at 0x3c is never loaded).
//
// Because the SMatrix's fourth component is not loaded at all, because the
// CMatrix4's floats 3/7/11 are not loaded either, and because the result's fourth
// column is built from literals, the source below models both operands as
// row-vector affine matrices: rows 0..2 linear, row 3 the translation, implicit
// fourth column (0,0,0,1).  That reading is INFERRED; the arithmetic itself is
// the observed one, including the three folded constants -- S(3,3)=1 (float 15
// never loaded), A(0..2,3)=0 (floats 3/7/11 never loaded) and A(3,3)=1 (float 15
// never loaded).
//
// NOT recovered: the full SMatrix/CMatrix4 member sets, the enumerator names for
// the constructor values, and whether the original operator* was an inline
// template in a header whose only instantiation happened to live in this
// translation unit.  The function is IMPLEMENTED + TESTED; machine-code agreement
// is measured separately by tools/compare/collada_matrix_multiply_pilot.py and
// is not claimed here.
#include "collada_matrix_multiply.h"

namespace glitch {
namespace core {

template <typename T, typename M>
CMatrix4<T> operator*(CMatrix4<T> const& lhs, M const& rhs) {
    // The reference constructs the result object through the out-of-line
    // CMatrix4(eConstructor) constructor with argument value 0 and then writes
    // all 16 floats, so the call is kept for call-graph fidelity; its effect is
    // entirely overwritten.
    CMatrix4<T> result(CMatrix4<T>::kObservedConstructorValue0);

    // The reference addresses all three objects as flat float arrays with the
    // 0x40-byte stride and re-loads every operand for every result element (72
    // vldr in the window, three per term, with no value kept live across two
    // result elements).  Keeping the accesses as array reads through these
    // pointers is what lets the compiler see that a store through `out` may alias
    // `a` or `b`; a form that lets it cache the operands in VFP registers emits
    // roughly two dozen fewer loads and no longer has the reference's shape.
    float* out = result.m_observedValues;
    float const* a = lhs.m_observedValues;
    float const* b = rhs.m_observedValues;

    for (int row = 0; row < 3; ++row) {
        for (int column = 0; column < 3; ++column) {
            out[row * 4 + column] =
                b[row * 4 + 0] * a[0 * 4 + column] +
                b[row * 4 + 1] * a[1 * 4 + column] +
                b[row * 4 + 2] * a[2 * 4 + column];
        }
        // Fourth column of the linear rows: a literal zero in the reference.  It
        // is not computed from a[3], a[7], a[11], which are never loaded.
        out[row * 4 + 3] = 0.0f;
    }

    for (int column = 0; column < 3; ++column) {
        // Translation row: the SMatrix's translation row against the CMatrix4's
        // linear part, plus the CMatrix4's own translation.
        out[3 * 4 + column] =
            b[3 * 4 + 0] * a[0 * 4 + column] +
            b[3 * 4 + 1] * a[1 * 4 + column] +
            b[3 * 4 + 2] * a[2 * 4 + column] +
            a[3 * 4 + column];
    }

    // Literal 1.0f in the reference; the CMatrix4's own (3,3) element at 0x3c is
    // never loaded, so the result does not depend on it.
    out[15] = 1.0f;
    return result;
}

template CMatrix4<float> operator*<float, collada::SMatrix>(
    CMatrix4<float> const&, collada::SMatrix const&);

}  // namespace core
}  // namespace glitch
