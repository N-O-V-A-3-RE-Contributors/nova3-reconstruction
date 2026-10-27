// Source-owned recovery, Premium ELF SHA-256:
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
// NOT MATCHED: portable behavior, not original compiler/ABI reproduction.
#include <array>
#include <cstdint>
#include <cstring>

namespace nova3::recovered::block_000d39f0 {

// Explicit dependencies of JNI_OnLoad (0x000d39f0). The caller supplies the
// persistent environment slot corresponding to original local global `env`.
// get_environment adapts JavaVM::GetEnv (ARM32 invocation-table offset 0x18).
// This interface is deliberately not an exported, drop-in Android JNI ABI.
struct VmServices {
    void (*set_voice_vm)(void*);
    void (*set_billing_vm)(void*);
    std::int32_t (*get_environment)(void*, void**, std::int32_t);
};

namespace {
constexpr std::array<std::uint32_t, 256> make_crc_table() noexcept {
    std::array<std::uint32_t, 256> table{};
    for (std::uint32_t byte = 0; byte < table.size(); ++byte) {
        auto remainder = byte;
        for (unsigned bit = 0; bit < 8; ++bit) {
            remainder = (remainder >> 1) ^
                        ((remainder & 1u) ? 0xedb88320u : 0u);
        }
        table[byte] = remainder;
    }
    return table;
}
constexpr auto crc_table = make_crc_table();
} // namespace

// Preconditions: all service pointers are valid, and environment is a writable
// persistent slot. Calls occur even when GetEnv subsequently reports failure.
extern "C" std::int32_t nova3_recovered_on_load(
    void* vm, void** environment, const VmServices* services) {
    constexpr std::int32_t jni_version_1_4 = 0x00010004;
    services->set_voice_vm(vm);
    services->set_billing_vm(vm);
    const auto status = services->get_environment(vm, environment, jni_version_1_4);
    return status == 0 ? jni_version_1_4 : -1;
}

// ComputeCRC(unsigned char*, int), 0x000de2cc. Valid domain: nonnegative
// length and that many readable bytes. Null is permitted only for length zero.
// Unlike the engine's checked CRC API, this does not accept null/nonempty input.
extern "C" std::uint32_t nova3_recovered_compute_crc(
    const std::uint8_t* data, std::int32_t length) noexcept {
    auto remainder = std::uint32_t{0xffffffffu};
    for (auto count = static_cast<std::uint32_t>(length); count != 0; --count) {
        remainder = crc_table[(remainder ^ *data++) & 0xffu] ^ (remainder >> 8);
    }
    return ~remainder;
}

// CCustomFileSystem::UseTextureETC(), 0x0029e7b0. This is a setter, not a
// predicate. The otherwise unknown object layout is kept opaque. Preconditions:
// object refers to at least 80 writable bytes in the original ARM32 layout.
extern "C" void nova3_recovered_use_texture_etc(void* object) noexcept {
    // Encode little-endian explicitly, avoiding alignment/aliasing assumptions.
    constexpr std::uint8_t etc_mode[4] = {4, 0, 0, 0};
    std::memcpy(static_cast<std::uint8_t*>(object) + 76, etc_mode, sizeof etc_mode);
}

// 0x002a4398, CCustomColladaFactory::createMaterialRenderer: NOT RECONSTRUCTED.
// Material/technique creation, intrusive ownership, virtual driver calls and
// unwind dependencies are unresolved. No substitute renderer or success stub.

} // namespace nova3::recovered::block_000d39f0
