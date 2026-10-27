// Standalone test: this task cannot edit the build system or add a header.
// Ordinary mode needs only C++17. Optional NOVA3_REFERENCE_ORACLE mode reads a
// caller-supplied local ELF into Unicorn memory; it never writes/extracts code.
// No reference bytes, original data assets, or decompiler exports are embedded.
#include "../../../recovered/libNOVA3_neon/block_0071e468.cpp"
#include <algorithm>
#include <array>
#include <climits>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#ifdef NOVA3_REFERENCE_ORACLE
#include <elf.h>
#include <unicorn/unicorn.h>
#endif

namespace r = nova3::recovered::block_0071e468;
using r::Handle;
constexpr Handle inputFile = 0x10001000, archiveFile = 0x10002000;
constexpr Handle openedFile = 0x10003000, fileSystem = 0x10004000;
constexpr Handle database = 0x10008000, driver = 0x11223344;
constexpr Handle memoryToken = 0x1001f000, nameToken = 0x12345678;
constexpr Handle readerToken = 0x87654321;

void require(bool condition, const std::string& what) {
    if (!condition) throw std::runtime_error(what);
}

struct Event {
    std::string operation;
    std::vector<Handle> args;
    bool operator==(const Event& b) const {
        return operation == b.operation && args == b.args;
    }
};

struct Model final : r::Engine {
    bool zipValid = false, memoryAvailable = false;
    Handle openResult = openedFile;
    std::vector<Event> trace;
    std::map<Handle, int> counts{{inputFile, 1}, {archiveFile, 0}, {openedFile, 0}};
    void event(const std::string& name, std::initializer_list<Handle> args = {}) {
        trace.push_back({name, args});
    }
    Handle openFile(Handle owner, const char* path) override {
        event(std::string("open:") + path, {owner});
        if (openResult) counts[openResult] = 1;
        return openResult;
    }
    void retain(Handle h) override {
        require(h != 0, "retain null");
        event("retain", {h});
        ++counts[h];
    }
    void release(Handle h) override {
        require(h != 0 && counts[h] > 0, "release without ownership");
        event("release", {h});
        --counts[h];
    }
    bool zipIsValid(Handle h) override {
        event("zipValid", {h});
        return zipValid;
    }
    void seekToStart(Handle h) override { event("seek", {h, 0, 0}); }
    Handle createZip(Handle h, const r::ZipOptions& o) override {
        event("allocate", {o.allocationSize});
        event("zipConstruct", {h, o.ignoreCase, o.ignorePaths, o.parameter, o.finalFlag});
        counts[archiveFile] = 0;
        return archiveFile;
    }
    void buildGeometry(void* dest, Handle db, Handle drv, Handle geometry) override {
        event("geometry", {db, drv, geometry});
        const std::array<Handle, 4> opaqueResult{{db, drv, geometry, 0xb17d}};
        std::memcpy(dest, opaqueResult.data(), sizeof(opaqueResult));
    }
    void initializeName(r::ResourceFileState& file, const char* name) override {
        event(std::string("name:") + name);
        require(file.references == 0 && file.directFile == 0,
                "name initialization order");
        require(file.content == 0xa5a5a5a5, "content initialized before name");
        file.name = nameToken;
    }
    bool hasMemory(Handle h) override {
        event("hasMemory", {h});
        return memoryAvailable;
    }
    Handle memory(Handle h) override {
        event("memory", {h});
        return memoryToken;
    }
    void initializeReader(r::ResourceFileState& file, Handle reader) override {
        event("initReader", {reader});
        file.content = readerToken;
    }
    void initializeMemory(r::ResourceFileState& file, Handle memory) override {
        event("initMemory", {memory});
        file.content = memory;
    }
};

