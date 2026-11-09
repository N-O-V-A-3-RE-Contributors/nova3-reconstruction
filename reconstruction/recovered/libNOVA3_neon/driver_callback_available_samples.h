// Source-owned view for Premium GetNbAvailableSamples(int), VA 0x009d38d8.
#ifndef NOVA3_RECOVERED_DRIVER_CALLBACK_AVAILABLE_SAMPLES_H
#define NOVA3_RECOVERED_DRIVER_CALLBACK_AVAILABLE_SAMPLES_H

namespace nova3_recovered {

// Names and units are inferred from arithmetic, NOT recovered original types.
// The reference uses 24-byte records: signed word +4 / divisor, minus signed
// word +16; a nonzero byte +20 terminates traversal (called "free" here).
// This portable declaration intentionally does NOT reproduce that record ABI.
struct CallbackSampleBuffer {
    int extent;
    int consumed_samples;
    unsigned char free;
};

// Correspondence in the original receiver: count +0x50, current +0x58,
// divisor +0x68, record-array pointer +0x6c. All other class fields, ownership,
// base classes and vtable layout remain UNRESOLVED. Never cast a game object
// to this view or add this method to the incomplete driver_callback.h class.
struct CallbackSampleState {
    const CallbackSampleBuffer* buffers;
    int count;
    int current;
    int divisor;
};

// Read-only accounting of at most count slots, starting at current, wrapping.
// A free slot returns the accumulated prefix; satisfying the request returns
// request; exhausting the entire ring without either event returns -1 (even
// if some samples were accumulated). count <= 0 also returns -1.
// Supported domain: 32-bit int; request >= 0. For count > 0, buffers points to
// count stable records, 0 <= current < count, divisor > 0, extent >= 0,
// 0 <= consumed_samples <= extent/divisor for every visited nonfree record,
// and all intermediate sums/index increments fit signed int. These are caller
// preconditions for defined portable arithmetic, not invented error handling.
// Free slots need no valid extent/consumption, nor does count <= 0 need a ring.
// No synchronization or ABI compatibility is claimed.
int GetNbAvailableSamples(const CallbackSampleState& state, int request);

} // namespace nova3_recovered
#endif
