// Standalone build command is recorded in the task evidence JSON.
// The test includes the implementation because this task permits no header or
// CMake edits. Do not also link block_002ad444.cpp into this test executable.
// Optional local differential test (no binary data is emitted):
//   /tmp/nova29-test --oracle-script | /path/to/venv/bin/python - LIBRARY /tmp/nova29-test
// Original executable bytes are used only by this optional research oracle.
// Production source has no original-library or emulator dependency. Helpers
// modeled by the oracle are enumerated below; renderer/ABI parity is excluded.
#include "../../../recovered/libNOVA3_neon/block_002ad444.cpp"

#include <cstring>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <vector>

using nova3::recovered::libnova3_neon::get_hash_name;
using namespace nova3::recovered::libnova3_neon;

static std::string unhex(const std::string& text) {
    if (text.size() % 2 != 0) throw std::runtime_error("odd hex length");
    auto digit = [](char c) -> unsigned {
        if (c >= '0' && c <= '9') return unsigned(c - '0');
        if (c >= 'a' && c <= 'f') return unsigned(c - 'a' + 10);
        throw std::runtime_error("invalid hex");
    };
    std::string bytes;
    for (std::size_t i = 0; i < text.size(); i += 2)
        bytes.push_back(static_cast<char>(digit(text[i]) * 16 + digit(text[i + 1])));
    return bytes;
}

static std::string hex(std::string_view bytes) {
    std::string result;
    for (unsigned char byte : bytes) {
        result += "0123456789abcdef"[byte >> 4];
        result += "0123456789abcdef"[byte & 15];
    }
    return result;
}

using Events = std::vector<std::pair<std::string, std::uint32_t>>;

class RecordingTexture final : public Texture {
public:
    RecordingTexture(std::uint8_t levels, Events& events) : levels_(levels), events_(events) {}
    std::uint8_t filter_level_count() const override {
        events_.emplace_back("levels", levels_);
        return levels_;
    }
    void set_mag_filter(std::uint32_t value) override { events_.emplace_back("mag", value); }
    void set_min_filter(std::uint32_t value) override { events_.emplace_back("min", value); }
    void set_anisotropy(float value) override {
        static_assert(sizeof(float) == sizeof(std::uint32_t) &&
                      std::numeric_limits<float>::is_iec559);
        std::uint32_t bits;
        std::memcpy(&bits, &value, sizeof(bits));
        events_.emplace_back("aniso", bits);
    }
private:
    std::uint8_t levels_;
    Events& events_;
};

class RecordingManager final : public TextureManager {
public:
    std::shared_ptr<Texture> next;
    std::string loaded_path, loaded_key;
    unsigned loads = 0;
    std::shared_ptr<Texture> load(std::string_view path, std::string_view key) override {
        ++loads;
        loaded_path = path;
        loaded_key = key;
        return next;
    }
};

static std::string observe_texture(std::string_view path, bool configure,
                                   const TexturePolicy& policy, std::uint8_t levels,
                                   bool available) {
    Events events;
    RecordingManager manager;
    if (available) manager.next = std::make_shared<RecordingTexture>(levels, events);
    auto result = get_texture(path, configure, policy, manager);
    if (result != manager.next || manager.loads != 1)
        throw std::runtime_error("texture identity or manager call count changed");
    std::ostringstream out;
    out << bool(result) << '|' << hex(manager.loaded_path) << '|' << hex(manager.loaded_key);
    for (const auto& [name, value] : events) out << '|' << name << ':' << value;
    return out.str();
}

static constexpr const char* oracle_script = R"PY(
import hashlib, itertools, json, pathlib, random, struct, subprocess, sys
import importlib.metadata
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR)

binary, executable = map(pathlib.Path, sys.argv[1:])
digest = hashlib.sha256(binary.read_bytes()).hexdigest()
if digest != '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf':
    raise RuntimeError('reference SHA256 mismatch')