enum class Kind { version, readFile, geometry, constructor };
struct Case {
    Kind kind;
    r::ResourceManager manager{fileSystem, 0, 0, 0};
    Handle input = inputFile;
    bool zipValid = false, memoryAvailable = false, directRequested = false;
    Handle openResult = openedFile;
    int geometryCount = 0;
    std::string identifier = "mesh";
};
const std::array<const char*, 7> identifiers{{"mesh", "Mesh", "", "mesh", "last", "\x80", "prefix.long"}};
constexpr Handle recordBase = 0x10009000;

struct Result {
    Handle returned = 0;
    r::ResourceManager manager{};
    std::array<Handle, 4> geometry{};
    r::ResourceFileState file{};
    std::vector<Event> trace;
    std::map<Handle, int> counts;
};

Result sourceRun(const Case& c) {
    Result out;
    out.manager = c.manager;
    Model m;
    m.zipValid = c.zipValid;
    m.memoryAvailable = c.memoryAvailable;
    m.openResult = c.openResult;
    std::memset(&out.file, 0xa5, sizeof(out.file));
    if (c.kind == Kind::version) {
        out.returned = static_cast<Handle>(r::checkVersion(out.manager, c.identifier.c_str(), m));
        require(out.returned == 0 && m.counts[openedFile] == 0, "version result / release");
        require(m.trace.size() == (c.openResult ? 2u : 1u), "version call count");
    } else if (c.kind == Kind::readFile) {
        out.returned = r::getReadFile(out.manager, c.input, m);
        const bool zipped = c.manager.tryZip && c.zipValid;
        require(out.returned == (zipped ? c.openResult : c.input), "readFile result");
        const auto enabled = c.manager.disableZipOnPassthrough && !zipped ? 0 : c.manager.tryZip;
        require(out.manager.tryZip == enabled, "passthrough flag update");
        require(m.counts[archiveFile] == 0, "archive leaked");
        require(m.counts[inputFile] == (zipped || c.input == 0 ? 1 : 2), "input ownership");
        require(m.counts[openedFile] == (zipped && c.openResult ? 1 : 0), "returned ownership");
    } else if (c.kind == Kind::geometry) {
        std::array<r::GeometryEntry, identifiers.size()> records{};
        Handle expected = 0;
        for (std::size_t i = 0; i < identifiers.size(); ++i) {
            records[i] = {identifiers[i], recordBase + static_cast<Handle>(16 * i)};
            if (static_cast<int>(i) < c.geometryCount && expected == 0 && c.identifier == identifiers[i])
                expected = records[i].record;
        }
        const r::GeometryTable table{c.geometryCount, records.data()};
        require(r::constructGeometry(out.geometry.data(), database, driver, table,
                                   c.identifier.c_str(), m) == out.geometry.data(), "geometry destination");
        require(out.geometry[2] == expected && m.trace.size() == 1, "geometry first match / delegate");
    } else {
        require(&r::initializeResourceFile(out.file, out.manager, c.identifier.c_str(),
                    c.input, c.directRequested, m) == &out.file, "constructor return");
        const bool direct = c.directRequested && c.memoryAvailable;
        require(out.file.directFile == (direct ? c.input : 0), "direct file ownership");
        require(out.file.content == (direct ? memoryToken : readerToken), "Init overload");
        require(out.file.references == 0 && out.file.name == nameToken, "constructor base fields");
        const auto* bytes = reinterpret_cast<const unsigned char*>(&out.file);
        const std::array<int, 11> zeroWords{{4,0x18,0x1c,0x24,0x28,0x2c,0x40,0x44,0x50,0x54,0x58}};
        std::array<bool, sizeof(out.file)> written{};
        std::fill(written.begin(), written.begin() + 0x14, true);
        written[0x14] = written[0x48] = true;
        for (const int offset : zeroWords) {
            Handle word;
            std::memcpy(&word, bytes + offset, 4);
            require(word == 0, "constructor zero word");
            std::fill(written.begin() + offset, written.begin() + offset + 4, true);
        }
        require(bytes[0x14] == 0 && bytes[0x48] == 0, "constructor zero flags");
        for (std::size_t i = 0; i < written.size(); ++i)
            if (!written[i]) require(bytes[i] == 0xa5, "constructor changed unknown byte");
        require(m.counts[inputFile] == (direct ? 2 : 1), "constructor input ownership");
        require(m.counts[archiveFile] == 0 && m.counts[openedFile] == 0, "constructor temporary leaked");
    }
    out.trace = m.trace;
    out.counts = m.counts;
    return out;
}

