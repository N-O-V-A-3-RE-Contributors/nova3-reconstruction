// Standalone test: compile this file alone; it includes the implementation.
// Optional local oracle: TEST --oracle-script | PYTHON - REFERENCE TEST ARM_OBJECT
// The emitted script contains no reference code/data. Original bytes are read
// locally, hashed, and executed only inside this separate research test.
#include "../../../recovered/libNOVA3_neon/block_007b13e0.cpp"

#include <cstdint>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <vector>

using namespace nova3::recovered::texture_lookup_007b13e0;

static void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}

static std::string hex(const std::string& bytes) {
    std::string result;
    for (unsigned char c : bytes) {
        result += "0123456789abcdef"[c >> 4];
        result += "0123456789abcdef"[c & 15];
    }
    return result;
}

static std::string unhex(const std::string& text) {
    if (text == "-") return {};
    require(text.size() % 2 == 0, "odd hex length");
    auto digit = [](char c) -> unsigned {
        if (c >= '0' && c <= '9') return unsigned(c - '0');
        if (c >= 'a' && c <= 'f') return unsigned(c - 'a' + 10);
        throw std::runtime_error("invalid hex");
    };
    std::string bytes;
    for (std::size_t i = 0; i < text.size(); i += 2)
        bytes += static_cast<char>(digit(text[i]) * 16 + digit(text[i + 1]));
    return bytes;
}

class RecordingServices final : public LookupServices {
public:
    std::string generated;
    bool cached = false, available = false, load_ok = false;
    TexturePtr cached_texture = std::make_shared<Texture>();
    TexturePtr loaded_texture = std::make_shared<Texture>();
    FilePtr opened = std::make_shared<ReadFile>();
    std::vector<std::string> events;

    std::string get_hash_name(const char* name) override {
        events.push_back("hash:" + hex(name));
        return generated;
    }
    TexturePtr find_texture(const char* key) override {
        events.push_back("find:" + hex(key));
        return cached ? cached_texture : nullptr;
    }
    FilePtr open_file(const char* name) override {
        events.push_back("open:" + hex(name));
        return available ? opened : nullptr;
    }
    TexturePtr get_texture_internal(const FilePtr& file, const std::string& key,
                                    bool flag, const TexturePtr& first,
                                    const TexturePtr& second) override {
        require(file == opened, "wrong file identity");
        require(!flag && !first && !second, "wrong internal arguments");
        events.push_back("load:" + hex(key) + ":0:0:0");
        return load_ok ? loaded_texture : nullptr;
    }
    void report_open_failure(const char* name, unsigned int level) override {
        events.push_back("error:" + hex(name) + ":" + std::to_string(level));
    }
};

static std::string observe(const std::string& name, bool explicit_key,
                            const std::string& key, const std::string& generated,
                            bool cached, bool available, bool load_ok) {
    RecordingServices services;
    services.generated = generated;
    services.cached = cached;
    services.available = available;
    services.load_ok = load_ok;
    const auto texture = get_texture(name.c_str(), explicit_key ? key.c_str() : nullptr,
                                     services);
    require(texture == (cached ? services.cached_texture :
                       (available && load_ok ? services.loaded_texture : nullptr)),
            "result identity mismatch");
    std::string result = cached ? "1" : (texture ? "2" : "0");
    for (const auto& event : services.events) result += "|" + event;
    return result;
}

static void standalone_tests() {
    const unsigned int words[] = {0, 1, 0x7fffffffu, 0x80000000u, 0xffffffffu,
                                  0x01020304u, 0xa5a55a5au};
    unsigned int count = 0;
    for (auto size : words) for (auto offset : words) {
        const PvrDataInfoArm32 info{{0x11223344u, 0x55667788u, 0x99aabbccu}, size, offset};
        require(nova3_007b8974(&info) == size, "file data size");
        require(nova3_007b8984(&info) == offset, "face start offset");
        require(info.opaque_prefix[0] == 0x11223344u &&
                info.opaque_prefix[1] == 0x55667788u &&
                info.opaque_prefix[2] == 0x99aabbccu, "prefix changed");
        ++count;
    }
    require(observe("A.PVR", false, "ignored", "key", true, false, false) ==
            "1|hash:412e505652|find:6b6579", "generated key/cache hit");
    require(observe("A.PVR", true, "", "ignored", true, true, true) ==
            "1|find:", "explicit empty key/cache hit");
    require(observe("A.PVR", true, "KEY", "ignored", false, false, true) ==
            "0|find:4b4559|open:412e505652|error:412e505652:3", "open failure");
    require(observe("A.PVR", false, "ignored", "key", false, true, false) ==
            "0|hash:412e505652|find:6b6579|open:412e505652|load:6b6579:0:0:0",
            "loader failure");
    require(observe("A.PVR", true, "KEY", "ignored", false, true, true) ==
            "2|find:4b4559|open:412e505652|load:4b4559:0:0:0", "loader success");
    require(observe(std::string("A\0ignored", 9), true, std::string("K\0tail", 6),
                    "unused", false, true, true) ==
            "2|find:4b|open:41|load:4b:0:0:0", "C-string termination");
    require(observe("A", false, "", std::string("K\0tail", 6), false, true, true) ==
            "2|hash:41|find:4b|open:41|load:4b007461696c:0:0:0",
            "generated key retains its length in internal call");
    std::cout << "PASS: " << count << " accessor vectors; 7 explicit lookup traces\n";
}