u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
with binary.open('rb') as stream:
    elf = ELFFile(stream)
    if elf['e_machine'] != 'EM_ARM': raise RuntimeError('not ARM')
    for segment in elf.iter_segments():
        if segment['p_type'] != 'PT_LOAD': continue
        lo = segment['p_vaddr'] & ~4095
        hi = (segment['p_vaddr'] + segment['p_memsz'] + 4095) & ~4095
        u.mem_map(lo, hi - lo)
        u.mem_write(segment['p_vaddr'], segment.data())

u.mem_map(0x2000000, 0x200000)
input_object, output_object, stack, stop = 0x2000000, 0x2000010, 0x21f0000, 0x21ffffc
heap = 0
def put(at, value): u.mem_write(at, struct.pack('<I', value))
def word(at): return struct.unpack('<I', u.mem_read(at, 4))[0]
def read_string(obj):
    data = word(obj)
    return bytes(u.mem_read(data, word(data - 12)))
def write_string(obj, data):
    global heap
    header = heap
    heap += (12 + len(data) + 1 + 15) & ~15
    if heap >= 0x21e0000: raise RuntimeError('synthetic string heap exhausted')
    u.mem_write(header, struct.pack('<III', len(data), len(data), 0) + data + b'\0')
    put(obj, header + 12)

