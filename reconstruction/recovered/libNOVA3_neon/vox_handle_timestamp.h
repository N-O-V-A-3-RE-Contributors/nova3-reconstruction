#ifndef NOVA_RECOVERED_VOX_HANDLE_TIMESTAMP_H
#define NOVA_RECOVERED_VOX_HANDLE_TIMESTAMP_H

#include <stdint.h>
#include <stddef.h>

namespace vox {
// Timestamp-only compatibility slice, NOT a complete original Handle definition.
// Observed Premium A32 accesses: 16 (first argument), 20 (second argument).
// INFERRED: these words represent timestamp and group. The preceding storage's
// types/meaning and any following storage are UNKNOWN. Do not use as a complete
// game-object ABI, or combine with a different Handle declaration.
// All other Handle methods: NOT RECONSTRUCTED (intentionally not declared).
struct Handle {
    uint32_t opaque_prefix[4];
    uint32_t timestamp;
    uint32_t group;

    void GetTimeStamp(uint32_t& timestamp_out, uint32_t& group_out) const;
    void SetTimeStamp(uint32_t timestamp_in, uint32_t group_in);
};
static_assert(sizeof(uint32_t) == 4, "requires 32-bit words");
static_assert(offsetof(Handle, timestamp) == 16, "slice offset");
static_assert(offsetof(Handle, group) == 20, "slice offset");
} // namespace vox

#endif
