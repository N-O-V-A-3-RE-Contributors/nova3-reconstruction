// Source-owned no-argument ABI experiment for android_getCpuCount at 0x000d2f10.
// Premium SHA256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// NOT MATCHED. No original instructions, assembly or runtime addresses embedded.
#include "arm32_cpu_query_candidate.h"
#include <stddef.h>

// C++03-compatible so a future historical compiler probe need not rewrite this
// candidate. These enforce our layout hypothesis, not a discovered C++ type.
typedef char Nova3OnceMustBeFourBytes[sizeof(pthread_once_t) == 4 ? 1 : -1];
typedef char Nova3CountMustFollowOnce[
    offsetof(Nova3Arm32CpuQueryState, count) == 4 ? 1 : -1];
typedef char Nova3StateMustBeEightBytes[
    sizeof(Nova3Arm32CpuQueryState) == 8 ? 1 : -1];

extern "C" int32_t nova3_arm32_cpu_count_candidate(void) {
    (void)pthread_once(&nova3_arm32_cpu_state.once, nova3_arm32_cpu_initialize);
    return nova3_arm32_cpu_state.count;
}
