// Behaviour test for the recovered
//
//   glitch::core::CMatrix4<float>::getTransposed(CMatrix4<float>&) const
//
// Reference: analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//   ELF VA 0x000eba84, 132 bytes, A32, STT_FUNC (STB_LOCAL).
//
// The recovered TU is included unchanged, so the shipped body runs; nothing is
// stubbed, because the reference window calls nothing and reads no global.
//
// Asserted: every one of the 16 destination slots is written exactly once from
// the distinct source slot (k % 4) * 4 + k / 4; the source is never modified;
// the four diagonal slots are the fixed points and every other slot moves; the
// mapping is an involution (transposing twice restores the original), so the
// operation is a transpose and not a rotation or a partial copy; a destination
// filled with junk ends up fully overwritten; the result is independent of the
// values (no arithmetic, only movement); guards prove no store lands outside the
// 64-byte object.
//
// One degenerate input is pinned too: the reference reads and writes the same
// address when the destination IS the source (it never loads the whole object
// up front), so `out == *this` is simulated element by element in the observed
// order and compared.  This documents the observed aliasing behaviour; it is
// not a recommended call.
//
// Instruction agreement is a separate measurement
// (tools/compare/collada_matrix4_transpose_pilot.py, which reports a byte-equal
// 132-byte window at -O3 and nothing beyond it).
#include <cstdio>
#include <cstring>
#include <new>

#include "collada_matrix4_transpose.cpp"

