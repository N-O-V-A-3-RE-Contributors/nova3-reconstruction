// Source-owned recovery of vox::DriverCallbackSourceInterface's period setter.
// Premium libNOVA3_neon.so SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// Symbol: _ZN3vox29DriverCallbackSourceInterface23SetDriverCallbackPeriodEf
// ELF VA [0x009d3af8, 0x009d3b20), 40 B, A32; no calls.
// Independently checked against the retail ELF, not private source correspondence:
// binary32 scaling by 16384, signed 32-bit conversion truncating toward zero,
// then one word written through the pointer slot at 0x00d83a54. That slot
// resolves to MinibusDataGeneratorInterface::s_driverCallbackPeriod at
// 0x00d8c468 (4 B). Its initial .data value is 2147483647.
// No NaN, infinity, or out-of-range conversion semantics are claimed here.
// Host tests do not establish original execution or an instruction-window match.
#include "driver_callback.h"

namespace vox {

std::int32_t MinibusDataGeneratorInterface::s_driverCallbackPeriod = 2147483647;

void DriverCallbackSourceInterface::SetDriverCallbackPeriod(float period) {
    MinibusDataGeneratorInterface::s_driverCallbackPeriod =
        static_cast<std::int32_t>(period * 16384.0f);
}

} // namespace vox
