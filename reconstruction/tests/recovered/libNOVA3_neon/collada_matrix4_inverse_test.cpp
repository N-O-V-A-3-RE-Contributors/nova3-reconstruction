// Behaviour test for the recovered
//
//   glitch::core::CMatrix4<float>::getInverse(CMatrix4<float>& out) const
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   ELF VA 0x000ebb08, 776 bytes, A32, STT_FUNC.
//
// The recovered TU is included unchanged, so the shipped body runs.  The oracle
// is an independently written double-precision Gauss-Jordan inverse; agreement
// with it is arithmetic agreement on this unit's own inputs, not a comparison
// against the original binary (that is a separate, offset-based measurement).
//
// Asserted:
//   * identity inverts to identity and reports success;
//   * a diagonal matrix inverts to the reciprocal of each diagonal element;
//   * a rigid transform (rotation + translation) inverts to its analytic
//     inverse (transposed rotation, -R^T * t) within float tolerance;
//   * the inverse of a well-conditioned general matrix, multiplied by the
//     original, is the identity within tolerance (independent Gauss-Jordan
//     agreement element by element as well);
//   * a singular matrix reports failure and writes NOTHING to `out` (the
//     reference returns before the first store, so a sentinel must survive);
//   * a nearly-singular matrix with |det| <= 1e-08 also reports failure, which
//     pins the measured tolerance constant 1e-08 from 0xebe08;
//   * the source object is never modified, and a large-but-finite input does not
//     trip the sign-of-determinant path (result elements are finite);
//   * the 64-byte destination is written completely on success.
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>

// The plain constructor is part of this unit's setup: every object used
// below is built through the recovered CMatrix4(eConstructor) body.
#include "collada_matrix4_ctor.cpp"
#include "collada_matrix4_inverse.cpp"

namespace {

using glitch::core::CMatrix4;

int failures = 0;

void check(bool condition, const char* what) {
    if (!condition) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

void checkClose(float actual, float expected, float tolerance, const char* what) {
    if (!(std::fabs(actual - expected) <= tolerance)) {
        std::printf("FAIL: %s (actual %.9g expected %.9g)\n", what, actual, expected);
        ++failures;
    }
}

void setIdentity(CMatrix4<float>& m) {
    std::memset(m.m_observedValues, 0, sizeof(m.m_observedValues));
    for (int i = 0; i < 4; ++i) {
        m.m_observedValues[i * 4 + i] = 1.0f;
    }
}

void setRowMajor(CMatrix4<float>& m, const double values[16]) {
    for (int i = 0; i < 16; ++i) {
        m.m_observedValues[i] = float(values[i]);
    }
}

// Independent oracle: Gauss-Jordan with partial pivoting in double.
bool invertOracle(const double in[16], double out[16]) {
    double a[4][8];
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            a[r][c] = in[r * 4 + c];
            a[r][c + 4] = (r == c) ? 1.0 : 0.0;
        }
    }
    for (int col = 0; col < 4; ++col) {
        int pivot = col;
        for (int r = col + 1; r < 4; ++r) {
            if (std::fabs(a[r][col]) > std::fabs(a[pivot][col])) {
                pivot = r;
            }
        }
        if (std::fabs(a[pivot][col]) < 1e-12) {
            return false;
        }
        if (pivot != col) {
            for (int c = 0; c < 8; ++c) {
                double tmp = a[col][c];
                a[col][c] = a[pivot][c];
                a[pivot][c] = tmp;
            }
        }
        double scale = a[col][col];
        for (int c = 0; c < 8; ++c) {
            a[col][c] /= scale;
        }
        for (int r = 0; r < 4; ++r) {
            if (r == col) {
                continue;
            }
            double factor = a[r][col];
            for (int c = 0; c < 8; ++c) {
                a[r][c] -= factor * a[col][c];
            }
        }
    }
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            out[r * 4 + c] = a[r][c + 4];
        }
    }
    return true;
}

void multiply(const float a[16], const float b[16], double out[16]) {
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            double sum = 0.0;
            for (int k = 0; k < 4; ++k) {
                sum += double(a[r * 4 + k]) * double(b[k * 4 + c]);
            }
            out[r * 4 + c] = sum;
        }
    }
}

