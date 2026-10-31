// Layout and interface for the recovered
//
//   glitch::core::CMatrix4<float>
//   glitch::core::operator*<float, glitch::collada::SMatrix>(
//       CMatrix4<float> const&, SMatrix const&)
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   _ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4IT_EERKS6_RKT0_
//   ELF VA 0x006e7e60, 564 bytes, A32, STT_FUNC.
//
// WHAT IS OBSERVED (read off the 564-byte window, see
// collada_matrix_multiply.cpp for the instruction-by-instruction mapping):
//
//   * both operands are addressed with a 0x40-byte element stride and the result
//     is a 64-byte object returned through the sret pointer in r0;
//   * the first argument (r1) is loaded at byte offsets
//       0x00,0x10,0x20, 0x04,0x14,0x24, 0x08,0x18,0x28   (a 3x3 block, floats
//                                                         0,4,8 / 1,5,9 / 2,6,10)
//     and 0x30,0x34,0x38 (floats 12,13,14);
//     its floats 3,7,11 and 15 (byte 0x0c/0x1c/0x2c/0x3c) are never touched.
//     So CMatrix4<float> is 16 floats laid out row-major in memory: rows of four
//     floats, rows 0..2 the linear part, row 3 the translation, and the fourth
//     float of row 3 is never read;
//   * the second argument (r2, the SMatrix) is loaded at byte offsets
//       0x00,0x04,0x08,  0x10,0x14,0x18,  0x20,0x24,0x28,  0x30,0x34,0x38
//     i.e. floats 0,1,2 / 4,5,6 / 8,9,10 / 12,13,14: four rows of four floats of
//     which only the first three floats of each row are ever loaded.  Every
//     0x0c/0x1c/0x2c/0x3c word is skipped.
//   * all sixteen result slots (byte 0x00..0x3c) are written, so
//     result(row, column) is at byte offset (row*4+column)*4; the three constant
//     stores of the fourth column land one slot early in program order, which is
//     scheduling, not a different layout.
//
// WHAT IS INFERRED (marked, not recovered):
//
//   * that the skipped fourth float of each SMatrix row is padding or an unused
//     `w` component.  The engine never reads it, so its meaning cannot be
//     established from this function;
//   * the algebra: with CMatrix4 rows 0..2 as a linear part and a translation in
//     positions 12,13,14, and with the SMatrix's three components per row, the
//     emitted arithmetic is a 4x4 product in which the SMatrix's implicit fourth
//     column is (0,0,0,1) and the CMatrix4's implicit (3,3) element is 1.  Those
//     are why the result's fourth column is written as a literal 0.0f / 1.0f
//     instead of being computed;
//   * the enumerator NAME for the constructor argument value 0 (only the value is
//     observed).
#ifndef NOVA3_RECOVERED_COLLADA_MATRIX_MULTIPLY_H
#define NOVA3_RECOVERED_COLLADA_MATRIX_MULTIPLY_H

#include "collada_skin_cache_deps.h"

namespace glitch {
namespace collada {

// 64 bytes, four rows of four floats, element (row, column) at row*4+column.
// Only columns 0..2 of each row are ever read by the recovered operator*, and
// the operator's own output writes the fourth column from constants, so nothing
// below assumes a value for the skipped float.  The remaining members, the
// constructors and the meaning of the type are NOT reconstructed.
struct SMatrix {
    float m_observedValues[16];
};

}  // namespace collada

namespace core {

// The reference symbol is a free function template (the mangling is
// `mlIfNS_7collada7SMatrixEE` = operator*<float, SMatrix>, returning
// CMatrix4<float> through sret).  The body is in collada_matrix_multiply.cpp and
// is instantiated explicitly there for <float, collada::SMatrix>.
template <typename T, typename M>
CMatrix4<T> operator*(CMatrix4<T> const& lhs, M const& rhs);

// The one instantiation the reference library contains.  Declared here so that
// call sites name it and the definition in the .cpp is not needed at compile
// time.
extern template CMatrix4<float> operator*<float, collada::SMatrix>(
    CMatrix4<float> const&, collada::SMatrix const&);

}  // namespace core
}  // namespace glitch

#endif  // NOVA3_RECOVERED_COLLADA_MATRIX_MULTIPLY_H
