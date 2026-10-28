// Source-owned research interface; not a production CPU detector.
#ifndef NOVA3_RESEARCH_ARM32_CPU_QUERY_CANDIDATE_H
#define NOVA3_RESEARCH_ARM32_CPU_QUERY_CANDIDATE_H

#include <pthread.h>
#include <stdint.h>

// Grouping these adjacent globals is a source-owned layout hypothesis, not a
// recovered original struct. ARM32 requires a four-byte once followed by count.
struct Nova3Arm32CpuQueryState {
    pthread_once_t once;
    int32_t count;
};

// The reference globals and initializer bind locally. Hidden external research
// dependencies model that binding without implementing the unknown initializer.
// A test/platform provider must define both; there are no success stubs here.
extern "C" {
extern Nova3Arm32CpuQueryState nova3_arm32_cpu_state
    __attribute__((visibility("hidden")));
void nova3_arm32_cpu_initialize(void) __attribute__((visibility("hidden")));
int32_t nova3_arm32_cpu_count_candidate(void);
}

// State must outlive queries. The initializer must publish count through the
// once service and must not recursively query this same once object. Writes
// after initialization need external synchronization. No count validation or
// once-status fallback is part of this candidate.
#endif
