// Standalone because this task cannot edit a public header or CMakeLists.txt.
// Ordinary tests use synthetic dependencies only. --oracle PYTHON LOCAL_ELF
// additionally compares the original ARM wrapper traces offline using Unicorn.
// No original executable bytes, text strings or decompiler output are embedded.
#include "../../../recovered/libNOVA3_neon/block_008ca0f0.cpp"

#include <array>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#if defined(__unix__)
#include <cerrno>
#include <cstdio>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace {
using namespace nova3::recovered::android;

void require(bool ok, std::string_view message) {
    if (!ok) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

using Event = std::vector<std::uint64_t>;
struct Spy final : Services {
    unsigned variant;
    ViewState state{0x22000100u, 0x22000200u, 0x22000300u};
    Handle app = 0x22001000u;
    std::array<std::array<char, 16>, 3> buffers{{
        {{'d', '/', 'a', '\0', 'x'}}, {{'h', '/', 'b', '\0', 'y'}},
        {{'t', '/', 'c', '\0', 'z'}}}};
    std::array<const char*, 3> paths{};
    std::vector<Event> events;

    explicit Spy(unsigned v) : variant(v) {
        for (unsigned i = 0; i < 3; ++i)
            paths[i] = (v & (1u << i)) ? nullptr : buffers[i].data();
        if (v & 8u) paths[1] = paths[0];
        if (v & 16u) paths[2] = paths[1];
    }
    std::uint64_t path_id(const char* p) const {
        if (!p) return 0;
        for (unsigned i = 0; i < buffers.size(); ++i)
            if (p == buffers[i].data()) return i + 1;
        require(false, "unexpected path pointer");
        return 0;
    }
    void update() override { events.push_back({0}); }
    void println(Message m, const char* p) override {
        events.push_back({1, static_cast<unsigned>(m), path_id(p)});
        if (variant & 128u) app += 0x100u;
    }
    void set_output_debug_string(bool value) override {
        events.push_back({2, value});
    }
    Handle current_app() override { return app; }
    Handle get_fs(Handle a) override {
        events.push_back({3, a});
        if (variant & 32u) app += 0x100u;
        return a + 0x10000u;
    }
    void set(unsigned which, Handle fs, const char* path) {
        events.push_back({4 + which, fs, path_id(path)});
        if (variant & 64u) app += 0x100u;
    }
    void set_data_dir(Handle fs, const char* p) override { set(0, fs, p); }
    void set_home_dir(Handle fs, const char* p) override { set(1, fs, p); }
    void set_temp_dir(Handle fs, const char* p) override { set(2, fs, p); }
    std::int32_t get_env(Handle vm, Handle* env, std::int32_t version) override {
        events.push_back({7, vm, static_cast<std::uint32_t>(version)});
        *env = 0x22000400u + (variant << 4);
        if (variant & 1u) state.view_class += 0x1000u;
        if (variant & 2u) state.create_view_method += 0x1000u;
        if (variant & 16u) state.vm += 0x1000u;
        // A nonzero status still writes a usable token in this synthetic case.
        return (variant & 4u) ? -2 : 0;
    }
    void call_static_void_method(Handle env, Handle cls, Handle method) override {
        events.push_back({8, env, cls, method});
    }
};

std::string run_cases() {
    std::ostringstream transcript;
    std::size_t count = 0;
    for (unsigned kind = 0; kind < 4; ++kind) {
        for (unsigned variant = 0; variant < 256; ++variant) {
            Spy s(variant);
            const auto original_buffers = s.buffers;
            switch (kind) {
            case 0: step(s); break;
            case 1: set_paths(s, s.paths[0], s.paths[1], s.paths[2]); break;
            case 2: create_view(s, s.state); break;
            case 3: init(s, s.state); break;
            }
            require(s.buffers == original_buffers, "input buffer immutability");
            if (kind == 0) {
                require(s.events == std::vector<Event>{{0}}, "step delegates once");
            } else if (kind == 1) {
                require(s.events.size() == 9, "exactly three logs, lookups and setters");
                const auto log_delta = (variant & 128u) ? 0x300u : 0u;
                const auto call_delta = ((variant & 32u) ? 0x100u : 0u) +
                                        ((variant & 64u) ? 0x100u : 0u);
                for (unsigned i = 0; i < 3; ++i) {
                    const auto app = 0x22001000u + log_delta + i * call_delta;
                    require(s.events[i] == Event{1, i, s.path_id(s.paths[i])},
                            "log ordering and unchanged pointer identity");
                    require(s.events[3 + 2*i] == Event{3, app},
                            "each setter rereads the current application");
                    require(s.events[4 + 2*i] == Event{4+i, app+0x10000u,
                                                       s.path_id(s.paths[i])},
                            "filesystem and path identity");
                }
            } else {
                std::vector<Event> expected;
                if (kind == 3) expected = {{1, 3, 0}, {2, 1}, {1, 4, 0}};
                expected.push_back({7, 0x22000100u, 0x10002u});
                expected.push_back({8, 0x22000400u + (variant << 4),
                    0x22000200u + ((variant & 1u) ? 0x1000u : 0u),
                    0x22000300u + ((variant & 2u) ? 0x1000u : 0u)});
                require(s.events == expected,
                        "JNI version, ignored status, post-GetEnv cache reads and init order");
            }
            transcript << kind << ' ' << variant << ' ';
            // Footer checks state changes as well as dependency calls.
            s.events.push_back({9, s.state.vm, s.state.view_class,
                                 s.state.create_view_method, s.app});
            for (const auto& event : s.events) {
                for (std::size_t i = 0; i < event.size(); ++i)
                    transcript << (i ? "," : "") << event[i];
                transcript << ';';
            }
            transcript << '\n';
            ++count;
        }
    }
    // Initialization is deliberately repeatable: no invented once-only guard.
    Spy repeated(0);
    init(repeated, repeated.state);
    const auto once = repeated.events;
    init(repeated, repeated.state);
    auto twice = once;
    twice.insert(twice.end(), once.begin(), once.end());
    require(repeated.events == twice, "repeated init repeats every operation");
    require(count == 1024, "case count");
    return transcript.str();
}

// This script contains only a harness and dependency models. All original
// instructions are read locally after a mandatory SHA256 check; no reference
// instructions are copied, patched, serialized or used in production.
constexpr const char* oracle = R"PY(
import hashlib, io, json, struct, sys
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import *

blob = open(sys.argv[1], 'rb').read()
digest = hashlib.sha256(blob).hexdigest()
if digest != '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf':
    sys.exit('Reference SHA256 mismatch; refusing oracle')
elf = ELFFile(io.BytesIO(blob))
assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian
segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
u.mem_map(0, (max(s['p_vaddr']+s['p_memsz'] for s in segments)+4095)&~4095)
for s in segments:
    u.mem_write(s['p_vaddr'], s.data())
u.mem_map(0x22000000, 0x20000)
u.mem_map(0x23000000, 0x10000)
u.mem_map(0x3f000000, 0x2000)
def put(a, value): u.mem_write(a, struct.pack('<I', value))
def get(a): return struct.unpack('<I', u.mem_read(a,4))[0]
def reg(n): return u.reg_read(n)
def ret(value=0xdeadbeef):
    u.reg_write(UC_ARM_REG_R0, value)
    u.reg_write(UC_ARM_REG_PC, reg(UC_ARM_REG_LR))
state = 0xda4360
app_global = 0xda44b0
stop = 0x3f000000
getenv_target = 0x3f001000
put(0x22000100, 0x22000000)
put(0x22001100, 0x22000000)
put(0x22000018, getenv_target)
formats = {0xc86d58:0, 0xc86d6c:1, 0xc86d7c:2, 0xc86de8:3, 0xc86df4:4}
dependencies = {0x8ca0c0,0x8da5f8,0x8d6d48,0x8ca584,0x8d853c,
                0x8d86c0,0x8d8528,0xdcd50,getenv_target}
ranges = [(0x8ca0f0,4),(0x8cd7f4,0x7c),(0x8cfaf8,0x48),(0x8cfb44,0x2c)]
events = []
variant = 0
reached = False
def path_id(p):
    if p == 0: return 0
    if p not in (0x22008000,0x22008100,0x22008200):
        raise AssertionError('unexpected path token')
    return (p-0x22008000)//0x100+1
def hook(uc, address, size, user):
    global reached
    if address == stop:
        reached = True
        uc.emu_stop()
        return
    if address not in dependencies:
        assert any(a <= address < a+n for a,n in ranges), 'unexpected control flow'
        return
    r0,r1,r2 = [reg(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2)]
    if address == 0x8ca0c0:
        events.append([0]); ret()
    elif address == 0x8da5f8:
        message = formats[r0]
        events.append([1,message,path_id(r1) if message < 3 else 0])
        if variant & 128: put(app_global,get(app_global)+0x100)
        ret()
    elif address == 0x8d6d48:
        events.append([2,r0]); ret()
    elif address == 0x8ca584:
        events.append([3,r0])
        if variant & 32: put(app_global,get(app_global)+0x100)
        ret(r0+0x10000)
    elif address in (0x8d853c,0x8d86c0,0x8d8528):
        events.append([{0x8d853c:4,0x8d86c0:5,0x8d8528:6}[address],r0,path_id(r1)])
        if variant & 64: put(app_global,get(app_global)+0x100)
        ret()
    elif address == getenv_target:
        events.append([7,r0,r2])
        put(r1,0x22000400+(variant<<4))
        if variant & 1: put(state+0x6c,get(state+0x6c)+0x1000)
        if variant & 2: put(state+0xa0,get(state+0xa0)+0x1000)
        if variant & 16: put(state,get(state)+0x1000)
        ret(0xfffffffe if variant & 4 else 0)
    elif address == 0xdcd50:
        events.append([8,r0,r1,r2]); ret()
u.hook_add(UC_HOOK_CODE,hook)
counts = [0,0,0,0]
transcript = hashlib.sha256()
for line in sys.stdin:
    kind, variant, expected = line.rstrip('\n').split(' ',2)
    kind, variant = int(kind), int(variant)
    events.clear(); reached = False
    put(state,0x22000100); put(state+0x6c,0x22000200); put(state+0xa0,0x22000300)
    put(app_global,0x22001000)
    paths = [0 if variant & (1<<i) else 0x22008000+i*0x100 for i in range(3)]
    if variant & 8: paths[1] = paths[0]
    if variant & 16: paths[2] = paths[1]
    for i in range(3):
        u.mem_write(0x22008000+i*0x100, bytes([97+i,0,120+i])+bytes(13))
    before = bytes(u.mem_read(0x22008000,0x300))
    u.reg_write(UC_ARM_REG_CPSR,0x10)
    for i,r in enumerate((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2)):
        u.reg_write(r,paths[i] if kind == 1 else 0x19700000+i*0x100)
    preserved = (UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,
                 UC_ARM_REG_R8,UC_ARM_REG_R9,UC_ARM_REG_R10,UC_ARM_REG_R11)
    for i,r in enumerate(preserved): u.reg_write(r,0x100+i)
    u.reg_write(UC_ARM_REG_SP,0x23008000)
    u.reg_write(UC_ARM_REG_LR,stop)
    u.emu_start((0x8ca0f0,0x8cd7f4,0x8cfaf8,0x8cfb44)[kind],stop+4,count=1000)
    assert reached, 'instruction limit or missing return'
    assert reg(UC_ARM_REG_SP) == 0x23008000, 'stack not restored'
    assert all(reg(r)==0x100+i for i,r in enumerate(preserved)), 'callee-save changed'
    assert bytes(u.mem_read(0x22008000,0x300)) == before, 'path bytes modified'
    events.append([9,get(state),get(state+0x6c),get(state+0xa0),get(app_global)])
    actual = ''.join(','.join(map(str,e))+';' for e in events)
    assert actual == expected, f'case {kind}/{variant}: boundary trace mismatch'
    transcript.update(line.encode())
    counts[kind] += 1
assert counts == [256]*4, counts
import unicorn, elftools
print(json.dumps({'result':'PASS','cases_by_entry':dict(zip(
    ['0x008ca0f0','0x008cd7f4','0x008cfaf8','0x008cfb44'],counts)),
    'transcript_sha256':transcript.hexdigest(),'reference_sha256':digest,
    'unicorn_version':unicorn.__version__,'pyelftools_version':elftools.__version__,
    'scope':'wrapper calls/arguments/order, mutable globals, stack/register preservation; dependencies modeled'}))
)PY";