namespace {

using glitch::core::CMatrix4;

int failures = 0;

void check(bool condition, const char* what) {
    if (!condition) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

// 64-byte matrix in raw storage with guard bytes, so an out-of-bounds store is
// visible.  CMatrix4 has no default constructor, so the storage is filled and
// reinterpreted rather than constructed.
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

// Values chosen so that any index error changes a value: exact in binary32 and
// distinct per slot.
float slot_value(int index) { return 1000.0f + static_cast<float>(index); }

void fill_slots(CMatrix4<float>& m) {
    for (int i = 0; i < 16; ++i) m.m_observedValues[i] = slot_value(i);
}

void guard(Guarded& g) {
    fill(g.before, sizeof g.before, 0x5A);
    fill(g.after, sizeof g.after, 0x5A);
    fill(g.storage, sizeof g.storage, 0xA5);
}

void check_guards(Guarded const& g, const char* what) {
    for (std::size_t i = 0; i < sizeof g.before; ++i) {
        if (g.before[i] != 0x5A || g.after[i] != 0x5A) {
            check(false, what);
            return;
        }
    }
}

// Independently written transpose: go through a 2-D staging array instead of the
// recovered flat index arithmetic.
void reference_transpose(float const* source, float* destination) {
    float staged[4][4];
    for (int outer = 0; outer < 4; ++outer) {
        for (int inner = 0; inner < 4; ++inner) {
            staged[outer][inner] = source[outer * 4 + inner];
        }
    }
    for (int outer = 0; outer < 4; ++outer) {
        for (int inner = 0; inner < 4; ++inner) {
            destination[outer * 4 + inner] = staged[inner][outer];
        }
    }
}

// --- the mapping -----------------------------------------------------------

void test_slot_mapping() {
    Guarded source;
    guard(source);
    fill_slots(source.matrix());

    Guarded destination;
    guard(destination);

    // Called through a const reference, so the const-ness is exercised too.
    CMatrix4<float> const& source_ref = source.matrix();
    source_ref.getTransposed(destination.matrix());

    for (int destination_slot = 0; destination_slot < 16; ++destination_slot) {
        int const expected_source_slot =
            (destination_slot % 4) * 4 + destination_slot / 4;
        float const got = destination.matrix().m_observedValues[destination_slot];
        float const expected = slot_value(expected_source_slot);
        if (got != expected) {
            std::printf("FAIL: destination slot %d was not written from source "
                        "slot %d\n", destination_slot, expected_source_slot);
            ++failures;
            return;
        }
    }
    check_guards(destination, "a store landed outside the destination object");
}

void test_diagonal_is_fixed_and_the_rest_moves() {
    Guarded source;
    guard(source);
    fill_slots(source.matrix());
    Guarded destination;
    guard(destination);
    source.matrix().getTransposed(destination.matrix());

    for (int slot = 0; slot < 16; ++slot) {
        bool const diagonal = (slot % 5 == 0);
        bool const unchanged =
            destination.matrix().m_observedValues[slot] ==
            source.matrix().m_observedValues[slot];
        if (diagonal != unchanged) {
            std::printf("FAIL: slot %d is %s but was %s\n", slot,
                        diagonal ? "diagonal" : "off-diagonal",
                        unchanged ? "unchanged" : "moved");
            ++failures;
            return;
        }
    }
}

void test_source_is_never_modified() {
    Guarded source;
    guard(source);
    fill_slots(source.matrix());
    unsigned char before[64];
    std::memcpy(before, source.storage, sizeof before);
    Guarded destination;
    guard(destination);
    source.matrix().getTransposed(destination.matrix());
    check(std::memcmp(before, source.storage, sizeof before) == 0,
          "the source object was modified");
}

void test_argument_order() {
    // `this` is the source and the argument is the destination: swapping which
    // object receives the call must swap the roles, not the meaning.
    Guarded first;
    guard(first);
    fill_slots(first.matrix());
    Guarded second;
    guard(second);
    for (int i = 0; i < 16; ++i) {
        second.matrix().m_observedValues[i] = slot_value(i) + 100.0f;
    }

    second.matrix().getTransposed(first.matrix());
    // Now `first` (the destination) holds the transpose of `second`, and
    // `second` (the source) is untouched.
    for (int destination_slot = 0; destination_slot < 16; ++destination_slot) {
        int const source_slot = (destination_slot % 4) * 4 + destination_slot / 4;
        if (first.matrix().m_observedValues[destination_slot] !=
            slot_value(source_slot) + 100.0f) {
            check(false, "the argument is not the destination");
            return;
        }
        if (second.matrix().m_observedValues[destination_slot] !=
            slot_value(destination_slot) + 100.0f) {
            check(false, "the receiver is not the source");
            return;
        }
    }
}

void test_matches_an_independent_transpose() {
    Guarded source;
    guard(source);
    // Non-linear values so the two implementations cannot agree by symmetry.
    for (int i = 0; i < 16; ++i) {
        source.matrix().m_observedValues[i] =
            static_cast<float>(i) * 1.5f - 7.25f;
    }
    Guarded destination;
    guard(destination);
    source.matrix().getTransposed(destination.matrix());

    float expected[16] = {0};
    reference_transpose(source.matrix().m_observedValues, expected);
    check(std::memcmp(expected, destination.matrix().m_observedValues,
                      sizeof expected) == 0,
          "the result differs from an independent transpose");
}

void test_is_an_involution() {
    Guarded original;
    guard(original);
    for (int i = 0; i < 16; ++i) {
        original.matrix().m_observedValues[i] =
            static_cast<float>(i) * 1.5f - 7.25f;
    }

    Guarded once;
    guard(once);
    original.matrix().getTransposed(once.matrix());

    Guarded twice;
    guard(twice);
    once.matrix().getTransposed(twice.matrix());

    check(std::memcmp(original.storage, twice.storage, sizeof original.storage) == 0,
          "transposing twice did not restore the original");
}

void test_destination_is_fully_overwritten() {
    Guarded source;
    guard(source);
    fill_slots(source.matrix());

    Guarded destination;
    guard(destination);
    for (int i = 0; i < 16; ++i) {
        destination.matrix().m_observedValues[i] = -1.0f;
    }
    source.matrix().getTransposed(destination.matrix());

    int remaining = 0;
    for (int i = 0; i < 16; ++i) {
        if (destination.matrix().m_observedValues[i] == -1.0f) ++remaining;
    }
    check(remaining == 0,
          "some destination slots kept their previous contents");
}

void test_values_are_moved_not_computed() {
    // Bit patterns, including a negative zero and a NaN, must survive unchanged:
    // the reference emits loads and stores only, no arithmetic.
    Guarded source;
    guard(source);
    unsigned int const bits[16] = {
        0x00000000u, 0x80000000u, 0x3f800000u, 0x7fc00000u,
        0xbf000000u, 0x40490fdbu, 0xc0490fdbu, 0x00000001u,
        0x7f800000u, 0xff800000u, 0x3eaaaaabu, 0xbeaaaaabu,
        0x4b189680u, 0xcb189680u, 0x33333333u, 0xb3333333u,
    };
    std::memcpy(source.storage, bits, sizeof bits);

    Guarded destination;
    guard(destination);
    source.matrix().getTransposed(destination.matrix());

    for (int destination_slot = 0; destination_slot < 16; ++destination_slot) {
        int const source_slot = (destination_slot % 4) * 4 + destination_slot / 4;
        if (std::memcmp(&destination.matrix().m_observedValues[destination_slot],
                        &bits[source_slot], 4) != 0) {
            std::printf("FAIL: destination slot %d did not preserve the source "
                        "bit pattern\n", destination_slot);
            ++failures;
            return;
        }
    }
}

void test_in_place_call_is_element_wise() {
    // The reference never stages the matrix: it loads and stores slot by slot in
    // ascending destination order, so `out == *this` produces the element-wise
    // simulation below rather than a correct transpose.  Pinned so the recovered
    // source cannot accidentally "improve" on the observed behaviour.
    Guarded source;
    guard(source);
    fill_slots(source.matrix());

    float simulated[16];
    for (int i = 0; i < 16; ++i) simulated[i] = slot_value(i);
    for (int destination_slot = 0; destination_slot < 16; ++destination_slot) {
        int const source_slot = (destination_slot % 4) * 4 + destination_slot / 4;
        simulated[destination_slot] = simulated[source_slot];
    }

    source.matrix().getTransposed(source.matrix());
    check(std::memcmp(simulated, source.matrix().m_observedValues,
                      sizeof simulated) == 0,
          "in-place behaviour does not match the reference's element-wise order");
    check_guards(source, "the in-place call wrote outside the object");
}

void test_object_shape() {
    check(sizeof(CMatrix4<float>) == 64, "CMatrix4<float> is not 64 bytes");
    Guarded g;
    guard(g);
    check(reinterpret_cast<void*>(&g.matrix().m_observedValues[0]) ==
              reinterpret_cast<void*>(&g.matrix()),
          "the 16 floats do not start at offset 0");
}

}  // namespace

int main() {
    test_slot_mapping();
    test_diagonal_is_fixed_and_the_rest_moves();
    test_source_is_never_modified();
    test_argument_order();
    test_matches_an_independent_transpose();
    test_is_an_involution();
    test_destination_is_fully_overwritten();
    test_values_are_moved_not_computed();
    test_in_place_call_is_element_wise();
    test_object_shape();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("collada_matrix4_transpose_behaviour: all checks passed\n");
    return 0;
}
