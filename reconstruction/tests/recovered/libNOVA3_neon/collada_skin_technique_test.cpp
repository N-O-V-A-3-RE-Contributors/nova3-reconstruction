// Standalone source-only regression for three recovered
// glitch::collada::detail::CSoftwareSkinTechnique leaves:
//
//   0x00710b50  checkAvailability(video::STechnique const&, unsigned) const
//   0x00710b48  needOutputBuffer() const
//   0x00710b58  attach(scene::ISceneNode*)
//
// The recovered translation unit is included unchanged.  There is nothing to
// stub: none of the three functions reads memory through its arguments, so the
// test supplies opaque storage for the incomplete types and asserts only the
// behaviour the reference bytes actually encode:
//
//   * both predicates answer true for arbitrary argument bytes and for several
//     distinct receivers (no argument or `this` is read);
//   * attach stores the pointer at object offset +0x0c and writes nothing else,
//     checked over a sentinel-filled object, several pointer values, and two
//     successive calls.
//
// Machine-code equality with the reference is measured separately by
// tools/compare/collada_skin_trivial_pilot.py and is not claimed here.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "../../../recovered/libNOVA3_neon/collada_skin_technique.cpp"

namespace {

void require(bool condition, const char* what) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        std::abort();
    }
}

constexpr std::size_t kSize = sizeof(glitch::collada::detail::
                                     CSoftwareSkinTechnique);
// The reference is ARM32: `str r1,[r0,#0xc]` fixes the member at +0xc only for
// 32-bit pointers.  A 64-bit host lays the declared class out differently, so
// the test never asserts the absolute offset there — it asserts that attach
// writes exactly one pointer-sized word, and checks +0xc only where the ABI
// makes that the true offset.
constexpr std::size_t kReferenceNodeOffset = 0x0c;
constexpr bool kPointerIs32Bit = sizeof(void*) == 4;
constexpr unsigned char kSentinel = 0xA5;

// Opaque stand-ins for the incomplete argument types.  Both functions promise
// (by their reference windows) never to dereference them.
alignas(8) unsigned char technique_storage[16];
alignas(8) unsigned char node_storage[16];

glitch::video::STechnique& opaque_technique() {
    std::memset(technique_storage, 0x5A, sizeof technique_storage);
    return *reinterpret_cast<glitch::video::STechnique*>(technique_storage);
}

glitch::scene::ISceneNode* opaque_node(std::uintptr_t value) {
    std::memset(node_storage, 0x3C, sizeof node_storage);
    return reinterpret_cast<glitch::scene::ISceneNode*>(value);
}

void fill(unsigned char* object) {
    std::memset(object, kSentinel, kSize);
}

std::uintptr_t read_word(const unsigned char* object, std::size_t offset) {
    std::uintptr_t value = 0;
    std::memcpy(&value, object + offset, sizeof value < 4 ? sizeof value : 4);
    return value;
}

}  // namespace

int main() {
    // The only layout fact declared by these slices: on the reference's 32-bit
    // ABI the observed store lands inside a pointer-sized object word.
    require(kSize >= kReferenceNodeOffset + sizeof(void*) || !kPointerIs32Bit,
            "object is too small for the observed +0x0c pointer");

    alignas(8) unsigned char object[kSize];

    // 1. Constant predicates, over sentinel receivers and junk arguments.
    for (int i = 0; i < 3; ++i) {
        fill(object);
        auto* self = reinterpret_cast<glitch::collada::detail::
                                      CSoftwareSkinTechnique*>(object);
        require(self->checkAvailability(opaque_technique(),
                                        static_cast<unsigned>(0xFFFFFFFFu * i)),
                "checkAvailability returned false");
        require(self->needOutputBuffer(),
                "needOutputBuffer returned false");
        for (std::size_t b = 0; b < kSize; ++b) {
            require(object[b] == kSentinel,
                    "a predicate wrote through its receiver");
        }
    }

    // 2. attach writes exactly one pointer-sized word holding its argument and
    //    touches nothing else, whatever the host pointer width.
    const std::uintptr_t values[] = {0x1u, 0xDEADBEEFu, 0x0u, 0x7FFFFFF0u};
    for (std::uintptr_t value : values) {
        alignas(8) unsigned char before[kSize];
        fill(object);
        std::memcpy(before, object, kSize);
        auto* self = reinterpret_cast<glitch::collada::detail::
                                      CSoftwareSkinTechnique*>(object);
        self->attach(opaque_node(value));

        std::size_t changed_first = kSize;
        std::size_t changed_count = 0;
        for (std::size_t b = 0; b < kSize; ++b) {
            if (object[b] != before[b]) {
                if (changed_first == kSize) {
                    changed_first = b;
                }
                ++changed_count;
            }
        }
        require(changed_count == sizeof(void*),
                "attach changed the wrong number of bytes");
        require(changed_first % sizeof(void*) == 0,
                "attach wrote an unaligned pointer slot");
        require(read_word(object, changed_first) == value,
                "attach did not store its argument in the written slot");
        if (kPointerIs32Bit) {
            require(changed_first == kReferenceNodeOffset,
                    "on a 32-bit ABI attach must write at +0x0c");
        }
    }

    // 3. Successive calls overwrite the same word (no accumulation, no early
    //    return on a previously stored value).
    {
        alignas(8) unsigned char once[kSize];
        fill(object);
        auto* self = reinterpret_cast<glitch::collada::detail::
                                      CSoftwareSkinTechnique*>(object);
        self->attach(opaque_node(0x1234u));
        std::memcpy(once, object, kSize);
        self->attach(opaque_node(0x5678u));
        std::size_t changed = 0;
        std::size_t slot = kSize;
        for (std::size_t b = 0; b < kSize; ++b) {
            if (object[b] != kSentinel) {
                if (slot == kSize) {
                    slot = b;
                }
                ++changed;
            }
        }
        require(changed == sizeof(void*), "second attach touched extra bytes");
        require(read_word(object, slot) == 0x5678u,
                "second attach did not replace the stored pointer");
        require(self->needOutputBuffer() &&
                self->checkAvailability(opaque_technique(), 0u),
                "predicates changed after attach");
    }

    std::printf("ok: 3 recovered CSoftwareSkinTechnique leaves behave as "
                "recovered (sizeof=%zu, pointer=%zu B)\n",
                kSize, sizeof(void*));
    return 0;
}