static constexpr const char* oracle_script = R"PY(
import hashlib, importlib.metadata, itertools, json, pathlib, random, struct, subprocess, sys
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)

reference, executable, candidate = map(pathlib.Path, sys.argv[1:])
digest = hashlib.sha256(reference.read_bytes()).hexdigest()
if digest != '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf':
    raise RuntimeError('wrong reference SHA256')
u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
with reference.open('rb') as f:
    elf = ELFFile(f)
    if elf['e_machine'] != 'EM_ARM' or not elf.little_endian or elf.elfclass != 32:
        raise RuntimeError('wrong ELF architecture')
    segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
    end = max(s['p_vaddr'] + s['p_memsz'] for s in segments)
    u.mem_map(0, (end + 4095) & ~4095)
    for s in segments: u.mem_write(s['p_vaddr'], s.data())
    def reference_region(at, size):
        s = next(s for s in segments if s['p_vaddr'] <= at and
                 at + size <= s['p_vaddr'] + s['p_filesz'])
        f.seek(s['p_offset'] + at - s['p_vaddr'])
        return f.read(size)
    references = {at: reference_region(at, size) for at, size in
                  [(0x7b8974, 8), (0x7b8984, 8)]}

# Exact comparison covers only the two accessor function bodies in this object.
# No relocation masking or instruction normalization is performed.
matches = []
with candidate.open('rb') as f:
    obj = ELFFile(f)
    if obj['e_machine'] != 'EM_ARM' or not obj.little_endian or obj.elfclass != 32:
        raise RuntimeError('candidate must be ARM32 little-endian')
    for at in references:
        name = 'nova3_%08x' % at
        symbols = obj.get_section_by_name('.symtab').get_symbol_by_name(name)
        if not symbols or len(symbols) != 1: raise RuntimeError('missing candidate symbol')
        symbol = symbols[0]
        section = obj.get_section(symbol['st_shndx'])
        offset = symbol['st_value'] - section['sh_addr']
        code = section.data()[offset:offset + symbol['st_size']]
        if symbol['st_value'] & 1 or code != references[at]:
            raise RuntimeError('NOT MATCHED: accessor ' + hex(at))
        # Reject candidate relocations overlapping the supplied function body.
        for relsec in obj.iter_sections():
            if relsec['sh_type'] in ('SHT_REL', 'SHT_RELA') and relsec['sh_info'] == symbol['st_shndx']:
                if any(offset <= rel['r_offset'] < offset + len(code) for rel in relsec.iter_relocations()):
                    raise RuntimeError('unresolved relocation in accessor')
        matches.append(dict(address=hex(at), candidate_symbol=name, size=len(code),
            candidate_sha256=hashlib.sha256(code).hexdigest(),
            reference_sha256=hashlib.sha256(references[at]).hexdigest(), exact_bytes=True))
print(json.dumps(dict(accessor_machine_code_comparison=matches), sort_keys=True))

base = 0x2000000
u.mem_map(base, 0x80000)
manager, fs, vtable, open_stub = [base + n for n in (0x1000, 0x2000, 0x3000, 0x4000)]
name_at, key_at, output, texture, file_at = [base + n for n in (0x5000, 0x8000, 0xb000, 0xc000, 0xd000)]
loaded_texture = texture + 0x100
stack, stop = base + 0x70000, base + 0x7f000

