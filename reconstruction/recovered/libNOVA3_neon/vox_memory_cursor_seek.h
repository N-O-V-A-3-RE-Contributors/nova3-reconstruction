#ifndef NOVA3_RECOVERED_VOX_MEMORY_CURSOR_SEEK_H
#define NOVA3_RECOVERED_VOX_MEMORY_CURSOR_SEEK_H

namespace vox {

// Source-owned bridge for the unreconstructed stream's virtual size query.
// The callback must be valid and return a signed 32-bit size. No ownership is
// transferred. The callback must not mutate the cursor or its size-access bridge.
// This is NOT the original StreamMemoryBuffer ABI or vtable.
struct MemoryCursorSizeAccess {
    void* context;
    int (*query)(void*);
};

// Only Seek is recovered. Aggregate fields permit source-owned setup without
// claiming recovery of construction, allocation, reading, or other methods.
// Stream and position member offsets in this portable adapter are not ABI claims.
struct StreamMemoryBufferCursor {
    MemoryCursorSizeAccess size_access;
    int position;

    int Seek(int offset, int origin);
};

}  // namespace vox
#endif