# Addresses are ELF virtual addresses, not Ghidra's +0x10000 rebased addresses.
assign, substr, leak, dispose = 0xed2a0, 0x2beec0, 0x20f4dc, 0xeca5c
helper_hits = {a: 0 for a in (assign, substr, leak, dispose)}
def hook(uc, address, size, user):
    if 0x2af114 <= address < 0x2af2ac: return
    if address not in helper_hits:
        raise RuntimeError('unmodeled control transfer at ' + hex(address))
    helper_hits[address] += 1
    r0, r1, r2, r3 = [uc.reg_read(r) for r in
        (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
    if address == assign:
        write_string(r0, read_string(r1))
    elif address == substr:
        data = read_string(r1)
        if r2 > len(data): raise RuntimeError('native substr out of range')
        write_string(r0, data[r2:r2 + r3])
    # Private synthetic storage has no sharing: leak and disposal have no work.
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
hash_hook = u.hook_add(UC_HOOK_CODE, hook)

def native(name):
    global heap
    heap = 0x2010000
    write_string(input_object, name)
    write_string(output_object, b'old output')
    for reg, value in ((UC_ARM_REG_R0, 0), (UC_ARM_REG_R1, input_object),
        (UC_ARM_REG_R2, output_object), (UC_ARM_REG_SP, stack), (UC_ARM_REG_LR, stop)):
        u.reg_write(reg, value)
    u.emu_start(0x2af114, stop, count=200000)
    if u.reg_read(UC_ARM_REG_PC) != stop: raise RuntimeError('instruction limit')
    result = read_string(output_object)
    if read_string(input_object) != name: raise RuntimeError('input mutated')
    return result

cases = [b'', b'A.TGA', b'dir.ext/FILE', b'.hidden', b'a.b.c', b'/X/',
    b'A\0B.TGA', bytes(range(256)), b'A' * 4096 + b'.TGA']
alphabet = (b'A', b'z', b'.', b'/', b'\\', b'\0', b'\xff')
for length in range(5):
    cases.extend(b''.join(chars) for chars in itertools.product(alphabet, repeat=length))
rng = random.Random(0x2af114)
for _ in range(300):
    cases.append(bytes(rng.randrange(256) for _ in range(rng.randrange(1, 160))))
requests = ''.join(data.hex() + '\n' for data in cases)
process = subprocess.run([str(executable), '--hash-lines'], input=requests,
    text=True, capture_output=True, check=True)
actual = process.stdout.splitlines()
if len(actual) != len(cases): raise RuntimeError('source result count')
transcript = hashlib.sha256()
for i, (data, result) in enumerate(zip(cases, actual)):
    expected = native(data)
    if result != expected.hex():
        raise RuntimeError('source/native disagreement in case ' + str(i) +
            ': input=' + data.hex() + ' source=' + result + ' native=' + expected.hex())
    transcript.update(struct.pack('<I', len(data)) + data)
    transcript.update(struct.pack('<I', len(expected)) + expected)
print(json.dumps(dict(result='PASS', cases=len(cases), binary_sha256=digest,
    transcript_sha256=transcript.hexdigest(), helpers={hex(k): v for k,v in helper_hits.items()},
    unicorn=importlib.metadata.version('unicorn'), pyelftools=importlib.metadata.version('pyelftools'),
    scope='0x2af114 full ARM body; string storage helpers modeled; no COW/ABI/engine equivalence'),
    sort_keys=True))

# getTexture: execute the original body and GetHashName; model external storage,
# reference counts, manager lookup and setter calls. Only DATA is initialized;
# no reference instruction bytes are patched. No rendering is performed here.
u.hook_del(hash_hook)
from unicorn import UC_HOOK_MEM_READ
from unicorn.arm_const import UC_ARM_REG_C1_C0_2, UC_ARM_REG_FPEXC
u.reg_write(UC_ARM_REG_C1_C0_2, u.reg_read(UC_ARM_REG_C1_C0_2) | (0xf << 20))
u.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
texture, device, driver, options, params = 0x2004000, 0x2005000, 0x2006000, 0x2007000, 0x2008000
name_buffer, manager = 0x2002000, 0x2009000
put(texture + 0x10, params)
put(0xd9b960, device)  # g_device
put(device + 8, driver)
put(driver + 0x14c, manager)
events, loads, texture_helpers = [], [], {}
present = False

def cstring(at):
    data = bytearray()
    for i in range(8192):
        byte = bytes(u.mem_read(at + i, 1))
        if byte == b'\0': return bytes(data)
        data.extend(byte)
    raise RuntimeError('unterminated synthetic string')

def texture_hook(uc, at, size, user):
    if 0x2af2ac <= at < 0x2af5c4 or 0x2af114 <= at < 0x2af2ac: return
    if at in helper_hits:
        hook(uc, at, size, user)
        return
    texture_helpers[at] = texture_helpers.get(at, 0) + 1
    r0, r1, r2, r3 = [uc.reg_read(r) for r in
        (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
    if at == 0x10b900:  # custom-allocator string construction from const char*
        write_string(r0, cstring(r1))
    elif at == 0x7b1dec:  # CTextureManager::getTexture, hidden return argument
        if r1 != manager: raise RuntimeError('wrong synthetic texture manager')
        loads.append((cstring(r2), cstring(r3)))
        put(r0, texture if present else 0)
    elif at == 0xbe3900:  # __sync_add_and_fetch_4; single-threaded oracle
        put(r0, (word(r0) + r1) & 0xffffffff)
        uc.reg_write(UC_ARM_REG_R0, word(r0))
    elif at == 0x2b2a58:  # DeviceOptions::Instance
        uc.reg_write(UC_ARM_REG_R0, options)
    elif at == 0x10918c:  # intrusive_ptr<ITexture> temporary destruction
        ptr = word(r0)
        if ptr:
            if ptr != texture: raise RuntimeError('unknown texture pointer')
            put(texture + 4, word(texture + 4) - 1)
        put(r0, 0)
    elif at in (0x77db74, 0x77db24, 0x77dc00):
        if r0 != texture: raise RuntimeError('setter on unknown texture')
        events.append(({0x77db74:'mag', 0x77db24:'min', 0x77dc00:'aniso'}[at], r1))
    else:
        raise RuntimeError('unmodeled texture control transfer at ' + hex(at))
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

def descriptor_read(uc, access, at, size, value, user):
    if at == params + 0x2f and 0x2af2ac <= uc.reg_read(UC_ARM_REG_PC) < 0x2af5c4:
        if size != 1: raise RuntimeError('unexpected descriptor access width')
        events.append(('levels', bytes(uc.mem_read(at, 1))[0]))

u.hook_add(UC_HOOK_CODE, texture_hook)
u.hook_add(UC_HOOK_MEM_READ, descriptor_read)

def native_texture(case):
    global heap, present
    configure, one, test, aniso, levels, opt, present, name = case
    heap = 0x2010000
    events.clear()
    loads.clear()
    put(output_object, 0)
    put(texture + 4, 1)
    put(options + 0x38, opt & 0xffffffff)
    for at, value in ((0xd9b331, one), (0xd9b330, aniso), (0xd9b821, test), (params + 0x2f, levels)):
        u.mem_write(at, bytes([value]))
    u.mem_write(name_buffer, name + b'\0')
    for reg, value in ((UC_ARM_REG_R0, output_object), (UC_ARM_REG_R1, 0x2001000),
        (UC_ARM_REG_R2, name_buffer), (UC_ARM_REG_R3, configure),
        (UC_ARM_REG_SP, stack), (UC_ARM_REG_LR, stop)):
        u.reg_write(reg, value)
    u.emu_start(0x2af2ac, stop, count=200000)
    if u.reg_read(UC_ARM_REG_PC) != stop: raise RuntimeError('texture instruction limit')
    if len(loads) != 1: raise RuntimeError('texture lookup count')
    if word(output_object) != (texture if present else 0): raise RuntimeError('texture result identity')
    if word(texture + 4) != 1: raise RuntimeError('synthetic temporary ownership imbalance')
    path, key = loads[0]
    return '|'.join([str(int(present)), path.hex(), key.hex()] +
                    [name + ':' + str(value) for name, value in events])

names = [b'Textures/Foo.TGA', b'', b'DIR.ext/NAME', b'a/B.C\\D.E', b'.hidden',
         b'A\0B.TGA', b'X.EXT\0/UNUSED', b'\xffAZ.TGA']
device_values = [0, 1, 4, -1, 16777217, -16777217, 2147483647, -2147483648]
texture_cases = []
for i, fields in enumerate(itertools.product((0, 1), (0, 1), (0, 1, 255),
                                             (0, 1), range(256), (0, 1))):
    configure, one, test, aniso, levels, available = fields
    texture_cases.append((configure, one, test, aniso, levels,
                          device_values[i % len(device_values)], available, names[i % len(names)]))
for test in range(256):
    for levels, aniso in itertools.product((0, 2), (0, 1)):
        texture_cases.append((1, 0, test, aniso, levels, 8, 1, names[test % len(names)]))
for opt in device_values:
    for levels in (0, 1, 2, 255):
        texture_cases.append((1, 0, 0, 0, levels, opt, 1, names[0]))
# Long scans need only a few cases; repeating them through the full policy
# matrix would consume time without exercising additional filter branches.
for configure, one, available in itertools.product((0, 1), repeat=3):
    texture_cases.append((configure, one, 0, 0, 2, 4, available, b'A' * 4096 + b'.TGA'))
texture_requests = ''.join(' '.join(map(str, c[:7])) + ' ' + (c[7].hex() or '-') + '\n'
                           for c in texture_cases)
process = subprocess.run([str(executable), '--texture-lines'], input=texture_requests,
                         text=True, capture_output=True, check=True)
actual = process.stdout.splitlines()
if len(actual) != len(texture_cases): raise RuntimeError('texture source result count')
transcript = hashlib.sha256()
for i, (case, result) in enumerate(zip(texture_cases, actual)):
    expected = native_texture(case)
    if result != expected:
        raise RuntimeError('texture source/native disagreement in case ' + str(i) +
                           ': source=' + result + ' native=' + expected)
    transcript.update((' '.join(map(str, case[:7])) + ' ' + case[7].hex() + '\n' + expected + '\n').encode())
print(json.dumps(dict(result='PASS', cases=len(texture_cases), binary_sha256=digest,
    transcript_sha256=transcript.hexdigest(), helpers={hex(k):v for k,v in texture_helpers.items()},
    scope='0x2af2ac/0x2af114 full ARM bodies; synthetic globals, descriptor and manager; '
          'storage/refcounts/options/setter boundaries modeled; no renderer, ABI or cache implementation equivalence'),
    sort_keys=True))
)PY";

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--oracle-script") {
        std::cout << oracle_script;
        return 0;
    }
    if (argc == 2 && std::string_view(argv[1]) == "--hash-lines") {
        for (std::string line; std::getline(std::cin, line);)
            std::cout << hex(get_hash_name(unhex(line))) << '\n';
        return 0;
    }
    if (argc == 2 && std::string_view(argv[1]) == "--texture-lines") {
        unsigned configure, one, mode, aniso, levels, available;
        std::int64_t device_value;
        std::string path;
        while (std::cin >> configure >> one >> mode >> aniso >> levels >> device_value >> available >> path) {
            if (configure > 1 || one > 1 || mode > 255 || aniso > 1 || levels > 255 || available > 1 ||
                device_value < INT32_MIN || device_value > INT32_MAX)
                throw std::runtime_error("invalid texture test request");
            const TexturePolicy policy{bool(one), static_cast<std::uint8_t>(mode),
                                       bool(aniso), static_cast<std::int32_t>(device_value)};
            std::cout << observe_texture(path == "-" ? std::string() : unhex(path), bool(configure),
                                         policy, static_cast<std::uint8_t>(levels), bool(available)) << '\n';
        }
        return std::cin.eof() ? 0 : 2;
    }
    if (argc != 1) return 2;
    const std::vector<std::pair<std::string, std::string>> vectors = {
        {"", ""}, {"A.TGA", "a"}, {"Dir\\Sub/Name.DDS", "name"},
        {"dir.ext/FILE", "file"}, {".hidden", ""}, {"a.b.c", "a.b"},
        {"A", "a"}, {"/X/", ""}, {"a\\b/FILE", "file"},
        {"dir./FILE", "file"}, {".dir/FILE", "file"},
        {"A..B", "a."}, {"X.", "x"}, {"//", ""}, {"\\", ""},
        {"a/B.C\\D.E", "d"}, {"dir.with.dot/a", "a"},
        {std::string("A\0B.TGA", 7), std::string("a\0b", 3)},
        {std::string("\x80\xff" "AZ", 4), std::string("\x80\xff" "az", 4)},
        {"A@Z[", "a@z["}, {std::string(4096, 'A') + ".TGA", std::string(4096, 'a')}
    };
    for (const auto& [input, expected] : vectors) {
        const auto result = get_hash_name(input);
        if (result != expected) {
            std::cerr << "FAIL input=" << hex(input) << " expected=" << hex(expected)
                      << " actual=" << hex(result) << '\n';
            return 1;
        }
    }
    std::cout << "PASS: " << vectors.size() << " explicit filename vectors\n";
    struct TextureVector {
        TexturePolicy policy;
        unsigned levels;
        bool configure, available;
        const char* expected;
    };
    const TextureVector texture_vectors[] = {
        {{}, 2, false, true, "1|4469722f582e544741|78"},
        {{}, 2, true, false, "0|4469722f582e544741|78"},
        {{false, 0, false, 8}, 1, true, true,
         "1|4469722f582e544741|78|mag:1|levels:1|min:1|aniso:1090519040"},
        {{false, 0, false, -1}, 2, true, true,
         "1|4469722f582e544741|78|mag:1|levels:2|min:2|aniso:3212836864"},
        {{false, 0, true, 8}, 0, true, true,
         "1|4469722f582e544741|78|mag:1|levels:0|min:1"},
        {{false, 0, true, 8}, 255, true, true,
         "1|4469722f582e544741|78|mag:1|levels:255|min:4|aniso:1082130432"},
        {{false, 1, true, 8}, 1, true, true,
         "1|4469722f582e544741|78|mag:0|levels:1|min:1"},
        {{false, 255, false, 8}, 2, true, true,
         "1|4469722f582e544741|78|mag:0|levels:2|aniso:0|min:2"},
        {{true, 0, false, 8}, 2, false, true, "1|4469722f582e544741|78"},
        {{true, 0, false, 8}, 2, true, true,
         "1|48656174446973746f72742e746761|78|mag:1|levels:2|min:2|aniso:1090519040"}
    };
    for (const auto& v : texture_vectors) {
        const auto result = observe_texture("Dir/X.TGA", v.configure, v.policy,
                                            static_cast<std::uint8_t>(v.levels), v.available);
        if (result != v.expected) {
            std::cerr << "FAIL texture expected=" << v.expected << " actual=" << result << '\n';
            return 1;
        }
    }
    if (observe_texture(std::string_view("Dir/X.TGA\0/Other", 16), false, {}, 0, true) !=
        "1|4469722f582e544741|78") return 1;
    std::cout << "PASS: 11 explicit texture policy/lookup vectors\n";
}