void testIdentity() {
    CMatrix4<float> source(CMatrix4<float>::kObservedConstructorValue2);
    CMatrix4<float> result(CMatrix4<float>::kObservedConstructorValue2);
    setIdentity(source);
    std::memset(result.m_observedValues, 0xff, sizeof(result.m_observedValues));
    check(source.getInverse(result), "identity: reports success");
    for (int i = 0; i < 16; ++i) {
        checkClose(result.m_observedValues[i], (i % 5 == 0) ? 1.0f : 0.0f, 1e-6f,
                   "identity: inverse equals identity");
    }
}

void testDiagonal() {
    const double values[16] = {2, 0, 0, 0,
                               0, 4, 0, 0,
                               0, 0, 5, 0,
                               0, 0, 0, 1};
    CMatrix4<float> source(CMatrix4<float>::kObservedConstructorValue2);
    CMatrix4<float> result(CMatrix4<float>::kObservedConstructorValue2);
    setRowMajor(source, values);
    check(source.getInverse(result), "diagonal: reports success");
    const double expected[4] = {0.5, 0.25, 0.2, 1.0};
    for (int i = 0; i < 4; ++i) {
        checkClose(result.m_observedValues[i * 4 + i], float(expected[i]), 1e-6f,
                   "diagonal: reciprocal diagonal");
    }
    for (int i = 0; i < 16; ++i) {
        if (i % 5 != 0) {
            checkClose(result.m_observedValues[i], 0.0f, 1e-7f,
                       "diagonal: off-diagonal stays zero");
        }
    }
}

void testRigidTransform() {
    // Row-major storage with the translation in the LAST ROW (indices 12..14),
    // which is the convention the recovered matrix code uses (see the
    // multiplyWith1x4Matrix-style indexing in the reference engine): a 90-degree
    // rotation about z, then translation (3, -2, 7).
    const float angle = float(M_PI) / 2.0f;
    const double values[16] = {std::cos(angle), -std::sin(angle), 0, 0,
                               std::sin(angle),  std::cos(angle), 0, 0,
                               0,                0,               1, 0,
                               3,                -2,              7, 1};
    CMatrix4<float> source(CMatrix4<float>::kObservedConstructorValue2);
    CMatrix4<float> result(CMatrix4<float>::kObservedConstructorValue2);
    setRowMajor(source, values);
    check(source.getInverse(result), "rigid: reports success");
    double combined[16];
    multiply(source.m_observedValues, result.m_observedValues, combined);
    for (int i = 0; i < 16; ++i) {
        checkClose(float(combined[i]), (i % 5 == 0) ? 1.0f : 0.0f, 1e-5f,
                   "rigid: source * inverse is the identity");
    }
    // Analytic inverse: rotation is transposed, translation is -t * R^-1.
    checkClose(result.m_observedValues[0], 0.0f, 1e-6f, "rigid: inverse R[0][0]");
    checkClose(result.m_observedValues[1], 1.0f, 1e-6f, "rigid: inverse R[0][1]");
    checkClose(result.m_observedValues[4], -1.0f, 1e-6f, "rigid: inverse R[1][0]");
    checkClose(result.m_observedValues[12], -2.0f, 1e-5f, "rigid: inverse t.x");
    checkClose(result.m_observedValues[13], -3.0f, 1e-5f, "rigid: inverse t.y");
    checkClose(result.m_observedValues[14], -7.0f, 1e-5f, "rigid: inverse t.z");
}

