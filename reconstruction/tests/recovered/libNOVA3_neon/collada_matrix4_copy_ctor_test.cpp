// Behaviour test for the recovered
//
//   glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float> const&,
//                                           CMatrix4<float>::eConstructor)
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   ELF VA 0x00108c8c, 240 bytes, A32, STT_FUNC.
//
// Both recovered translation units are included here unchanged -- the
// copy-with-mode constructor under test and the plain CMatrix4(eConstructor)
// that the value-5 path uses to introduce its 64-byte stack local -- so the
// shipped bodies run.  The only definitions supplied here are the two
// dependencies the reference keeps out of line:
//
//   getTransposed(CMatrix4&) const   (0x000eba84, 132 B, RECOVERED in
//                                     collada_matrix4_transpose.cpp)
//   getInverse(CMatrix4&) const      (0x000ebb08, 776 B, NOT RECONSTRUCTED)
//
// getTransposed is deliberately still substituted here: this test asserts WHICH
// object is transposed, which needs an instrumented body.  The recovered body is
// exercised by the sibling tests collada_matrix4_transpose_behaviour (its own
// slot mapping) and collada_matrix4_transpose_link_path (the recovered
// constructor and the recovered transpose linked together, with only getInverse
// stubbed).
//
// The stand-ins record how often and with which `this`/destination each was
// called and write a recognisable 64-byte pattern, which is what lets the
// assertions below distinguish the five enumerator paths.  Instruction agreement
// is a separate measurement (tools/compare/collada_matrix4_copy_ctor_pilot.py);
// this file claims behaviour only.
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <new>

#include "collada_matrix4_ctor.cpp"
#include "collada_matrix4_copy_ctor.cpp"

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

// --- instrumented dependencies -------------------------------------------

struct Calls {
    int transposed = 0;
    int inverse = 0;
    void const* last_transposed_this = nullptr;
    void const* last_transposed_dest = nullptr;
    void const* last_inverse_this = nullptr;
    void const* last_inverse_dest = nullptr;
    float const* last_inverse_dest_values = nullptr;
};

Calls calls;
bool inverse_succeeds = true;

// Recognisable, non-degenerate patterns so a Copy of the wrong object is
// visible rather than coincidentally equal.
const float kTransposeBase = 100.0f;
const float kInverseBase = 200.0f;

}  // namespace

namespace glitch {
namespace core {

template <>
void CMatrix4<float>::getTransposed(CMatrix4<float>& out) const {
    ++calls.transposed;
    calls.last_transposed_this = this;
    calls.last_transposed_dest = &out;
    for (int row = 0; row < 4; ++row) {
        for (int column = 0; column < 4; ++column) {
            // Written so that the result is not symmetric: the transpose of the
            // *source* and the transpose of the *destination* differ.
            out.m_observedValues[row * 4 + column] =
                kTransposeBase + static_cast<float>(row * 4 + column) +
                m_observedValues[column * 4 + row];
        }
    }
}

template <>
bool CMatrix4<float>::getInverse(CMatrix4<float>& out) const {
    ++calls.inverse;
    calls.last_inverse_this = this;
    calls.last_inverse_dest = &out;
    if (!inverse_succeeds) {
        return false;
    }
    for (int i = 0; i < 16; ++i) {
        out.m_observedValues[i] = kInverseBase + static_cast<float>(i);
    }
    calls.last_inverse_dest_values = out.m_observedValues;
    return true;
}

}  // namespace core
}  // namespace glitch

namespace {

// 64-byte matrix in raw storage with guard bytes, so an out-of-bounds store is
// visible and no default constructor is needed.
struct Guarded {
    alignas(4) unsigned char before[16];
    alignas(4) unsigned char storage[64];
    alignas(4) unsigned char after[16];

