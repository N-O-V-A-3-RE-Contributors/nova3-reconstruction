// Standalone source-only regression for the recovered
//
//   glitch::core::operator*<float, glitch::collada::SMatrix>(
//       CMatrix4<float> const&, SMatrix const&)
//
// (ELF VA 0x006e7e60, 564 B, A32).
//
// The recovered translation unit is included unchanged.  Its only unresolved
// dependency, glitch::core::CMatrix4<float>::CMatrix4(eConstructor), is defined
// here (counting its calls), so the constructor call at the head of the recovered
// function is real and deleting the reference to it breaks the test rather than
// passing silently.
//
// What is asserted, each fact read off the reference instructions:
//   * both operands are 64-byte objects and the result is 64 bytes, so the
//     element addresses used below are the reference's;
//   * the result's fourth column is a literal 0.0f for rows 0..2 and a literal
//     1.0f for row 3: they do NOT depend on the CMatrix4's (3,3) element, which
//     the reference never loads -- asserted by filling that element with junk;
//   * the SMatrix's fourth float in every row is never read -- asserted by
//     filling it with a large sentinel and, separately, with a NaN, and requiring
//     bit-identical results;
//   * the 3x3 part and the translation row reproduce an independently written
//     generic 4x4 product of the SMatrix (with its implicit (0,0,0,1) fourth
//     column) and the CMatrix4 (with its implicit (3,3) = 1);
//   * the result depends only on its arguments -- two calls with different inputs
//     give different outputs, and the constructor runs exactly once per call.
//
// Machine-code agreement with the reference is measured separately by
// tools/compare/collada_matrix_multiply_pilot.py and is NOT claimed here.
#include "../../../recovered/libNOVA3_neon/collada_matrix_multiply.cpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// ------------------------------------------------------- constructor dependency
// The recovered TU declares CMatrix4(eConstructor) and calls it for the result
// object; the body is not recovered, so the test supplies one and counts calls.
unsigned g_constructor_calls = 0;

namespace glitch {
namespace core {

template <typename T>
CMatrix4<T>::CMatrix4(eConstructor) {
    ++g_constructor_calls;
}

}  // namespace core
}  // namespace glitch

namespace {

using glitch::collada::SMatrix;
using glitch::core::CMatrix4;

void require(bool condition, const char* what) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        std::abort();
    }
}

// ------------------------------------------------------------------- utilities
CMatrix4<float> makeMatrix(float const (&values)[16]) {
    CMatrix4<float> m(CMatrix4<float>::kObservedConstructorValue0);
    for (int i = 0; i < 16; ++i) {
        m.m_observedValues[i] = values[i];
    }
    return m;
}

SMatrix makeSMatrix(float const (&values)[16]) {
    SMatrix s;
    for (int i = 0; i < 16; ++i) {
        s.m_observedValues[i] = values[i];
    }
    return s;
}

// Fills every row's fourth float (the lane the reference never reads).
SMatrix withFourthLane(SMatrix s, float lane) {
    for (int row = 0; row < 4; ++row) {
        s.m_observedValues[row * 4 + 3] = lane;
    }
    return s;
}

bool bitsEqual(float a, float b) {
    unsigned lhs = 0;
    unsigned rhs = 0;
    std::memcpy(&lhs, &a, sizeof lhs);
    std::memcpy(&rhs, &b, sizeof rhs);
    return lhs == rhs;
}

bool matrixBitsEqual(CMatrix4<float> const& a, CMatrix4<float> const& b) {
    for (int i = 0; i < 16; ++i) {
        if (!bitsEqual(a.m_observedValues[i], b.m_observedValues[i])) {
            return false;
        }
    }
    return true;
}