def put(at, value): u.mem_write(at, struct.pack('<I', value & 0xffffffff))
def word(at): return struct.unpack('<I', u.mem_read(at, 4))[0]
def cstring(at):
    data = bytearray()
    for i in range(16384):
        c = bytes(u.mem_read(at + i, 1))
        if c == b'\0': return bytes(data)
        data.extend(c)
    raise RuntimeError('synthetic string is not terminated')
def read_string(at):
    data = word(at)
    return bytes(u.mem_read(data, word(data - 12)))
heap = base + 0x10000
def write_string(at, data):
    global heap
    block = heap
    heap += (len(data) + 16 + 3) & ~3
    if heap >= base + 0x60000: raise RuntimeError('synthetic heap overflow')
    u.mem_write(block, struct.pack('<III', len(data), len(data), 0) + data + b'\0')
    put(at, block + 12)
def ret(): u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
events, helpers, state = [], {}, {}

def hook(uc, at, size, user):
    if 0x7b1dec <= at < 0x7b1dec + 0x14c: return
    helpers[at] = helpers.get(at, 0) + 1
    r0, r1, r2, r3 = [uc.reg_read(r) for r in
        (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
    if at == 0x10b900:  # custom string construction; length/storage modeled
        write_string(r0, cstring(r1))
    elif at == 0x79a944:  # manager hash/name policy delegated, not reconstructed
        if r1 != manager: raise RuntimeError('wrong hash manager')
        events.append('hash:' + cstring(r2).hex())
        write_string(r0, state['generated'])
    elif at == 0x7cd538:  # cache service
        if r1 != manager: raise RuntimeError('wrong find manager')
        events.append('find:' + cstring(r2).hex())
        put(r0, texture if state['cached'] else 0)
    elif at == 0x35a334:  # intrusive assignment; ownership excluded
        put(r0, word(r1))
    elif at in (0x7cdde4, 0x10918c, 0xec294, 0xeca5c):
        pass  # texture/file releases, texture temporary destructor, string disposal
    elif at == open_stub:
        if r1 != fs: raise RuntimeError('wrong file system')
        events.append('open:' + cstring(r2).hex())
        put(r0, file_at if state['available'] else 0)
    elif at == 0x7b13e0:  # whole internal loader delegated; never claim recovery
        sp = uc.reg_read(UC_ARM_REG_SP)
        if r1 != manager or r2 != file_at: raise RuntimeError('wrong load receiver/file')
        flag, first, second = word(sp), word(word(sp + 4)), word(word(sp + 8))
        if (flag, first, second) != (0, 0, 0): raise RuntimeError('wrong load arguments')
        events.append('load:' + read_string(r3).hex() + ':0:0:0')
        put(r0, loaded_texture if state['load_ok'] else 0)
    elif at == 0x69a288:  # diagnostic prefix text excluded, filename/level checked
        events.append('error:' + cstring(r1).hex() + ':' + str(r2))
    else: raise RuntimeError('unmodeled control transfer at ' + hex(at))
    ret()

put(manager + 0x60, fs)
put(fs, vtable)
put(vtable + 0xc, open_stub)
hook_id = u.hook_add(UC_HOOK_CODE, hook)

def native(case):
    global events, state, heap
    name, explicit, key, generated, cached, available, load_ok = case
    state = dict(generated=generated, cached=cached, available=available, load_ok=load_ok)
    events, heap = [], base + 0x10000
    u.mem_write(name_at, name + b'\0')
    u.mem_write(key_at, key + b'\0')
    put(output, 0xdeadbeef)  # original must initialize the hidden return storage
    for reg, value in ((UC_ARM_REG_R0, output), (UC_ARM_REG_R1, manager),
                       (UC_ARM_REG_R2, name_at), (UC_ARM_REG_R3, key_at if explicit else 0),
                       (UC_ARM_REG_SP, stack), (UC_ARM_REG_LR, stop)):
        u.reg_write(reg, value)
    u.emu_start(0x7b1dec, stop, count=10000)
    if u.reg_read(UC_ARM_REG_PC) != stop: raise RuntimeError('instruction limit')
    if u.reg_read(UC_ARM_REG_R0) != output: raise RuntimeError('hidden return pointer changed')
    result = {0:'0', texture:'1', loaded_texture:'2'}[word(output)]
    return '|'.join([result] + events)

cases = []
names = [b'', b'A.PVR', b'dir/Mixed.Name.pvr', b'A\0ignored', b'\xff\x80.pvr', b'Z' * 4096]
keys = [(False, b'', b'generated'), (True, b'', b'ignored'),
        (True, b'KEY\0tail', b'ignored'), (False, b'ignored', b'K\0tail')]
for name, (explicit, key, generated), cached, available, load_ok in itertools.product(
        names, keys, (False, True), (False, True), (False, True)):
    cases.append((name, explicit, key, generated, cached, available, load_ok))
rng = random.Random(44)
for _ in range(300):
    def random_bytes(): return bytes(rng.randrange(256) for _ in range(rng.randrange(160)))
    cases.append((random_bytes(), bool(rng.randrange(2)), random_bytes(), random_bytes(),
                  bool(rng.randrange(2)), bool(rng.randrange(2)), bool(rng.randrange(2))))
def token(b): return b.hex() or '-'
requests = ''.join(' '.join([token(n), str(int(e)), token(k), token(g),
                            str(int(c)), str(int(a)), str(int(l))]) + '\n'
                   for n,e,k,g,c,a,l in cases)
process = subprocess.run([str(executable), '--lookup-lines'], input=requests,
                         text=True, capture_output=True, check=True)
results = process.stdout.splitlines()
if len(results) != len(cases): raise RuntimeError('wrong source result count')
transcript = hashlib.sha256()
for i, (case, actual) in enumerate(zip(cases, results)):
    expected = native(case)
    if actual != expected: raise RuntimeError('lookup disagreement at case ' + str(i))
    transcript.update(json.dumps([list(v) if isinstance(v, bytes) else v for v in case],
                                 separators=(',', ':')).encode() + b'\n' + expected.encode() + b'\n')
print(json.dumps(dict(lookup_result='PASS', cases=len(cases),
    transcript_sha256=transcript.hexdigest(), helpers={hex(k):v for k,v in helpers.items()},
    scope='0x7b1dec control flow, arguments and return identity; services/storage modeled; ownership/ABI excluded'),
    sort_keys=True))

# Both native accessor bodies execute without modeled callees, on synthetic data.
u.hook_del(hook_id)
transcript = hashlib.sha256()
words = [0, 1, 0x7fffffff, 0x80000000, 0xffffffff, 0x01020304, 0xa5a55a5a]
pairs = list(itertools.product(words, repeat=2))
pairs += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(1000)]
for size, offset in pairs:
    data = struct.pack('<IIIII', rng.getrandbits(32), rng.getrandbits(32),
                       rng.getrandbits(32), size, offset)
    u.mem_write(output, data)
    for at, expected in ((0x7b8974, size), (0x7b8984, offset)):
        for reg, value in ((UC_ARM_REG_R0, output), (UC_ARM_REG_LR, stop), (UC_ARM_REG_SP, stack)):
            u.reg_write(reg, value)
        u.emu_start(at, stop, count=10)
        if u.reg_read(UC_ARM_REG_PC) != stop or u.reg_read(UC_ARM_REG_R0) != expected:
            raise RuntimeError('native accessor disagreement')
        if bytes(u.mem_read(output, 20)) != data: raise RuntimeError('accessor mutated object')
        transcript.update(struct.pack('<II', at, expected) + data)
print(json.dumps(dict(accessor_native_result='PASS', cases_per_accessor=len(pairs),
    transcript_sha256=transcript.hexdigest(), reference_sha256=digest,
    unicorn=importlib.metadata.version('unicorn'), pyelftools=importlib.metadata.version('pyelftools')),
    sort_keys=True))
)PY";

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--oracle-script") {
            std::cout << oracle_script;
            return 0;
        }
        if (argc == 2 && std::string(argv[1]) == "--lookup-lines") {
            std::string line;
            while (std::getline(std::cin, line)) {
                std::istringstream input(line);
                std::string name, key, generated, extra;
                unsigned int explicit_key, cached, available, loaded;
                require(bool(input >> name >> explicit_key >> key >> generated >> cached >> available >> loaded),
                        "invalid lookup request");
                require(!(input >> extra) && explicit_key <= 1 && cached <= 1 && available <= 1 && loaded <= 1,
                        "invalid flags or trailing data");
                std::cout << observe(unhex(name), explicit_key, unhex(key), unhex(generated),
                                     cached, available, loaded) << '\n';
            }
            return 0;
        }
        require(argc == 1, "unexpected arguments");
        standalone_tests();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