    CMatrix4<float>& matrix() {
        return *reinterpret_cast<CMatrix4<float>*>(storage);
    }
    CMatrix4<float> const& matrix() const {
        return *reinterpret_cast<CMatrix4<float> const*>(storage);
    }
};

void fill(unsigned char* p, std::size_t n, unsigned char v) {
    for (std::size_t i = 0; i < n; ++i) p[i] = v;
}

void reset_calls() {
    calls = Calls{};
    inverse_succeeds = true;
}

bool all_bytes(CMatrix4<float> const& m, unsigned char v) {
    unsigned char const* raw = reinterpret_cast<unsigned char const*>(&m);
    for (std::size_t i = 0; i < sizeof m; ++i) {
        if (raw[i] != v) return false;
    }
    return true;
}

bool same_floats(CMatrix4<float> const& a, CMatrix4<float> const& b) {
    return std::memcmp(&a, &b, sizeof a) == 0;
}

bool is_identity(CMatrix4<float> const& m) {
    for (int i = 0; i < 16; ++i) {
        float const expected = (i % 5 == 0) ? 1.0f : 0.0f;
        if (m.m_observedValues[i] != expected) return false;
    }
    return true;
}

// The recovered CMatrix4 has no default constructor, so fixtures are built
// with the recovered plain constructor and then filled.
CMatrix4<float> make_matrix() {
    return CMatrix4<float>(CMatrix4<float>::kObservedConstructorValue2);
}

void source_fixture(CMatrix4<float>& m) {
    for (int i = 0; i < 16; ++i) {
        m.m_observedValues[i] = static_cast<float>(i) * 1.5f - 3.0f;
    }
}

void expect_transpose_of(CMatrix4<float> const& from, float base,
                         CMatrix4<float> const& got) {
    for (int row = 0; row < 4; ++row) {
        for (int column = 0; column < 4; ++column) {
            float const expected =
                base + static_cast<float>(row * 4 + column) +
                from.m_observedValues[column * 4 + row];
            if (got.m_observedValues[row * 4 + column] != expected) {
                std::printf("FAIL: transpose result differs at (%d,%d)\n",
                            row, column);
                ++failures;
                return;
            }
        }
    }
}

// Construct `mode` from `source` on top of whatever the storage already holds.
void construct_over(Guarded& g, CMatrix4<float> const& source, int mode) {
    fill(g.before, sizeof g.before, 0x5A);
    fill(g.after, sizeof g.after, 0x5A);
    new (g.storage) CMatrix4<float>(source, static_cast<Mode>(mode));
}

// Construct `mode` from `source` into freshly junked guarded storage.
void construct(Guarded& g, CMatrix4<float> const& source, int mode) {
    fill(g.before, sizeof g.before, 0x5A);
    fill(g.after, sizeof g.after, 0x5A);
    fill(g.storage, sizeof g.storage, 0xA5);
    new (g.storage) CMatrix4<float>(
        source, static_cast<Mode>(mode));
}

void check_guards(Guarded const& g, const char* what) {
    for (std::size_t i = 0; i < sizeof g.before; ++i) {
        if (g.before[i] != 0x5A || g.after[i] != 0x5A) {
            check(false, what);
            return;
        }
    }
}

// --- value 1: copy --------------------------------------------------------

void test_value_1_copies_the_source() {
    reset_calls();
    CMatrix4<float> source = make_matrix();
    source_fixture(source);
    CMatrix4<float> source_copy = source;

    Guarded g;
    construct(g, source, 1);

    check(same_floats(g.matrix(), source_copy), "value 1 did not copy the source");
    check(same_floats(source, source_copy), "value 1 modified its source");
    check(calls.transposed == 0 && calls.inverse == 0,
          "value 1 called an unrecovered dependency");
    check_guards(g, "value 1 wrote outside the object");
}

// --- value 2: identity ----------------------------------------------------

void test_value_2_builds_the_identity() {
    reset_calls();
    CMatrix4<float> source = make_matrix();
    source_fixture(source);

    Guarded g;
    construct(g, source, 2);

    check(is_identity(g.matrix()), "value 2 did not build the identity");
    check(calls.transposed == 0 && calls.inverse == 0,
          "value 2 called an unrecovered dependency");
    check_guards(g, "value 2 wrote outside the object");
}

// --- value 3: transpose ---------------------------------------------------

void test_value_3_transposes_the_source() {
    reset_calls();
    CMatrix4<float> source = make_matrix();
    source_fixture(source);
    CMatrix4<float> source_copy = source;

    Guarded g;
    construct(g, source, 3);

    check(calls.inverse == 0, "value 3 called getInverse");
    check(calls.transposed == 1, "value 3 did not call getTransposed once");
    check(calls.last_transposed_this == &source,
          "value 3 called getTransposed on the wrong object");
    check(calls.last_transposed_dest == &g.matrix(),
          "value 3 wrote the transpose into the wrong object");
    expect_transpose_of(source, kTransposeBase, g.matrix());
    check(same_floats(source, source_copy), "value 3 modified its source");
    check_guards(g, "value 3 wrote outside the object");
}

// --- value 4: inverse -----------------------------------------------------

void test_value_4_inverts_the_source() {
    reset_calls();
    CMatrix4<float> source = make_matrix();
    source_fixture(source);

    Guarded g;
    construct(g, source, 4);

    check(calls.inverse == 1, "value 4 did not call getInverse once");
    check(calls.transposed == 0, "value 4 called getTransposed");
    check(calls.last_inverse_this == &source,
          "value 4 called getInverse on the wrong object");
    check(calls.last_inverse_dest == &g.matrix(),
          "value 4 wrote the inverse into the wrong object");
    for (int i = 0; i < 16; ++i) {
        if (g.matrix().m_observedValues[i] != kInverseBase + static_cast<float>(i)) {
            check(false, "value 4 did not keep the inverse result");
            break;
        }
    }
    check_guards(g, "value 4 wrote outside the object");
}

void test_value_4_failed_inverse_zeroes_the_object() {
    reset_calls();
    inverse_succeeds = false;
    CMatrix4<float> source = make_matrix();
    source_fixture(source);

    Guarded g;
    construct(g, source, 4);

    check(calls.inverse == 1, "failed value 4 did not call getInverse once");
    check(all_bytes(g.matrix(), 0x00),
          "a failed inverse did not leave the all-zero matrix");
    check(!is_identity(g.matrix()), "a failed inverse produced the identity");
    check_guards(g, "failed value 4 wrote outside the object");
}

// --- value 5: inverse-transpose -------------------------------------------

void test_value_5_transposes_its_inverse() {
    reset_calls();
    CMatrix4<float> source = make_matrix();
    source_fixture(source);
    CMatrix4<float> source_copy = source;

    Guarded g;
    construct(g, source, 5);

    check(calls.inverse == 1, "value 5 did not call getInverse once");
    check(calls.transposed == 1, "value 5 did not call getTransposed once");
    check(calls.last_inverse_this == &source,
          "value 5 called getInverse on the wrong object");
    // The transpose is taken of the object that just received the inverse, not
    // of the source: the reference passes `this` in r0 at 0x108cdc.
    check(calls.last_transposed_this == &g.matrix(),
          "value 5 transposed the source instead of the inverse");
    check(calls.last_transposed_dest != &g.matrix(),
          "value 5 transposed into the object it was transposing");
    // The in-place result is the transpose of the 200..215 pattern.
    for (int row = 0; row < 4; ++row) {
        for (int column = 0; column < 4; ++column) {
            float const expected =
                kTransposeBase + static_cast<float>(row * 4 + column) +
                (kInverseBase + static_cast<float>(column * 4 + row));
            if (g.matrix().m_observedValues[row * 4 + column] != expected) {
                check(false, "value 5 result is not transpose(inverse(source))");
                row = 4;
                break;
            }
        }
    }
    check(same_floats(source, source_copy), "value 5 modified its source");
    check_guards(g, "value 5 wrote outside the object");
}

void test_value_5_failed_inverse_zeroes_and_skips_the_transpose() {
    reset_calls();
    inverse_succeeds = false;
    CMatrix4<float> source = make_matrix();
    source_fixture(source);

    Guarded g;
    construct(g, source, 5);

    check(calls.inverse == 1, "failed value 5 did not call getInverse once");
    check(calls.transposed == 0,
          "failed value 5 transposed an object that has no inverse");
    check(all_bytes(g.matrix(), 0x00),
          "a failed inverse in value 5 did not leave the all-zero matrix");
    check_guards(g, "failed value 5 wrote outside the object");
}

// --- untouched values -----------------------------------------------------

void test_values_0_and_6_upwards_write_nothing() {
    for (int mode : {0, 6, 7, 100, 0x7fffffff}) {
        reset_calls();
        CMatrix4<float> source = make_matrix();
        source_fixture(source);

        Guarded g;
        construct(g, source, mode);

        char msg[96];
        std::snprintf(msg, sizeof msg,
                      "mode %d wrote to the object", mode);
        check(all_bytes(g.matrix(), 0xA5), msg);
        std::snprintf(msg, sizeof msg,
                      "mode %d called an unrecovered dependency", mode);
        check(calls.transposed == 0 && calls.inverse == 0, msg);
        check_guards(g, "an untouched mode wrote outside the object");
    }
}

void test_repeat_construction() {
    reset_calls();
    CMatrix4<float> source = make_matrix();
    source_fixture(source);
    Guarded g;
    construct(g, source, 2);
    check(is_identity(g.matrix()), "first construction is not the identity");
    construct(g, source, 1);
    check(same_floats(g.matrix(), source),
          "re-construction over an identity did not copy the source");
    construct_over(g, source, 0);
    check(same_floats(g.matrix(), source),
          "mode 0 disturbed an already-built object");
}

void test_object_shape() {
    check(sizeof(CMatrix4<float>) == 64, "CMatrix4<float> is not 64 bytes");
    CMatrix4<float> m = make_matrix();
    check(reinterpret_cast<void*>(&m.m_observedValues[0]) ==
              reinterpret_cast<void*>(&m),
          "the 16 floats do not start at offset 0");
}

}  // namespace

int main() {
    test_value_1_copies_the_source();
    test_value_2_builds_the_identity();
    test_value_3_transposes_the_source();
    test_value_4_inverts_the_source();
    test_value_4_failed_inverse_zeroes_the_object();
    test_value_5_transposes_its_inverse();
    test_value_5_failed_inverse_zeroes_and_skips_the_transpose();
    test_values_0_and_6_upwards_write_nothing();
    test_repeat_construction();
    test_object_shape();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("collada_matrix4_copy_ctor_behaviour: all checks passed\n");
    return 0;
}
