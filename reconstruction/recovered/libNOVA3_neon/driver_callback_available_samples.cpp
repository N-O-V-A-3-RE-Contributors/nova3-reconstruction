#include "driver_callback_available_samples.h"

namespace nova3_recovered {

// Premium retail SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// _ZN3vox29DriverCallbackSourceInterface21GetNbAvailableSamplesEi
// A32, 192 bytes at 0x009d38d8. Independently checked by the bounded reference
// test, not accepted from the private template. Reference ranges:
// 38dc-3908 initial count/index/free checks; 390c-392c initial quotient-minus-
// consumed and cap; 3930-3968 remainder-indexed subsequent records and cap;
// 396c-397c count-bounded traversal; 3980-3994 exhaustion/prefix/cap returns.
// This is portable source, NOT an original class ABI or instruction match.
int GetNbAvailableSamples(const CallbackSampleState& state, int request) {
    typedef char Require32BitInt[sizeof(int) == 4 ? 1 : -1];
    (void)sizeof(Require32BitInt);
    if (state.count <= 0) {
        return -1;
    }
    int available = 0;
    int index = state.current;
    for (int visited = 0; visited != state.count; ++visited) {
        const CallbackSampleBuffer& buffer = state.buffers[index];
        if (buffer.free != 0) {
            return available;
        }
        available += buffer.extent / state.divisor - buffer.consumed_samples;
        if (available >= request) {
            return request;
        }
        index = (index + 1) % state.count;
    }
    return -1;
}

} // namespace nova3_recovered