#ifdef NOVA3_REFERENCE_ORACLE
// This harness executes only the four reference bodies. All calls outside them
// are intercepted, validated and modelled. It proves scoped control/ownership
// behavior under these contracts, not ZIP parsing, file Init or geometry output.
class Oracle {
    uc_engine* uc = nullptr;
    uc_hook hook{};
    const Case* current = nullptr;
    Model model;
    std::string failure;
    std::set<Handle> visited;
    Handle globalManagerSlot = 0, originalVtable = 0;
    static constexpr Handle scratch = 0x10000000, manager = 0x10006000;
    static constexpr Handle object = 0x10010000, output = 0x10011000;
    static constexpr Handle inputRef = 0x10012000, path = 0x10013000;
    static constexpr Handle vtable = 0x10005000, table = 0x10008300;
    static constexpr Handle stop = 0x30000000;
    static constexpr Handle openHook = stop + 0x100, seekHook = stop + 0x110;
    static constexpr Handle hasHook = stop + 0x120, memoryHook = stop + 0x130;
    static constexpr Handle stack = 0x20000000, initialSp = stack + 0xff00;

    static void check(uc_err error) {
        if (error != UC_ERR_OK) throw std::runtime_error(uc_strerror(error));
    }
    Handle reg(int n) const { Handle v; check(uc_reg_read(uc, n, &v)); return v; }
    void reg(int n, Handle v) { check(uc_reg_write(uc, n, &v)); }
    Handle word(Handle a) const { Handle v; check(uc_mem_read(uc, a, &v, 4)); return v; }
    void word(Handle a, Handle v) { check(uc_mem_write(uc, a, &v, 4)); }
    void byte(Handle a, std::uint8_t v) { check(uc_mem_write(uc, a, &v, 1)); }
    std::string text(Handle a) const {
        std::string s;
        for (int i = 0; i < 1024; ++i) {
            char c;
            check(uc_mem_read(uc, a + i, &c, 1));
            if (c == 0) return s;
            s += c;
        }
        throw std::runtime_error("unterminated oracle identifier");
    }
    void text(Handle a, const std::string& s) {
        check(uc_mem_write(uc, a, s.c_str(), s.size() + 1));
    }
    void returnToCaller(Handle value) {
        reg(UC_ARM_REG_R0, value);
        reg(UC_ARM_REG_PC, reg(UC_ARM_REG_LR));
    }
    void countToMemory(Handle h) { if (h) word(h + 4, static_cast<Handle>(model.counts[h])); }
    void intercept(Handle a) {
        const Handle r0 = reg(UC_ARM_REG_R0), r1 = reg(UC_ARM_REG_R1);
        const Handle r2 = reg(UC_ARM_REG_R2), r3 = reg(UC_ARM_REG_R3);
        if (a == openHook) {
            const Handle opened = model.openFile(r1, text(r2).c_str());
            word(r0, opened);
            countToMemory(opened);
            returnToCaller(r0);
        } else if (a == seekHook) {
            require(r1 == 0 && r2 == 0, "seek arguments");
            model.seekToStart(r0);
            returnToCaller(1);
        } else if (a == hasHook) {
            returnToCaller(model.hasMemory(r0));
        } else if (a == memoryHook) {
            returnToCaller(model.memory(r0));
        } else if (a == 0x7e59f0) {
            returnToCaller(model.zipIsValid(r0));
        } else if (a == 0x69bf9c) {
            require(r0 == 0x2c, "archive allocation size");
            model.event("allocate", {r0});
            returnToCaller(archiveFile);
        } else if (a == 0x81b854) {
            require(r0 == archiveFile, "ZIP constructor object");
            const Handle sp = reg(UC_ARM_REG_SP);
            model.event("zipConstruct", {word(r1), r2, r3, word(sp), word(sp + 4)});
            returnToCaller(r0);
        } else if (a == 0xbe3900 || a == 0x1088c4) {
            if (a == 0xbe3900) require(r1 == 1, "atomic increment argument");
            model.retain(r0 - 4);
            countToMemory(r0 - 4);
            returnToCaller(word(r0));
        } else if (a == 0xec294) {
            model.release(r0);
            countToMemory(r0);
            returnToCaller(0);
        } else if (a == 0x702f80) {
            const Handle old = word(r0), incoming = word(r1);
            if (incoming) model.retain(incoming);
            if (old) model.release(old);
            countToMemory(incoming);
            countToMemory(old);
            word(r0, incoming);
            returnToCaller(r0);
        } else if (a == 0xd24fc) {
            // strcmp@plt, resolved by llvm-objdump and dynamic relocation name.
            const int compared = text(r0).compare(text(r1));
            returnToCaller(compared == 0 ? 0 : (compared < 0 ? UINT32_MAX : 1));
        } else if (a == 0x723c98) {
            std::array<Handle, 4> result{};
            model.buildGeometry(result.data(), r1, r2, r3);
            check(uc_mem_write(uc, r0, result.data(), sizeof(result)));
            returnToCaller(r0);
        } else if (a == 0x10b900) {
            require(r0 == object + 0xc, "string destination");
            r::ResourceFileState f;
            check(uc_mem_read(uc, object, &f, sizeof(f)));
            model.initializeName(f, text(r1).c_str());
            word(r0, f.name);
            returnToCaller(r0);
        } else if (a == 0x6c1d80 || a == 0x76c3f4) {
            require(r0 == object + 0x10, "File::Init destination");
            r::ResourceFileState f;
            check(uc_mem_read(uc, object, &f, sizeof(f)));
            if (a == 0x6c1d80) model.initializeReader(f, word(r1 + 4));
            else model.initializeMemory(f, r1);
            word(r0, f.content);
            returnToCaller(r0);
        } else {
            const bool permitted =
                (current->kind == Kind::version && a >= 0x71e468 && a < 0x71e4ac) ||
                ((current->kind == Kind::readFile || current->kind == Kind::constructor)
                    && a >= 0x72490c && a < 0x724b3c) ||
                (current->kind == Kind::geometry && a >= 0x73f804 && a < 0x73f898) ||
                (current->kind == Kind::constructor && a >= 0x747828 && a < 0x747988);
            require(permitted, "unexpected reference execution address " + std::to_string(a));
            visited.insert(a);
        }
    }
    static void callback(uc_engine*, std::uint64_t a, std::uint32_t, void* context) {
        auto& self = *static_cast<Oracle*>(context);
        try { self.intercept(static_cast<Handle>(a)); }
        catch (const std::exception& e) {
            self.failure = e.what();
            uc_emu_stop(self.uc);
        }
    }
public:
    explicit Oracle(const char* localElf) {
        std::ifstream f(localElf, std::ios::binary);
        require(bool(f), "cannot open local reference ELF");
        const std::vector<char> data((std::istreambuf_iterator<char>(f)), {});
        require(data.size() >= sizeof(Elf32_Ehdr), "short ELF header");
        Elf32_Ehdr header;
        std::memcpy(&header, data.data(), sizeof(header));
        require(std::memcmp(header.e_ident, ELFMAG, SELFMAG) == 0 &&
                header.e_ident[EI_CLASS] == ELFCLASS32 &&
                header.e_ident[EI_DATA] == ELFDATA2LSB && header.e_machine == EM_ARM &&
                header.e_phentsize == sizeof(Elf32_Phdr), "unsupported ELF");
        std::vector<Elf32_Phdr> loads;
        std::uint64_t end = 0;
        for (unsigned i = 0; i < header.e_phnum; ++i) {
            const std::uint64_t off = header.e_phoff + std::uint64_t(i) * sizeof(Elf32_Phdr);
            require(off + sizeof(Elf32_Phdr) <= data.size(), "short program header");
            Elf32_Phdr p;
            std::memcpy(&p, data.data() + off, sizeof(p));
            if (p.p_type != PT_LOAD) continue;
            require(p.p_filesz <= p.p_memsz && std::uint64_t(p.p_offset) + p.p_filesz <= data.size(),
                    "bad load bounds");
            loads.push_back(p);
            end = std::max(end, std::uint64_t(p.p_vaddr) + p.p_memsz);
        }
        require(end != 0 && end < scratch, "unexpected ELF mapping extent");
        check(uc_open(UC_ARCH_ARM, UC_MODE_ARM, &uc));
        try {
            check(uc_mem_map(uc, 0, (end + 4095) & ~std::uint64_t(4095), UC_PROT_ALL));
            for (const auto& p : loads)
                check(uc_mem_write(uc, p.p_vaddr, data.data() + p.p_offset, p.p_filesz));
            check(uc_mem_map(uc, scratch, 0x40000, UC_PROT_ALL));
            check(uc_mem_map(uc, stack, 0x10000, UC_PROT_ALL));
            check(uc_mem_map(uc, stop, 0x1000, UC_PROT_ALL));
            globalManagerSlot = 0x7478ac + 8 + word(0x74798c);
            originalVtable = 0x747838 + 8 + word(0x747988) + 8;
            check(uc_hook_add(uc, &hook, UC_HOOK_CODE,
                             reinterpret_cast<void*>(callback), this, 1, 0));
        } catch (...) { uc_close(uc); uc = nullptr; throw; }
    }
    Oracle(const Oracle&) = delete;
    Oracle& operator=(const Oracle&) = delete;
    ~Oracle() { if (uc) uc_close(uc); }
    void reportCoverage() const {
        // Instruction ranges exclude literal pools. This is fixture coverage,
        // not proof of complete path coverage or reconstructed dependencies.
        for (const auto range : {std::pair<Handle, Handle>{0x71e468, 0x71e4ac},
                               {0x72490c, 0x724b3c}, {0x73f804, 0x73f898},
                               {0x747828, 0x747988}}) {
            unsigned count = 0;
            for (Handle a : visited) if (a >= range.first && a < range.second) ++count;
            std::cout << "ARM coverage 0x" << std::hex << range.first << std::dec
                      << ": " << count << '/' << (range.second - range.first) / 4
                      << " instruction addresses\n";
        }
    }
    Result run(const Case& c) {
        current = &c;
        model = Model{};
        model.zipValid = c.zipValid;
        model.memoryAvailable = c.memoryAvailable;
        model.openResult = c.openResult;
        failure.clear();
        const std::vector<char> zero(0x40000, 0);
        check(uc_mem_write(uc, scratch, zero.data(), zero.size()));
        check(uc_mem_write(uc, stack, zero.data(), 0x10000));
        for (Handle h : {inputFile, archiveFile, openedFile, fileSystem}) {
            word(h, vtable);
            word(h + 4, h == inputFile ? 1 : 0);
        }
        word(vtable + 0xc, openHook);
        word(vtable + 0x18, seekHook);
        word(vtable + 0x34, memoryHook);
        word(vtable + 0x3c, hasHook);
        word(manager + 0x20, manager + 0x100);
        word(manager + 0x120, fileSystem);
        byte(manager + 0x25, c.manager.tryZip);
        byte(manager + 0x26, c.manager.disableZipOnPassthrough);
        byte(manager + 0x27, c.manager.quantized);
        word(globalManagerSlot, manager);
        word(inputRef, c.input);
        text(path, c.identifier);
        word(database, database + 0x100);
        word(database + 0x110, database + 0x200);
        word(database + 0x220, table);
        word(table + 0x84, static_cast<Handle>(c.geometryCount));
        word(table + 0x88, recordBase);
        for (std::size_t i = 0; i < identifiers.size(); ++i) {
            const auto at = 0x1000a000 + static_cast<Handle>(i * 64);
            text(at, identifiers[i]);
            word(recordBase + static_cast<Handle>(i * 16), at);
        }
        const std::vector<unsigned char> poison(sizeof(r::ResourceFileState), 0xa5);
        check(uc_mem_write(uc, object, poison.data(), poison.size()));
        reg(UC_ARM_REG_CPSR, 0x10);
        const std::array<int, 8> saved{{UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,
                                      UC_ARM_REG_R8,UC_ARM_REG_R9,UC_ARM_REG_R10,UC_ARM_REG_R11}};
        for (std::size_t i = 0; i < saved.size(); ++i) reg(saved[i], 0xabc00000 + static_cast<Handle>(i));
        reg(UC_ARM_REG_SP, initialSp);
        reg(UC_ARM_REG_LR, stop);
        Handle entry = 0;
        if (c.kind == Kind::version) {
            entry = 0x71e468;
            reg(UC_ARM_REG_R0, manager); reg(UC_ARM_REG_R1, path);
        } else if (c.kind == Kind::readFile) {
            entry = 0x72490c;
            reg(UC_ARM_REG_R0, output); reg(UC_ARM_REG_R1, manager); reg(UC_ARM_REG_R2, inputRef);
        } else if (c.kind == Kind::geometry) {
            entry = 0x73f804;
            reg(UC_ARM_REG_R0, output); reg(UC_ARM_REG_R1, database);
            reg(UC_ARM_REG_R2, driver); reg(UC_ARM_REG_R3, path);
        } else {
            entry = 0x747828;
            reg(UC_ARM_REG_R0, object); reg(UC_ARM_REG_R1, path);
            reg(UC_ARM_REG_R2, inputRef); reg(UC_ARM_REG_R3, c.directRequested);
        }
        check(uc_emu_start(uc, entry, stop, 100000, 10000));
        require(failure.empty(), failure);
        require(reg(UC_ARM_REG_PC) == stop && reg(UC_ARM_REG_SP) == initialSp, "oracle return / stack");
        for (std::size_t i = 0; i < saved.size(); ++i)
            require(reg(saved[i]) == 0xabc00000 + static_cast<Handle>(i), "callee-saved register");
        Result out;
        out.manager = c.manager;
        check(uc_mem_read(uc, manager + 0x25, &out.manager.tryZip, 1));
        check(uc_mem_read(uc, manager + 0x26, &out.manager.disableZipOnPassthrough, 1));
        check(uc_mem_read(uc, manager + 0x27, &out.manager.quantized, 1));
        if (c.kind == Kind::version) out.returned = reg(UC_ARM_REG_R0);
        else if (c.kind == Kind::readFile) {
            require(reg(UC_ARM_REG_R0) == output, "readFile hidden return pointer");
            out.returned = word(output);
        } else if (c.kind == Kind::geometry) {
            require(reg(UC_ARM_REG_R0) == output, "geometry hidden return pointer");
            check(uc_mem_read(uc, output, out.geometry.data(), sizeof(out.geometry)));
        } else {
            require(reg(UC_ARM_REG_R0) == object, "constructor this return");
            check(uc_mem_read(uc, object, &out.file, sizeof(out.file)));
            require(out.file.dispatchTag == originalVtable, "original constructor vptr");
            out.file.dispatchTag = 0x4e335246; // Compare state with source-owned tag.
        }
        out.trace = model.trace;
        out.counts = model.counts;
        for (Handle h : {inputFile, archiveFile, openedFile})
            require(word(h + 4) == static_cast<Handle>(model.counts[h]), "reference counter memory");
        return out;
    }
};

