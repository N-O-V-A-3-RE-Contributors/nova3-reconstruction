#include "vox_handle_timestamp.h"

namespace vox {
// Premium 0x00196748, 20 bytes, A32. Do not snapshot both source words:
// the first output can alias group, affecting the subsequent read.
void Handle::GetTimeStamp(uint32_t& timestamp_out, uint32_t& group_out) const {
    timestamp_out = timestamp;
    group_out = group;
}

// Premium 0x0019675c, 12 bytes, A32. Arguments are passed by value.
void Handle::SetTimeStamp(uint32_t timestamp_in, uint32_t group_in) {
    group = group_in;
    timestamp = timestamp_in;
}
} // namespace vox
