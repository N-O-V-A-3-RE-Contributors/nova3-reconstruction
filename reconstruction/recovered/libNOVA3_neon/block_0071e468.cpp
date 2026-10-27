// Task 40: resource-file / Collada control flow, reconstructed from local ARM
// reference instructions. This is a portable dependency-injected model, NOT a
// binary-compatible replacement class. Engine dependencies remain unrecovered.
// Normal, nonthrowing paths only; valid object graphs and identifiers required.
// No original executable or vtable is a build/runtime dependency.
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace nova3::recovered::block_0071e468 {

// Opaque source-owned handles model target pointers without host pointer casts.
using Handle = std::uint32_t;

struct ResourceManager {
    Handle fileSystem;
    std::uint8_t tryZip;                 // target manager +0x25
    std::uint8_t disableZipOnPassthrough; // +0x26
    std::uint8_t quantized;              // +0x27
};

struct GeometryEntry {
    const char* identifier;
    Handle record; // Opaque original SGeometry identity, not its recovered type.
};

struct GeometryTable {
    std::int32_t count; // Target count is signed, at table +0x84.
    const GeometryEntry* entries; // Target records start at +0x88, stride 16.
};

// Only observed fields are named semantically; other names are byte offsets.
// Unwritten fields and padding must survive initialization unchanged.
struct ResourceFileState {
    Handle dispatchTag; // Source-owned tag, not an original executable pointer.
    std::uint32_t references;
    Handle directFile;
    Handle name;
    Handle content;
    std::uint8_t field14;
    std::uint8_t unknown15[3];
    std::uint32_t field18, field1c, unknown20, field24, field28, field2c;
    std::uint32_t unknown30, unknown34, unknown38, unknown3c;
    std::uint32_t field40, field44;
    std::uint8_t field48;
    std::uint8_t unknown49[7];
    std::uint32_t field50, field54, field58;
};
static_assert(sizeof(ResourceFileState) == 0x5c);
static_assert(offsetof(ResourceFileState, content) == 0x10);
static_assert(offsetof(ResourceFileState, unknown20) == 0x20);
static_assert(offsetof(ResourceFileState, field48) == 0x48);
static_assert(offsetof(ResourceFileState, field58) == 0x58);

struct ZipOptions {
    std::uint32_t allocationSize;
    bool ignoreCase;
    bool ignorePaths;
    std::uint32_t parameter; // Meaning of the constructor's uint argument unknown.
    bool finalFlag;
};

// openFile returns an owning reference. createZip must return a nonnull object
// with zero references; this caller retains it. A null archive is dereferenced
// by the original before its later fallback branch, so that branch is outside
// this model's valid-object contract. initializeReader and initializeMemory
// represent the two distinct res::File::Init overloads, not decoded file data.
// Their internal semantics, virtual dispatch, allocation failures, exception
// unwinding, actual I/O, atomics and the geometry builder are outside this unit.
class Engine {
public:
    virtual ~Engine() = default;
    virtual Handle openFile(Handle owner, const char* path) = 0;
    virtual void retain(Handle object) = 0;
    virtual void release(Handle object) = 0;
    virtual bool zipIsValid(Handle file) = 0;
    virtual void seekToStart(Handle file) = 0;
    virtual Handle createZip(Handle file, const ZipOptions& options) = 0;
    virtual void buildGeometry(void* destination, Handle database, Handle driver,
                               Handle geometry) = 0;
    virtual void initializeName(ResourceFileState& file, const char* name) = 0;
    virtual bool hasMemory(Handle file) = 0;
    virtual Handle memory(Handle file) = 0;
    virtual void initializeReader(ResourceFileState& file, Handle reader) = 0;
    virtual void initializeMemory(ResourceFileState& file, Handle memory) = 0;
};

// 0x0071e468, CResFileManager::checkVersion(char const*), 0x44 bytes.
// The name does not imply a recovered version comparison: there is none here.
int checkVersion(const ResourceManager& manager, const char* path, Engine& engine) {
    const Handle file = engine.openFile(manager.fileSystem, path);
    if (file != 0) engine.release(file);
    return 0;
}

// 0x0072490c, CResFileManager::getReadFile(intrusive_ptr<IReadFile> const&).
// Returns one owning reference, including on the passthrough path.
Handle getReadFile(ResourceManager& manager, Handle input, Engine& engine) {
    if (manager.tryZip != 0 && engine.zipIsValid(input)) {
        engine.seekToStart(input);
        const Handle archive = engine.createZip(input, {0x2c, true, true, 1, false});
        engine.retain(archive);
        const char* entry = manager.quantized != 0
            ? "little_endian_quantized.bdae"
            : "little_endian_not_quantized.bdae";
        const Handle opened = engine.openFile(archive, entry);

        // Preserve the reference operations of temporary assignment and return.
        if (opened != 0) {
            engine.retain(opened);
            engine.release(opened);
            engine.retain(opened);
            engine.release(opened);
        }
        engine.release(archive);
        return opened;
    }
    if (manager.disableZipOnPassthrough != 0 && manager.tryZip != 0)
        manager.tryZip = 0;
    if (input != 0) engine.retain(input);
    return input;
}

// 0x0073f804, CColladaDatabase::constructGeometry(driver, char const*) const.
// The output type is not established: its storage is passed through unchanged.
void* constructGeometry(void* destination, Handle database, Handle driver,
                        const GeometryTable& table, const char* identifier,
                        Engine& engine) {
    Handle selected = 0;
    for (std::int32_t index = 0; index < table.count; ++index) {
        if (std::strcmp(table.entries[index].identifier, identifier) == 0) {
            selected = table.entries[index].record;
            break; // First matching record wins, including duplicate identifiers.
        }
    }
    engine.buildGeometry(destination, database, driver, selected);
    return destination;
}

// 0x00747828, CResFile::CResFile(name, intrusive_ptr<IReadFile> const&, bool).
// dispatchTag belongs to this source implementation. Oracle tests normalize
// the original vptr instead of importing or using an original vtable.
ResourceFileState& initializeResourceFile(ResourceFileState& file,
                                         ResourceManager& manager,
                                         const char* name, Handle input,
                                         bool directRequested, Engine& engine) {
    file.dispatchTag = 0x4e335246; // "N3RF", a source-owned diagnostic tag.
    file.references = 0;
    file.directFile = 0;
    engine.initializeName(file, name);
    file.content = 0;
    file.field14 = 0;
    file.field18 = file.field1c = file.field24 = file.field28 = file.field2c = 0;
    file.field40 = file.field44 = 0;
    file.field48 = 0;
    file.field50 = file.field54 = file.field58 = 0;

    if (directRequested && engine.hasMemory(input)) {
        engine.retain(input);
        file.directFile = input;
        engine.initializeMemory(file, engine.memory(input));
    } else {
        const Handle reader = getReadFile(manager, input, engine);
        if (reader != 0) engine.retain(reader);
        engine.initializeReader(file, reader);
        if (reader != 0) {
            engine.release(reader); // Temporary FileReader's owning reference.
            engine.release(reader); // getReadFile's return temporary.
        }
    }
    return file;
}

} // namespace nova3::recovered::block_0071e468