void compare(const Case& c, const Result& a, const Result& b) {
    require(a.returned == b.returned, "reference return mismatch");
    require(a.manager.tryZip == b.manager.tryZip &&
            a.manager.disableZipOnPassthrough == b.manager.disableZipOnPassthrough &&
            a.manager.quantized == b.manager.quantized, "reference manager flags mismatch");
    require(a.trace == b.trace, "reference dependency trace mismatch");
    require(a.counts == b.counts, "reference ownership mismatch");
    if (c.kind == Kind::geometry) require(a.geometry == b.geometry, "reference geometry output mismatch");
    if (c.kind == Kind::constructor)
        require(std::memcmp(&a.file, &b.file, sizeof(a.file)) == 0, "reference initialized bytes mismatch");
}
#endif

int main(int argc, char** argv) {
    try {
#ifdef NOVA3_REFERENCE_ORACLE
        require(argc == 2, "usage: test LOCAL_REFERENCE_ELF (verify SHA256 externally first)");
        Oracle oracle(argv[1]);
        unsigned major = 0, minor = 0;
        uc_version(&major, &minor);
        std::cout << "Unicorn " << major << '.' << minor << '\n';
#else
        (void)argv;
        require(argc == 1, "ordinary tests take no reference input");
#endif
        std::array<unsigned, 4> totals{};
        auto test = [&](const Case& c) {
            const Result source = sourceRun(c);
            (void)source;
#ifdef NOVA3_REFERENCE_ORACLE
            try { compare(c, source, oracle.run(c)); }
            catch (const std::exception& e) {
                throw std::runtime_error("kind=" + std::to_string(static_cast<int>(c.kind)) +
                    " case=" + std::to_string(totals[static_cast<int>(c.kind)]) + ": " + e.what());
            }
#endif
            ++totals[static_cast<int>(c.kind)];
        };
        for (const char* name : {"", "resource.bdae", "a/b.bdae"})
            for (Handle opened : {Handle(0), openedFile}) {
                Case c{Kind::version}; c.identifier = name; c.openResult = opened; test(c);
            }
        for (Handle input : {Handle(0), inputFile})
            for (int enabled : {0, 1, 128}) for (int clear : {0, 1, 255})
                for (int quantized : {0, 1, 255}) for (bool valid : {false, true})
                    for (Handle opened : {Handle(0), openedFile}) {
                        if (input == 0 && enabled != 0) continue; // Invalid virtual-call receiver excluded.
                        Case c{Kind::readFile};
                        c.input = input; c.manager.tryZip = static_cast<std::uint8_t>(enabled);
                        c.manager.disableZipOnPassthrough = static_cast<std::uint8_t>(clear);
                        c.manager.quantized = static_cast<std::uint8_t>(quantized);
                        c.zipValid = valid; c.openResult = opened; test(c);
                    }
        for (int count : {INT_MIN, -5, -1, 0, 1, 2, 3, 5, 7})
            for (const char* id : {"mesh", "Mesh", "MESH", "", "last", "missing", "\x80", "prefix", "prefix.long"}) {
                Case c{Kind::geometry}; c.geometryCount = count; c.identifier = id; test(c);
            }
        for (bool direct : {false, true}) for (bool available : {false, true})
            for (int enabled : {0, 1}) for (int clear : {0, 1}) for (int quantized : {0, 1})
                for (bool valid : {false, true}) for (Handle opened : {Handle(0), openedFile}) {
                    Case c{Kind::constructor};
                    c.directRequested = direct; c.memoryAvailable = available;
                    c.manager.tryZip = static_cast<std::uint8_t>(enabled);
                    c.manager.disableZipOnPassthrough = static_cast<std::uint8_t>(clear);
                    c.manager.quantized = static_cast<std::uint8_t>(quantized);
                    c.zipValid = valid; c.openResult = opened; test(c);
                }
        std::cout << "PASS version=" << totals[0] << " readFile=" << totals[1]
                  << " geometry=" << totals[2] << " constructor=" << totals[3] << '\n';
#ifdef NOVA3_REFERENCE_ORACLE
        oracle.reportCoverage();
        std::cout << "Scoped ARM differential comparison passed; machine-code match NOT MATCHED.\n";
#else
        std::cout << "Host contracts passed; reference comparison not run in this mode.\n";
#endif
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << '\n';
        return 1;
    }
}
