// Source-owned recovery from Premium ELF SHA256
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
#include "block_000d9968/interface.h"

namespace nova3::recovered::block_000d9968 {

// JNI nativeSendData: discard env and receiver, forward the third argument.
extern "C" Word nova3_000d9968(Environment*, Object, Object data) {
    return nova3_iab_send_data_dependency(data);
}

extern "C" Word nova3_000da9e4() {
    return nova3_iab_item_array_dependency();
}

extern "C" Word nova3_000da9e8(const char* id, StoreItem& item) {
    return nova3_iab_item_by_id_dependency(id, item);
}

// Original _JNIEnv::CallObjectMethod at 0xda9ec; ELF symbol size 0x34
// (52 decimal), not 34 decimal. Construct a va_list and dispatch through V.
Object Environment::call_object_method(Object object, Method method, ...) {
    va_list arguments;
    va_start(arguments, method);
    Object result = functions->call_object_method_v(this, object, method, arguments);
    va_end(arguments);
    return result;
}

} // namespace nova3::recovered::block_000d9968