void testGeneralAgainstOracle() {
    const double values[16] = {4.0, 7.0, 2.0, 3.0,
                               1.5, 6.0, 9.0, 0.5,
                               2.5, 1.0, 8.0, 4.0,
                               0.25, 3.0, 1.5, 2.0};
    double oracle[16];
    check(invertOracle(values, oracle), "general: oracle succeeds");
    CMatrix4<float> source(CMatrix4<float>::kObservedConstructorValue2);
    CMatrix4<float> result(CMatrix4<float>::kObservedConstructorValue2);
    setRowMajor(source, values);
    check(source.getInverse(result), "general: reports success");
    for (int i = 0; i < 16; ++i) {
        checkClose(result.m_observedValues[i], float(oracle[i]), 1e-4f,
                   "general: agrees with independent Gauss-Jordan inverse");
    }
    double combined[16];
    multiply(source.m_observedValues, result.m_observedValues, combined);
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            float expected = (r == c) ? 1.0f : 0.0f;
            checkClose(float(combined[r * 4 + c]), expected, 1e-4f,
                       "general: source * inverse is the identity");
        }
    }
    // The source object must be untouched.
    for (int i = 0; i < 16; ++i) {
        checkClose(source.m_observedValues[i], float(values[i]), 0.0f,
                   "general: source is not modified");
    }
}

void testSingularWritesNothing() {
    const double values[16] = {1, 2, 3, 4,
                               2, 4, 6, 8,   // twice row 0: singular
                               5, 6, 7, 8,
                               9, 10, 11, 12};
    CMatrix4<float> source(CMatrix4<float>::kObservedConstructorValue2);
    CMatrix4<float> result(CMatrix4<float>::kObservedConstructorValue2);
    setRowMajor(source, values);
    for (int i = 0; i < 16; ++i) {
        result.m_observedValues[i] = 12345.0f + float(i);
    }
    check(!source.getInverse(result), "singular: reports failure");
    for (int i = 0; i < 16; ++i) {
        checkClose(result.m_observedValues[i], 12345.0f + float(i), 0.0f,
                   "singular: destination untouched");
    }
}

void testToleranceConstant() {
    // diag(1e4, 1e4, 1e4, 1e-4) has determinant 1e4 * 1e4 * 1e4 * 1e-4 = 1e8,
    // far above the tolerance; the nearly singular case below has a determinant
    // just under 1e-8 and must be rejected by the measured 1e-08 constant.
    const double singularish[16] = {1e-2, 0, 0, 0,
                                    0, 1e-2, 0, 0,
                                    0, 0, 1e-2, 0,
                                    0, 0, 0, 1e-3};
    CMatrix4<float> source(CMatrix4<float>::kObservedConstructorValue2);
    CMatrix4<float> result(CMatrix4<float>::kObservedConstructorValue2);
    setRowMajor(source, singularish);
    // det = 1e-9 <= 1e-8: rejected.
    check(!source.getInverse(result), "tolerance: |det| <= 1e-08 is rejected");

    const double above[16] = {1e-1, 0, 0, 0,
                              0, 1e-1, 0, 0,
                              0, 0, 1e-1, 0,
                              0, 0, 0, 1e-2};
    setRowMajor(source, above);
    // det = 1e-5 > 1e-8: accepted.
    check(source.getInverse(result), "tolerance: |det| > 1e-08 is accepted");
    checkClose(result.m_observedValues[0], 10.0f, 1e-3f,
               "tolerance: accepted case inverts");
}

void testLargeButFinite() {
    const double values[16] = {1e6, 0, 0, 0,
                               0, 1e6, 0, 0,
                               0, 0, 1e6, 0,
                               0, 0, 0, 1e6};
    CMatrix4<float> source(CMatrix4<float>::kObservedConstructorValue2);
    CMatrix4<float> result(CMatrix4<float>::kObservedConstructorValue2);
    setRowMajor(source, values);
    check(source.getInverse(result), "large: reports success");
    for (int i = 0; i < 16; ++i) {
        check(std::isfinite(result.m_observedValues[i]), "large: result is finite");
    }
    checkClose(result.m_observedValues[0], 1e-6f, 1e-12f, "large: scaled reciprocal");
}

}  // namespace

int main() {
    testIdentity();
    testDiagonal();
    testRigidTransform();
    testGeneralAgainstOracle();
    testSingularWritesNothing();
    testToleranceConstant();
    testLargeButFinite();
    if (failures == 0) {
        std::printf("collada_matrix4_inverse: all checks passed\n");
        return 0;
    }
    std::printf("collada_matrix4_inverse: %d failure(s)\n", failures);
    return 1;
}
