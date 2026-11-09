#include "vox_memory_cursor_seek.h"

#include <climits>

// Reviewed Premium metadata: A32, VA 0x009c8cfc, 164 bytes.
// ELF SHA256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Window SHA256: 3a8f70db5de465c7c58708925e32c1903a8de8e67ee61626db1b935da1572b00
// Independently checked offline with bounded ARM emulation and a substituted
// size-query receiver; no private source was used to implement this function.
// 7,423 oracle/reference cases agreed (including wrapping arithmetic and live
// size changes), visiting all 41 instruction addresses. This is bounded window
// behavior evidence with a stubbed size query, not original-stream validation.
// Observed: cursor position at +8, stream at +4, virtual size entry at +16.
// The portable header deliberately does not reproduce those object layouts.
// Origins 0/1/2: absolute/current/end-minus-one-minus-offset. Other origins
// leave the candidate unchanged but still validate it. Bounds are signed and
// inclusive [0, size]. Failure returns -1 without storing; success stores and
// returns 0. End origin queries size before arithmetic, then queries again
// only if the wrapped candidate is nonnegative. Other origins query only at
// that final bound check. Size-query side effects are outside our ownership.
// Original compiler/flags and instruction-window agreement: NOT MATCHED.

static_assert(INT_MAX == 2147483647 && INT_MIN == (-2147483647 - 1),
              "Seek models signed 32-bit ARM registers");
static_assert(UINT_MAX == 4294967295U, "Seek needs 32-bit unsigned arithmetic");

namespace {
// Convert register bits to their signed mathematical value without an
// out-of-range unsigned-to-signed cast or undefined signed overflow.
int signed_register(unsigned int bits) {
    if (bits <= static_cast<unsigned int>(INT_MAX)) {
        return static_cast<int>(bits);
    }
    return -1 - static_cast<int>(UINT_MAX - bits);
}
}  // namespace

int vox::StreamMemoryBufferCursor::Seek(int offset, int origin) {
    int candidate = position;
    if (origin == 0) {
        candidate = offset;
    } else if (origin == 1) {
        candidate = signed_register(static_cast<unsigned int>(position) +
                                    static_cast<unsigned int>(offset));
    } else if (origin == 2) {
        const int size = size_access.query(size_access.context);
        candidate = signed_register(static_cast<unsigned int>(size) - 1U -
                                    static_cast<unsigned int>(offset));
    }

    if (candidate < 0) {
        return -1;
    }
    if (candidate > size_access.query(size_access.context)) {
        return -1;
    }
    position = candidate;
    return 0;
}