// An independent 4x4 product: both operands are expanded to full row-major 4x4
// row-vector affine matrices -- linear part in rows 0..2, translation in row 3,
// and a fourth column of (0,0,0,1) -- and the result is the plain triple-loop
// product.  That fourth column is why A's elements at floats 3/7/11/15 are never
// loaded by the reference.
void expectedProduct(float const (&s)[16], float const (&a)[16], float out[16]) {
    float sFull[4][4];
    float aFull[4][4];
    for (int row = 0; row < 4; ++row) {
        for (int column = 0; column < 4; ++column) {
            float const affineColumn = column == 3 ? (row == 3 ? 1.0f : 0.0f) : 0.0f;
            sFull[row][column] = column == 3 ? affineColumn : s[row * 4 + column];
            aFull[row][column] = column == 3 ? affineColumn : a[row * 4 + column];
        }
    }
    for (int row = 0; row < 4; ++row) {
        for (int column = 0; column < 4; ++column) {
            float sum = 0.0f;
            for (int k = 0; k < 4; ++k) {
                sum += sFull[row][k] * aFull[k][column];
            }
            out[row * 4 + column] = sum;
        }
    }
}

void requireMatchesExpected(CMatrix4<float> const& got, float const (&s)[16],
                            float const (&a)[16], const char* what) {
    float want[16];
    expectedProduct(s, a, want);
    for (int i = 0; i < 16; ++i) {
        if (!(std::fabs(got.m_observedValues[i] - want[i]) <= 1e-5f)) {
            std::fprintf(stderr, "FAIL: %s (element %d: got %g want %g)\n", what,
                         i, static_cast<double>(got.m_observedValues[i]),
                         static_cast<double>(want[i]));
            std::abort();
        }
    }
}

// ------------------------------------------------------------------ fixtures
// CMatrix4: linear part diag(2,3,4) plus translation (5,6,7).
float const kMatrixAffine[16] = {
    2, 0, 0, 5,
    0, 3, 0, 6,
    0, 0, 4, 7,
    5, 6, 7, 1,
};

// SMatrix: rows (0,1,0) / (-1,0,0) / (0,0,1) = 90 degrees about z, translation
// row (10,20,30).
float const kSMatrixRotateZ[16] = {
    0, 1, 0, 0,
    -1, 0, 0, 0,
    0, 0, 1, 0,
    10, 20, 30, 0,
};

float const kMatrixIdentity[16] = {
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
};

float const kSMatrixIdentity[16] = {
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 0,
};

void testSizes() {
    require(sizeof(SMatrix) == 64, "SMatrix must be 64 bytes");
    require(sizeof(CMatrix4<float>) == 64, "CMatrix4<float> must be 64 bytes");
}

void testIdentity() {
    CMatrix4<float> const a = makeMatrix(kMatrixIdentity);
    SMatrix const s = makeSMatrix(kSMatrixIdentity);
    CMatrix4<float> const out = glitch::core::operator*<float, SMatrix>(a, s);
    for (int row = 0; row < 4; ++row) {
        for (int column = 0; column < 4; ++column) {
            float const want = row == column ? 1.0f : 0.0f;
            require(out.m_observedValues[row * 4 + column] == want,
                    "identity * identity must be identity");
        }
    }
}

void testAffineProduct() {
    CMatrix4<float> const a = makeMatrix(kMatrixAffine);
    SMatrix const s = makeSMatrix(kSMatrixRotateZ);
    CMatrix4<float> const out = glitch::core::operator*<float, SMatrix>(a, s);

    // Hand-computed translations: 10*2 + 20*0 + 30*0 + 5, 20*3 + 6, 30*4 + 7.
    require(out.m_observedValues[12] == 25.0f, "translation x is wrong");
    require(out.m_observedValues[13] == 66.0f, "translation y is wrong");
    require(out.m_observedValues[14] == 127.0f, "translation z is wrong");
    require(out.m_observedValues[15] == 1.0f, "result (3,3) must be 1.0f");
    // Row 0 of the SMatrix is (0,1,0): the result's row 0 is A's row 1 linear
    // part, i.e. from the row-major A fixture (0,3,0).
    require(out.m_observedValues[0] == 0.0f, "row 0 column 0 is wrong");
    require(out.m_observedValues[1] == 3.0f, "row 0 column 1 is wrong");
    require(out.m_observedValues[2] == 0.0f, "row 0 column 2 is wrong");
    // Row 1 of the SMatrix is (-1,0,0): negated A row 0 linear part.
    require(out.m_observedValues[4] == -2.0f, "row 1 column 0 is wrong");
    requireMatchesExpected(out, kSMatrixRotateZ, kMatrixAffine,
                           "affine product disagrees with the generic product");
}

