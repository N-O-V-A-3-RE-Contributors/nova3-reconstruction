// Behaviour test for the recovered
//
//   glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float>::eConstructor)
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   ELF VA 0x00108c54, 56 bytes, A32, STT_FUNC.
//
// The recovered TU (collada_matrix4_ctor.cpp) is included here unchanged, and
// nothing is stubbed: the whole function under test is compiled from the same
// source file the ARM pilot compiles, so these assertions exercise the shipped
// body rather than a copy.  Instruction agreement is a separate measurement
// (tools/compare/collada_matrix4_ctor_pilot.py); this file only claims
// behaviour.
//
// What the reference bytes say, and what is asserted below:
//   * enumerator values 0 and 1 write nothing at all (`cmp r1,#1` + unsigned
//     `bls` to the epilogue, no store on that path);
//   * any value >= 2 zeroes all 64 bytes and then sets floats 0, 5, 10 and 15
//     to 1.0f -- the 4x4 identity;
//   * no byte outside the 64-byte object is touched (the reference stores only
//     through `this`);
//   * the constructor returns `this` in r0 (C1 complete-object constructor), so
//     `CMatrix4<float> m = CMatrix4<float>(...)`-style initialisation of a
//     distinct object is a copy of an already-initialised object, not an alias.
#include <cmath>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <new>

#include "collada_matrix4_ctor.cpp"

namespace {

int failures = 0;

void check(bool condition, const char* what) {
    if (!condition) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

// A 64-byte matrix stored in raw storage with guard bytes on both sides, so an
// out-of-bounds store is visible and no default constructor is required (the
// recovered CMatrix4 has none).
struct Guarded {
    alignas(4) unsigned char before[16];
    alignas(4) unsigned char storage[64];
    alignas(4) unsigned char after[16];

    glitch::core::CMatrix4<float>& matrix() {
        return *reinterpret_cast<glitch::core::CMatrix4<float>*>(storage);
    }
};

void fill(unsigned char* p, std::size_t n, unsigned char v) {
    for (std::size_t i = 0; i < n; ++i) p[i] = v;
}

bool is_identity(glitch::core::CMatrix4<float> const& m) {
    for (int i = 0; i < 16; ++i) {
        float const expected = (i % 5 == 0) ? 1.0f : 0.0f;
        if (m.m_observedValues[i] != expected) return false;
    }
    return true;
}

bool all_bytes(glitch::core::CMatrix4<float> const& m, unsigned char v) {
    unsigned char const* raw = reinterpret_cast<unsigned char const*>(&m);
    for (std::size_t i = 0; i < sizeof m; ++i) {
        if (raw[i] != v) return false;
    }
    return true;
}

void construct_into(Guarded& g, int value) {
    // Placement construction through the recovered constructor: this proves the
    // body writes through its `this` argument and nothing else.
    new (g.storage) glitch::core::CMatrix4<float>(
        static_cast<glitch::core::CMatrix4<float>::eConstructor>(value));
}

void test_no_write_for_zero_and_one() {
    for (int value = 0; value <= 1; ++value) {
        Guarded g;
        fill(g.before, sizeof g.before, 0x5A);
        fill(g.after, sizeof g.after, 0x5A);
        fill(g.storage, sizeof g.storage, 0xA5);
        construct_into(g, value);
        check(all_bytes(g.matrix(), 0xA5), "constructor value 0/1 wrote the object");
        for (std::size_t i = 0; i < sizeof g.before; ++i) {
            check(g.before[i] == 0x5A, "constructor wrote before the object");
            check(g.after[i] == 0x5A, "constructor wrote after the object");
        }
    }
}

void test_identity_for_two_and_above() {
    for (int value : {2, 3, 4, 5, 100, 0x7fffffff}) {
        Guarded g;
        fill(g.before, sizeof g.before, 0x5A);
        fill(g.after, sizeof g.after, 0x5A);
        fill(g.storage, sizeof g.storage, 0xA5);
        construct_into(g, value);
        if (!is_identity(g.matrix())) {
            char msg[96];
            std::snprintf(msg, sizeof msg,
                          "value %d did not produce the identity", value);
            check(false, msg);
        }
        for (std::size_t i = 0; i < sizeof g.before; ++i) {
            check(g.before[i] == 0x5A, "identity path wrote before the object");
            check(g.after[i] == 0x5A, "identity path wrote after the object");
        }
    }
}

void test_diagonal_only_is_set() {
    // Every off-diagonal element must come out exactly 0.0f, not merely
    // "small", which is what distinguishes a memset from a partial clear.
    glitch::core::CMatrix4<float> m(
        glitch::core::CMatrix4<float>::kObservedConstructorValue2);
    for (int i = 0; i < 16; ++i) {
        if (i % 5 == 0) {
            check(m.m_observedValues[i] == 1.0f, "diagonal element is not 1.0f");
        } else {
            check(m.m_observedValues[i] == 0.0f, "off-diagonal element is not 0.0f");
            check(!std::signbit(m.m_observedValues[i]),
                  "off-diagonal element is negative zero");
        }
    }
}

void test_repeat_construction_is_idempotent() {
    Guarded g;
    fill(g.storage, sizeof g.storage, 0xA5);
    construct_into(g, 2);
    check(is_identity(g.matrix()), "first construction is not the identity");
    // Overwrite with junk, then rebuild in the same storage.
    for (int i = 0; i < 16; ++i) g.matrix().m_observedValues[i] = -7.25f;
    construct_into(g, 2);
    check(is_identity(g.matrix()), "second construction is not the identity");
    // ... and a value <= 1 afterwards must leave that identity untouched.
    construct_into(g, 1);
    check(is_identity(g.matrix()), "value 1 modified an already-built identity");
}

void test_value_is_returned_object() {
    // The reference returns `this` in r0, so the object is usable as a value.
    glitch::core::CMatrix4<float> m(
        glitch::core::CMatrix4<float>::kObservedConstructorValue2);
    check(is_identity(m), "returned object is not the constructed identity");
    check(reinterpret_cast<void*>(&m.m_observedValues[0]) ==
              reinterpret_cast<void*>(&m),
          "the 16 floats do not start at offset 0 of the object");
    check(sizeof m == 64, "CMatrix4<float> is not 64 bytes");
}

}  // namespace

int main() {
    test_no_write_for_zero_and_one();
    test_identity_for_two_and_above();
    test_diagonal_only_is_set();
    test_repeat_construction_is_idempotent();
    test_value_is_returned_object();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("collada_matrix4_ctor_behaviour: all checks passed\n");
    return 0;
}
