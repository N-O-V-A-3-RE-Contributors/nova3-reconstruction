// Source-owned portable reconstruction for task #30. No original code is linked.
// Reference: libNOVA3_neon.so SHA256
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// These interfaces model dependency boundaries; they do not reproduce ARM C++ ABI.
// 0x00819878 (CZipReader::openFile(int)) remains NOT RECONSTRUCTED: the
// inflater, stored-file views, cache and intrusive ownership require more work.
// No substitute or success stub for that function is supplied here.

#include <cstddef>
#include <cstdint>

namespace nova3::recovered::block_00819878 {

// Opaque, source-owned handles. Zero represents an empty intrusive pointer.
struct ZipOperations {
    void* context;
    std::int32_t (*find)(void*, const char*);
    std::uintptr_t (*open_index)(void*, std::int32_t);
};

// 0x00819ed8: CZipReader::openFile(const char*).
// The reference checks exactly -1; other values are forwarded unchanged.
// Callback validity and a valid C string are caller preconditions.
std::uintptr_t open_zip_named(const ZipOperations& operations, const char* name) {
    const auto index = operations.find(operations.context, name);
    if (index == -1) return 0;
    return operations.open_index(operations.context, index);
}

// Recovered wire record: intrusive buffer at +0, byte offset at +4, type at
// +8, component count at +12, stride at +14. Handles are ARM32 tokens, not
// host pointers. The original constructs this record in uninitialized storage.
struct VertexStream {
    std::uint32_t buffer;
    std::uint32_t byte_offset;
    std::uint32_t type;
    std::uint16_t components;
    std::uint16_t stride;
};
static_assert(sizeof(VertexStream) == 16);
static_assert(offsetof(VertexStream, byte_offset) == 4);
static_assert(offsetof(VertexStream, type) == 8);
static_assert(offsetof(VertexStream, components) == 12);
static_assert(offsetof(VertexStream, stride) == 14);

struct MeshStreams {
    std::uint32_t buffer; // SMesh+0x34
    const std::uint32_t* strides; // SMesh+0x0c
    const std::uint32_t* offsets; // SMesh+0x14
    const std::uint32_t* types; // SMesh+0x1c
    const std::uint32_t* components; // SMesh+0x24
};

struct BufferReferences {
    void* context;
    void (*retain)(void*, std::uint32_t);
    void (*release)(void*, std::uint32_t);
};

// 0x0081bd20: scene::{anonymous}::addStream, portable valid-input domain.
// attribute indexes all four arrays; output has room for index+1 records.
// output may be null only when index==0. Records must not alias input arrays.
// Driver and SBufferConfig parameters are unused by the observed routine.
// Ownership of the newly constructed output reference passes to the caller.
std::uint8_t add_stream(const MeshStreams& mesh, std::uint32_t minimum_vertex,
                        std::uint32_t attribute, VertexStream* output,
                        std::uint8_t index, const BufferReferences& references) {
    const auto buffer = mesh.buffer;
    if (buffer != 0) references.retain(references.context, buffer);

    const auto stride = mesh.strides[attribute];
    const auto offset = minimum_vertex * stride + mesh.offsets[attribute];
    const auto type = mesh.types[attribute];
    const auto components = mesh.components[attribute];
    if (output != nullptr) {
        auto& stream = output[index];
        if (buffer != 0) references.retain(references.context, buffer);
        stream.buffer = buffer;
        stream.stride = static_cast<std::uint16_t>(stride);
        stream.type = type;
        stream.components = static_cast<std::uint16_t>(components);
        stream.byte_offset = offset; // multiplication/addition wrap modulo 2^32
    }
    if (buffer != 0) references.release(references.context, buffer);
    return static_cast<std::uint8_t>(index + 1u);
}

// 0x008ca0c0: glf::AndroidUpdate. The reference reads glf::gApp once,
// returns when null, otherwise calls its vtable slot +0x24 with that object.
// A callback makes the observed dispatch portable without guessing CApp layout.
struct AndroidApplication {
    void* context;
    void (*update)(void*);
};

void android_update(const AndroidApplication* application) {
    if (application != nullptr) application->update(application->context);
}

} // namespace nova3::recovered::block_00819878