void testFourthColumnIsLiteral() {
    float junk[16];
    for (int i = 0; i < 16; ++i) {
        junk[i] = kMatrixAffine[i];
    }
    junk[3] = 1000.0f;   // would be used by a real 4x4 product
    junk[7] = -1000.0f;
    junk[11] = 500.0f;
    junk[15] = 42.0f;    // the element the reference never loads

    CMatrix4<float> const clean = makeMatrix(kMatrixAffine);
    CMatrix4<float> const dirty = makeMatrix(junk);
    SMatrix const s = makeSMatrix(kSMatrixRotateZ);

    CMatrix4<float> const outClean =
        glitch::core::operator*<float, SMatrix>(clean, s);
    CMatrix4<float> const outDirty =
        glitch::core::operator*<float, SMatrix>(dirty, s);
    require(matrixBitsEqual(outClean, outDirty),
            "the CMatrix4 fourth column must not be read");
    require(outDirty.m_observedValues[15] == 1.0f,
            "result (3,3) must be a literal 1.0f, not the input element");
    require(outClean.m_observedValues[3] == 0.0f &&
                outClean.m_observedValues[7] == 0.0f &&
                outClean.m_observedValues[11] == 0.0f,
            "the result's fourth column of rows 0..2 must be literal zeros");
}

void testSMatrixFourthLaneIsNeverRead() {
    SMatrix const s = makeSMatrix(kSMatrixRotateZ);
    SMatrix const huge = withFourthLane(s, 1.0e30f);
    SMatrix const nan = withFourthLane(s, std::nanf(""));
    CMatrix4<float> const a = makeMatrix(kMatrixAffine);

    CMatrix4<float> const base = glitch::core::operator*<float, SMatrix>(a, s);
    CMatrix4<float> const withHuge =
        glitch::core::operator*<float, SMatrix>(a, huge);
    CMatrix4<float> const withNan =
        glitch::core::operator*<float, SMatrix>(a, nan);
    require(matrixBitsEqual(base, withHuge),
            "the SMatrix's fourth float per row must not be read (finite)");
    require(matrixBitsEqual(base, withNan),
            "the SMatrix's fourth float per row must not be read (NaN)");
}

void testNoHiddenState() {
    CMatrix4<float> const a = makeMatrix(kMatrixAffine);
    unsigned const before = g_constructor_calls;
    CMatrix4<float> const first =
        glitch::core::operator*<float, SMatrix>(a, makeSMatrix(kSMatrixRotateZ));
    require(g_constructor_calls == before + 1,
            "the result constructor must run exactly once per call");
    CMatrix4<float> const second =
        glitch::core::operator*<float, SMatrix>(a, makeSMatrix(kSMatrixIdentity));
    require(!matrixBitsEqual(first, second),
            "two different inputs must not produce the same result");
    CMatrix4<float> const third =
        glitch::core::operator*<float, SMatrix>(a, makeSMatrix(kSMatrixRotateZ));
    require(matrixBitsEqual(first, third), "the operator must be pure");
}

}  // namespace

int main() {
    testSizes();
    testIdentity();
    testAffineProduct();
    testFourthColumnIsLiteral();
    testSMatrixFourthLaneIsNeverRead();
    testNoHiddenState();
    std::printf(
        "collada_matrix_multiply: ok (identity, affine product vs generic 4x4, "
        "literal fourth column, unread SMatrix fourth lane, purity)\n");
    return 0;
}
