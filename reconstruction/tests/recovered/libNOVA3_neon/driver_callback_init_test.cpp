// Standalone source-only regression for the recovered
// vox::DriverCallbackInterface::Init(void*) slice (ELF VA 0x009d53f4, 48 B).
//
// The recovered translation unit is included unchanged; only
// vox::DriverCallbackSourceInterface::DetectNeon is supplied here as a stub
// whose result the test controls.  That makes the initializer's own behaviour
// observable: which statics it clears, what it stores into the NEON flag, how
// many times it consults the probe, and that it neither touches the remaining
// recovered statics nor needs its `this` or its void* argument.
//
// Scope: behaviour of the source on the host.  The reference window's
// instruction shape, its PIC base resolution and its store-to-symbol mapping are
// measured by tools/compare/driver_callback_init_pilot.py and are NOT claimed
// here beyond the facts the assertions encode.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "../../../recovered/libNOVA3_neon/driver_callback_init.cpp"

namespace {

void require(bool condition, const char* what) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        std::abort();
    }
}

bool neon_result = false;
unsigned detect_calls = 0;

void set_dirty() {
    vox::DriverCallbackInterface::m_sWorkBuffer.size = 7;
    vox::DriverCallbackInterface::m_sWorkBuffer.data = reinterpret_cast<void*>(0x1);
    vox::DriverCallbackInterface::m_sMixingBuffer.size = 9;
    vox::DriverCallbackInterface::m_sMixingBuffer.data = reinterpret_cast<void*>(0x2);
}

bool buffers_clear() {
    return vox::DriverCallbackInterface::m_sWorkBuffer.size == 0
        && vox::DriverCallbackInterface::m_sWorkBuffer.data == nullptr
        && vox::DriverCallbackInterface::m_sMixingBuffer.size == 0
        && vox::DriverCallbackInterface::m_sMixingBuffer.data == nullptr;
}

} // namespace

// The only external dependency of the recovered TU.  A real implementation
// lives in driver_callback_detect_neon.cpp; this stub isolates Init.
bool vox::DriverCallbackSourceInterface::DetectNeon() {
    ++detect_calls;
    return neon_result;
}

int main() {
    vox::DriverCallbackInterface callback;

    // 1. Clears both buffer references and copies the probe result into the
    //    1-byte NEON flag.
    neon_result = true;
    detect_calls = 0;
    set_dirty();
    callback.Init(nullptr);
    require(buffers_clear(), "Init did not clear both buffer references");
    require(vox::DriverCallbackSourceInterface::enableNeon,
            "enableNeon not set from a true probe result");
    require(detect_calls == 1, "probe not consulted exactly once");

    // 2. The flag tracks the probe, not the previous value.
    neon_result = false;
    detect_calls = 0;
    set_dirty();
    callback.Init(nullptr);
    require(buffers_clear(), "Init did not clear buffers on the false path");
    require(!vox::DriverCallbackSourceInterface::enableNeon,
            "enableNeon not set from a false probe result");
    require(detect_calls == 1, "probe not consulted exactly once on the false path");

    // 3. The void* argument is unused: never read, never dereferenced.
    int local = 0;
    void* const arguments[] = {nullptr, &local, reinterpret_cast<void*>(0xDEADBEEF)};
    for (void* argument : arguments) {
        neon_result = true;
        detect_calls = 0;
        set_dirty();
        callback.Init(argument);
        require(buffers_clear(), "Init behaved differently for an argument value");
        require(vox::DriverCallbackSourceInterface::enableNeon,
                "enableNeon depended on the argument value");
        require(detect_calls == 1, "probe call count depended on the argument value");
    }

    // 4. `this` is unused: two distinct objects give the same result.  (The
    //    reference never reads r0, so this holds for any receiver.)
    vox::DriverCallbackInterface other;
    neon_result = false;
    detect_calls = 0;
    set_dirty();
    callback.Init(nullptr);
    require(buffers_clear(), "Init via first object did not clear buffers");
    set_dirty();
    other.Init(nullptr);
    require(buffers_clear(), "Init via second object did not clear buffers");
    require(!vox::DriverCallbackSourceInterface::enableNeon,
            "enableNeon differed between receivers");
    require(detect_calls == 2, "probe call count differed between receivers");

    // 5. Nothing else in the recovered static block is written: the reference's
    //    stores start at the work buffer, so the listener parameters and the 3D
    //    simulation flag must survive.
    unsigned char listener_before[48];
    std::memset(vox::DriverCallbackSourceInterface::s_listenerParameters, 0xA5,
                sizeof listener_before);
    std::memcpy(listener_before, vox::DriverCallbackSourceInterface::s_listenerParameters,
                sizeof listener_before);
    vox::DriverCallbackSourceInterface::s_enable3dSimulation = 0x12345678;
    neon_result = true;
    detect_calls = 0;
    set_dirty();
    callback.Init(nullptr);
    require(std::memcmp(listener_before,
                        vox::DriverCallbackSourceInterface::s_listenerParameters,
                        sizeof listener_before) == 0,
            "Init modified s_listenerParameters");
    require(vox::DriverCallbackSourceInterface::s_enable3dSimulation == 0x12345678,
            "Init modified s_enable3dSimulation");
    require(vox::DriverCallbackSourceInterface::enableNeon,
            "enableNeon not written in the isolation scenario");

    // 6. Repeated calls reset again after the buffers are refilled.
    set_dirty();
    detect_calls = 0;
    callback.Init(nullptr);
    require(buffers_clear(), "second Init did not reset the buffers");
    require(detect_calls == 1, "second Init consulted the probe unexpectedly");

    std::printf("driver_callback_init behaviour: 6 scenarios passed\n");
    return 0;
}
