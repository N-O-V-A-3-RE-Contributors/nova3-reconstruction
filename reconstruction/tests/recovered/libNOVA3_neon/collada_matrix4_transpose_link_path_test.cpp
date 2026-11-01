// Link-path test: the recovered CMatrix4<float>::getTransposed (0x000eba84) and
// the recovered CMatrix4<float> constructors, compiled together.
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// The sibling test collada_matrix4_copy_ctor_test.cpp substitutes an
// instrumented getTransposed so it can observe which object is transposed.  That
// leaves the constructor's value-3 and value-5 paths exercised against a stand-in
// rather than the recovered arithmetic, so this test links the three recovered
// TUs unchanged and stubs only the one dependency that is still NOT
// RECONSTRUCTED (getInverse, 0x000ebb08, 776 B).
//
// Asserted:
//   * value 3 stores the real recovered transpose of the source, slot by slot;
//   * value 5 stores the real recovered transpose of the inverse, taken of the
//     object that received it (not of the source);
//   * value 4 still keeps the inverse and zeroes the object on failure, i.e.
//     recovering getTransposed changed nothing about the inverse path;
//   * value 1 and value 2 are unaffected;
//   * the source object is never modified by any path.
#include <cstdio>
#include <cstring>
#include <new>

#include "collada_matrix4_ctor.cpp"
#include "collada_matrix4_copy_ctor.cpp"
#include "collada_matrix4_transpose.cpp"

namespace {

using glitch::core::CMatrix4;
using Mode = CMatrix4<float>::eConstructor;

int failures = 0;

void check(bool condition, const char* what) {
    if (!condition) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

int inverse_calls = 0;
bool inverse_succeeds = true;
float const kInverseBase = 500.0f;

}  // namespace

namespace glitch {
namespace core {

// The only remaining stand-in.  getInverse is 776 bytes and NOT RECONSTRUCTED;
// its result here is a recognisable ramp so a transpose of the wrong object is
// visible.
template <>
bool CMatrix4<float>::getInverse(CMatrix4<float>& out) const {
    ++inverse_calls;
    if (!inverse_succeeds) {
        return false;
    }
    for (int i = 0; i < 16; ++i) {
        out.m_observedValues[i] = kInverseBase + static_cast<float>(i);
    }
    return true;
}

}  // namespace core
}  // namespace glitch

namespace {

struct Raw {
    alignas(4) unsigned char storage[64];
    CMatrix4<float>& matrix() {
        return *reinterpret_cast<CMatrix4<float>*>(storage);
    }
    CMatrix4<float> const& matrix() const {
        return *reinterpret_cast<CMatrix4<float> const*>(storage);
    }
};

void reset() {
    inverse_calls = 0;
    inverse_succeeds = true;
}

void source_fixture(Raw& r) {
    // Distinct per slot, non-symmetric, exact in binary32.
    for (int i = 0; i < 16; ++i) {
        r.matrix().m_observedValues[i] = 10.0f + static_cast<float>(i * 3);
    }
}

// Independently written transpose through a 2-D staging array.
void stage_transpose(float const* source, float* destination) {
    float staged[4][4];
    for (int a = 0; a < 4; ++a) {
        for (int b = 0; b < 4; ++b) staged[a][b] = source[a * 4 + b];
    }
    for (int a = 0; a < 4; ++a) {
        for (int b = 0; b < 4; ++b) destination[a * 4 + b] = staged[b][a];
    }
}

void construct(Raw& destination, CMatrix4<float> const& source, int mode) {
    std::memset(destination.storage, 0xEE, sizeof destination.storage);
    new (destination.storage) CMatrix4<float>(source, static_cast<Mode>(mode));
}

void test_value_3_uses_the_recovered_transpose() {
    reset();
    Raw source;
    source_fixture(source);
    unsigned char before[64];
    std::memcpy(before, source.storage, sizeof before);

    Raw destination;
    construct(destination, source.matrix(), 3);

    float expected[16] = {0};
    stage_transpose(source.matrix().m_observedValues, expected);
    check(std::memcmp(expected, destination.matrix().m_observedValues,
                      sizeof expected) == 0,
          "value 3 is not the recovered transpose of the source");
    check(inverse_calls == 0, "value 3 called getInverse");
    check(std::memcmp(before, source.storage, sizeof before) == 0,
          "value 3 modified the source");
}

void test_value_5_uses_the_recovered_transpose_on_the_inverse() {
    reset();
    Raw source;
    source_fixture(source);

    float inverse[16];
    for (int i = 0; i < 16; ++i) inverse[i] = kInverseBase + static_cast<float>(i);

    Raw destination;
    construct(destination, source.matrix(), 5);

    float expected[16] = {0};
    stage_transpose(inverse, expected);
    check(std::memcmp(expected, destination.matrix().m_observedValues,
                      sizeof expected) == 0,
          "value 5 is not the recovered transpose of the inverse");
    check(inverse_calls == 1, "value 5 did not call getInverse exactly once");
}

void test_value_4_is_unaffected() {
    reset();
    Raw source;
    source_fixture(source);
    Raw destination;
    construct(destination, source.matrix(), 4);

    for (int i = 0; i < 16; ++i) {
        if (destination.matrix().m_observedValues[i] !=
            kInverseBase + static_cast<float>(i)) {
            check(false, "value 4 did not keep the inverse result");
            break;
        }
    }
    check(inverse_calls == 1, "value 4 did not call getInverse exactly once");

    reset();
    inverse_succeeds = false;
    Raw failed;
    construct(failed, source.matrix(), 4);
    for (int i = 0; i < 16; ++i) {
        if (failed.storage[i] != 0x00) {
            check(false, "a failed inverse did not leave the all-zero matrix");
            break;
        }
    }
}

void test_copies_and_identity_are_unaffected() {
    reset();
    Raw source;
    source_fixture(source);

    Raw copy;
    construct(copy, source.matrix(), 1);
    check(std::memcmp(copy.storage, source.storage, sizeof copy.storage) == 0,
          "value 1 is no longer a copy of the source");

    Raw identity;
    construct(identity, source.matrix(), 2);
    for (int i = 0; i < 16; ++i) {
        float const expected = (i % 5 == 0) ? 1.0f : 0.0f;
        if (identity.matrix().m_observedValues[i] != expected) {
            check(false, "value 2 is no longer the identity");
            break;
        }
    }
    check(inverse_calls == 0, "value 1 or 2 called getInverse");
}

}  // namespace

int main() {
    test_value_3_uses_the_recovered_transpose();
    test_value_5_uses_the_recovered_transpose_on_the_inverse();
    test_value_4_is_unaffected();
    test_copies_and_identity_are_unaffected();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("collada_matrix4_transpose_link_path: all checks passed\n");
    return 0;
}