int run_oracle(const char* python, const char* library, const std::string& cases) {
#if defined(__unix__)
    // An anonymous temporary file avoids pipe deadlocks and shell interpolation.
    auto* input = std::tmpfile();
    require(input != nullptr, "create oracle input");
    require(std::fwrite(cases.data(), 1, cases.size(), input) == cases.size(),
            "write oracle input");
    require(std::fflush(input) == 0, "flush oracle input");
    std::rewind(input);
    const auto child = fork();
    require(child >= 0, "fork oracle");
    if (child == 0) {
        if (dup2(fileno(input), STDIN_FILENO) == -1) _exit(126);
        execl(python, python, "-c", oracle, library, static_cast<char*>(nullptr));
        _exit(127);
    }
    int status = 0;
    pid_t waited;
    do { waited = waitpid(child, &status, 0); } while (waited == -1 && errno == EINTR);
    std::fclose(input);
    require(waited == child, "wait for oracle");
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
#else
    (void)python; (void)library; (void)cases;
    std::cerr << "Offline oracle subprocess requires a Unix host\n";
    return 2;
#endif
}
} // namespace

int main(int argc, char** argv) {
    if (argc != 1 && !(argc == 2 && std::string_view(argv[1]) == "--emit-cases") &&
        !(argc == 4 && std::string_view(argv[1]) == "--oracle")) return 2;
    const auto cases = run_cases();
    if (argc == 2) std::cout << cases;
    else if (argc == 4) return run_oracle(argv[2], argv[3], cases);
    else std::cout << "PASS: 1024 synthetic wrapper cases plus repeated init; "
                      "original comparison requires --oracle\n";
}
