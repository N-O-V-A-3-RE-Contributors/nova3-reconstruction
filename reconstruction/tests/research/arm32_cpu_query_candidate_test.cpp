// Source-only dependency tests. Link with -pthread -Wl,--wrap=pthread_once.
// The initializer below is a test model, never original CPU detection.
#include "../../research/arm32_cpu_query_candidate.h"
#include <atomic>
#include <cassert>
#include <cerrno>
#include <cstring>
#include <thread>

namespace {
std::atomic<int> initializer_calls(0);
std::atomic<int> once_calls(0);
int32_t initial_count;
bool force_once_error = false; // Set only after every worker joins.
}

extern "C" {
Nova3Arm32CpuQueryState nova3_arm32_cpu_state = {PTHREAD_ONCE_INIT, 123};

void nova3_arm32_cpu_initialize(void) {
    ++initializer_calls;
    nova3_arm32_cpu_state.count = initial_count;
}

int __real_pthread_once(pthread_once_t*, void (*)(void));
int __wrap_pthread_once(pthread_once_t* once, void (*initialize)(void)) {
    assert(once == &nova3_arm32_cpu_state.once);
    assert(initialize == nova3_arm32_cpu_initialize);
    ++once_calls;
    if (force_once_error) return EINVAL; // Explicit injected service failure.
    return __real_pthread_once(once, initialize);
}
}

int main(int argc, char** argv) {
    // Each mode runs in a fresh process; never reset a used pthread_once_t.
    assert(argc == 2);
    if (std::strcmp(argv[1], "positive") == 0) initial_count = 7;
    else if (std::strcmp(argv[1], "zero") == 0) initial_count = 0;
    else if (std::strcmp(argv[1], "negative") == 0) initial_count = -17;
    else if (std::strcmp(argv[1], "minimum") == 0) initial_count = INT32_MIN;
    else if (std::strcmp(argv[1], "maximum") == 0) initial_count = INT32_MAX;
    else return 2;

    const int workers = 8;
    const int queries = 128;
    std::atomic<int> ready(0);
    std::atomic<bool> start(false);
    std::thread threads[workers];
    for (int n = 0; n < workers; ++n) {
        threads[n] = std::thread([&] {
            ++ready;
            while (!start.load()) std::this_thread::yield();
            for (int k = 0; k < queries; ++k)
                assert(nova3_arm32_cpu_count_candidate() == initial_count);
        });
    }
    while (ready.load() != workers) std::this_thread::yield();
    start.store(true);
    for (int n = 0; n < workers; ++n) threads[n].join();
    assert(initializer_calls.load() == 1);
    assert(once_calls.load() == workers * queries);

    // Read the cache after once returns on every query; no snapshot or clamp.
    nova3_arm32_cpu_state.count = -99;
    assert(nova3_arm32_cpu_count_candidate() == -99);
    assert(initializer_calls.load() == 1);

    // A failed once service must not replace the stored result with a fallback.
    force_once_error = true;
    nova3_arm32_cpu_state.count = INT32_MIN;
    assert(nova3_arm32_cpu_count_candidate() == INT32_MIN);
    assert(initializer_calls.load() == 1);
    assert(once_calls.load() == workers * queries + 2);
}
