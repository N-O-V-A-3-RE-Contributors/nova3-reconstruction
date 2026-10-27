// Task #42, libNOVA3_neon.so SHA256
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// Source-owned portable orchestration recovered from four ARM entry points.
// NOT MATCHED: these APIs do not reproduce the original JNI/C++ binary ABI.
// External engine, console, filesystem and JNI operations are dependencies,
// not success stubs. Exact console text and their implementations are excluded.
// See coordination/evidence/functions-d6f72b681eb3f205d2a352.json for proof scope.

#include <cstdint>

namespace nova3::recovered::android {

// Opaque source-side tokens; no original object layout is imposed on the host.
using Handle = std::uintptr_t;

enum class Message {
    data_path,       // Reference format at ELF VA 0x00c86d58, one string argument.
    home_path,       // 0x00c86d6c, one string argument.
    temporary_path,  // 0x00c86d7c, one string argument.
    init_before_debug_output, // 0x00c86de8, no arguments.
    init_after_debug_output   // 0x00c86df4, no arguments.
};

struct ViewState {
    Handle vm;
    Handle view_class;
    Handle create_view_method;
};

class Services {
public:
    virtual ~Services() = default;
    virtual void update() = 0;
    virtual void println(Message message, const char* argument) = 0;
    virtual void set_output_debug_string(bool enabled) = 0;
    // Models a read of the current app global, not a cached object snapshot.
    virtual Handle current_app() = 0;
    virtual Handle get_fs(Handle app) = 0;
    virtual void set_data_dir(Handle fs, const char* path) = 0;
    virtual void set_home_dir(Handle fs, const char* path) = 0;
    virtual void set_temp_dir(Handle fs, const char* path) = 0;
    // The supported contract requires writing a usable environment token.
    // The original ignores the status; it does not attach a thread or recover
    // from a failed GetEnv that leaves its stack output slot uninitialized.
    virtual std::int32_t get_env(Handle vm, Handle* environment,
                                 std::int32_t version) = 0;
    virtual void call_static_void_method(Handle environment, Handle view_class,
                                         Handle method) = 0;
};

// 0x008ca0f0 (4 bytes): JNI step tail-delegates to glf::AndroidUpdate.
void step(Services& services) {
    services.update();
}

// 0x008cd7f4 (0x8c bytes): log all paths before any filesystem access.
// The original forwards pointers unchanged, including null/aliased pointers;
// handling those pointers is the responsibility of the external dependencies.
void set_paths(Services& services, const char* data, const char* home,
               const char* temporary) {
    services.println(Message::data_path, data);
    services.println(Message::home_path, home);
    services.println(Message::temporary_path, temporary);
    services.set_data_dir(services.get_fs(services.current_app()), data);
    services.set_home_dir(services.get_fs(services.current_app()), home);
    services.set_temp_dir(services.get_fs(services.current_app()), temporary);
}

// 0x008cfaf8 (0x4c bytes): JavaVM::GetEnv slot 6, JNI_VERSION_1_2;
// followed by JNIEnv::CallStaticVoidMethod with no Java method arguments.
void create_view(Services& services, ViewState& state) {
    Handle environment; // Written by get_env, as required by the contract above.
    (void)services.get_env(state.vm, &environment, 0x00010002);
    // Read cached class/method after GetEnv: its callback can change the state.
    services.call_static_void_method(environment, state.view_class,
                                      state.create_view_method);
}

// 0x008cfb44 (0x34 bytes): console output, enable debug output, console
// output, then tail-delegate to AndroidCreateView. No once-only guard exists.
void init(Services& services, ViewState& state) {
    services.println(Message::init_before_debug_output, nullptr);
    services.set_output_debug_string(true);
    services.println(Message::init_after_debug_output, nullptr);
    create_view(services, state);
}

} // namespace nova3::recovered::android
