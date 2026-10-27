// Standalone: c++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror
// reconstruction/tests/recovered/libNOVA3_neon/block_00710368_test.cpp -o /tmp/nova43-test
// /tmp/nova43-test
// Optional reproducible local oracle; no reference bytes are emitted:
// /tmp/nova43-test --emit-oracle | /path/to/venv/python - /path/to/libNOVA3_neon.so /tmp/nova43-test [/tmp/nova43.arm.o]
// Requires locally installed pyelftools and Unicorn. Never downloads anything.
#include "../../../recovered/libNOVA3_neon/block_00710368.cpp"
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>

using namespace nova3::recovered::block_00710368;
namespace {
unsigned checks = 0;
void require(bool condition) {
    ++checks;
    if (!condition) {
        std::fprintf(stderr, "FAIL: check %u\n", checks);
        std::exit(1);
    }
}
constexpr const char* names[] = {"alpha", "Beta", "", "alpha", "\200id", "alphabet"};
constexpr const char* queries[] = {"alpha", "Beta", "beta", "", "\200id", "alphabet", "alph", "missing"};
constexpr int32_t counts[] = {std::numeric_limits<int32_t>::min(), -3, 0, 1, 2, 3, 4, 5, 6};
constexpr int32_t indices[] = {-3, -1, 0, 1, 7, std::numeric_limits<int32_t>::min(),
                              std::numeric_limits<int32_t>::max()};
constexpr uint32_t bases[] = {0, 0x10000, 0xffffff00, 0xffffffff};

struct Fixture {
    std::array<ImageRecord, 6> images{};
    std::array<MaterialRecord, 6> materials{};
    ColladaRoot root{};
    ResourceHeader header{};
    ResourceFile file{};
    ColladaDatabase database{};
    Fixture() {
        for (unsigned i = 0; i < images.size(); ++i) {
            images[i].id = names[i];
            materials[i].id = names[i];
            for (auto& word : images[i].unknown) word = 0xa5a50000u + i;
            for (auto& word : materials[i].unknown) word = 0x5a5a0000u + i;
        }
        root.images = images.data();
        root.materials = materials.data();
        header.collada = &root;
        file.header = &header;
        database.resource = &file;
    }
};

void vectors(bool emit) {
    Fixture f;
    std::array<unsigned char, sizeof(f)> before{};
    for (const int32_t count : counts) {
        f.root.image_count = count;
        f.root.material_count = count;
        std::memcpy(before.data(), &f, sizeof(f));
        for (unsigned q = 0; q < std::size(queries); ++q) {
            // Separate storage prevents pointer identity from passing the test.
            const std::string query = queries[q];
            int expected = -1;
            for (int i = 0; i < count; ++i) {
                if (query == names[i]) { expected = i; break; }
            }
            auto* image = f.database.getImage(query.c_str());
            auto* material = f.database.getMaterial(query.c_str());
            require(image == (expected < 0 ? nullptr : &f.images[expected]));
            require(material == (expected < 0 ? nullptr : &f.materials[expected]));
            require(std::memcmp(before.data(), &f, sizeof(f)) == 0);
            if (emit) {
                std::cout << "L " << count << ' ' << q << ' '
                          << (material ? material - f.materials.data() : -1) << ' '
                          << (image ? image - f.images.data() : -1) << '\n';
            }
        }
    }
    // Nonpositive counts must avoid reading even the names/array storage.
    f.root.images = nullptr;
    f.root.materials = nullptr;
    // Keep null as a runtime fixture: GCC otherwise warns about an unreachable
    // inlined strcmp(nullptr) despite the nonpositive count guarding that call.
    const char* volatile absent_name = nullptr;
    for (const int32_t count : {std::numeric_limits<int32_t>::min(), -1, 0}) {
        f.root.image_count = f.root.material_count = count;
        require(f.database.getImage(absent_name) == nullptr);
        require(f.database.getMaterial(absent_name) == nullptr);
    }

    GeometryRecord geometries[9]{};
    f.root.geometries = geometries + 3;
    for (int i = -3; i <= 5; ++i)
        require(f.database.getGeometry(i) == &geometries[i + 3]);
    for (const auto base : bases) {
        f.root.geometries = reinterpret_cast<GeometryRecord*>(uintptr_t(base));
        for (const auto index : indices) {
            const auto got = uint32_t(reinterpret_cast<uintptr_t>(f.database.getGeometry(index)));
            require(got == base + uint32_t(index) * 16u);
            if (emit) std::cout << "G " << base << ' ' << index << ' ' << got << '\n';
        }
    }
    for (const auto first : bases) for (const auto second : bases) {
        const auto result = checkVersion(reinterpret_cast<const void*>(uintptr_t(first)),
                                         reinterpret_cast<const void*>(uintptr_t(second)));
        require(result == 0);
        if (emit) std::cout << "V " << first << ' ' << second << ' ' << result << '\n';
    }
}

// Embedded to keep the entire repeatable oracle in this task's allowed paths.
// The reference is a local test oracle only, never a runtime/build dependency.
constexpr const char* oracle = R"NOVA43PY(
import hashlib, io, json, pathlib, struct, subprocess, sys
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *

binary, candidate = map(pathlib.Path, sys.argv[1:3])
expected = '84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf'
original = binary.read_bytes()
assert hashlib.sha256(original).hexdigest() == expected, 'reference hash mismatch'
elf = ELFFile(io.BytesIO(original))
assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian
u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
for segment in elf.iter_segments():
    if segment['p_type'] == 'PT_LOAD':
        lo = segment['p_vaddr'] & ~4095
        end = (segment['p_vaddr'] + segment['p_memsz'] + 4095) & ~4095
        u.mem_map(lo, end-lo)
        u.mem_write(segment['p_vaddr'], segment.data())
data, stack, stop = 0x2000000, 0x3000000, 0x4000000
u.mem_map(data, 0x10000)
u.mem_map(stack, 0x10000)
u.mem_map(stop, 0x1000)
db, file, header, root = data, data+0x100, data+0x200, data+0x300
materials, images, strings, query = data+0x1000, data+0x2000, data+0x3000, data+0x4000
def put(address, value): u.mem_write(address, struct.pack('<I', value & 0xffffffff))
put(db, file); put(file+0x10, header); put(header+0x20, root)
names = [b'alpha', b'Beta', b'', b'alpha', b'\x80id', b'alphabet']
queries = [b'alpha', b'Beta', b'beta', b'', b'\x80id', b'alphabet', b'alph', b'missing']
for i, name in enumerate(names):
    address = strings + 0x100*i
    u.mem_write(address, name+b'\0')
    put(materials+36*i, address)
    put(images+16*i, address)
def cstring(address):
    value = bytearray()
    for i in range(128):
        byte = u.mem_read(address+i, 1)[0]
        if byte == 0: return bytes(value)
        value.append(byte)
    raise AssertionError('unterminated synthetic string')
comparisons = []
def code_hook(uc, address, size, context):
    if address == 0xd24fc: # strcmp PLT slot, checked against ELF relocation below
        a, b = uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)
        comparisons.append((a,b))
        left, right = cstring(a), cstring(b)
        uc.reg_write(UC_ARM_REG_R0, ((left > right) - (left < right)) & 0xffffffff)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
u.hook_add(UC_HOOK_CODE, code_hook)
def write_hook(uc, access, address, size, value, context):
    assert stack <= address and address+size <= stack+0x10000, 'non-stack reference write'
u.hook_add(UC_HOOK_MEM_WRITE, write_hook)
callee_saved = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
def run(address, first, second):
    comparisons.clear()
    for i, register in enumerate(callee_saved): u.reg_write(register, 0x55550000+i)
    u.reg_write(UC_ARM_REG_R0, first & 0xffffffff)
    u.reg_write(UC_ARM_REG_R1, second & 0xffffffff)
    u.reg_write(UC_ARM_REG_SP, stack+0xf000)
    u.reg_write(UC_ARM_REG_LR, stop)
    u.emu_start(address, stop, count=1000)
    assert u.reg_read(UC_ARM_REG_PC) == stop, 'instruction budget exceeded'
    assert u.reg_read(UC_ARM_REG_SP) == stack+0xf000
    for i, register in enumerate(callee_saved): assert u.reg_read(register) == 0x55550000+i
    return u.reg_read(UC_ARM_REG_R0)

plt = elf.get_section_by_name('.plt')
rel = elf.get_section_by_name('.rel.plt')
slot = (0xd24fc - plt['sh_addr'] - 20)//12
assert plt['sh_addr']+20+12*slot == 0xd24fc
assert elf.get_section(rel['sh_link']).get_symbol(rel.get_relocation(slot)['r_info_sym']).name == 'strcmp'

output = subprocess.check_output([str(candidate), '--vectors'], text=True)
cases = {'lookup': 0, 'geometry': 0, 'version': 0, 'null_nonpositive_lookup': 0}
total_comparisons = 0
for line in output.splitlines():
    kind, *values = line.split(); values = list(map(int, values))
    if kind == 'L':
        count, q, want_material, want_image = values
        put(root+0x78, count); put(root+0x7c, materials)
        put(root+0x68, count); put(root+0x6c, images)
        u.mem_write(query, queries[q]+b'\0')
        for address, base, stride, want in [(0x7146ac, materials, 36, want_material),
                                           (0x71476c, images, 16, want_image)]:
            snapshot = bytes(u.mem_read(data, 0x10000))
            got = run(address, db, query)
            assert got == (0 if want == -1 else base+stride*want), (line,hex(address),hex(got))
            assert bytes(u.mem_read(data, 0x10000)) == snapshot
            steps = 0 if count <= 0 else (count if want == -1 else want+1)
            assert comparisons == [(strings+0x100*i, query) for i in range(steps)]
            total_comparisons += steps
            cases['lookup'] += 1
    elif kind == 'G':
        base, index, want = values
        put(root+0x88, base)
        assert run(0x710368, db, index) == want, line
        cases['geometry'] += 1
    elif kind == 'V':
        first, second, want = values
        assert run(0x710700, first, second) == want == 0
        cases['version'] += 1
    else: raise AssertionError(kind)
for count in [-2147483648, -1, 0]:
    put(root+0x68, count); put(root+0x78, count)
    put(root+0x6c, 0); put(root+0x7c, 0)
    for address in [0x7146ac, 0x71476c]:
        assert run(address, db, 0) == 0 and comparisons == []
        cases['null_nonpositive_lookup'] += 1
assert cases == {'lookup': 144, 'geometry': 28, 'version': 16, 'null_nonpositive_lookup': 6}
result = {'reference_sha256': expected, 'cases': cases, 'strcmp_calls': total_comparisons,
          'host_vectors_sha256': hashlib.sha256(output.encode()).hexdigest(),
          'scope': 'Original ARM control flow vs host candidate; strcmp modeled by Python byte-string comparison. Synthetic data only. No engine/asset integration.'}

if len(sys.argv) == 4:
    obj_data = pathlib.Path(sys.argv[3]).read_bytes()
    obj = ELFFile(io.BytesIO(obj_data))
    assert obj['e_type'] == 'ET_REL' and obj['e_machine'] == 'EM_ARM'
    assert obj.elfclass == 32 and obj.little_endian
    table = obj.get_section_by_name('.symtab')
    comparison = []
    for address, size, fragment in [(0x710368,24,'getGeometry'), (0x710700,8,'checkVersion'),
                                     (0x7146ac,96,'getMaterial'), (0x71476c,96,'getImage')]:
        symbols = [s for s in table.iter_symbols() if s['st_info']['type']=='STT_FUNC' and fragment in s.name]
        assert len(symbols) == 1
        symbol = symbols[0]; section = obj.get_section(symbol['st_shndx'])
        offset = symbol['st_value']-section['sh_addr']
        code = section.data()[offset:offset+symbol['st_size']]
        segment = next(s for s in elf.iter_segments() if s['p_type']=='PT_LOAD' and s['p_vaddr'] <= address < s['p_vaddr']+s['p_filesz'])
        reference = original[segment['p_offset']+address-segment['p_vaddr']:][:size]
        relocations = []
        for relsec in obj.iter_sections():
            if relsec['sh_type'] in ('SHT_REL','SHT_RELA') and relsec['sh_info']==symbol['st_shndx']:
                relocations.extend(r['r_offset'] for r in relsec.iter_relocations()
                                   if offset <= r['r_offset'] < offset+symbol['st_size'])
        equal = code == reference and not relocations
        comparison.append({'address':hex(address), 'reference_size':size, 'candidate_size':len(code),
                           'reference_code_sha256':hashlib.sha256(reference).hexdigest(),
                           'candidate_code_sha256':hashlib.sha256(code).hexdigest(),
                           'candidate_relocations_in_section':relocations,
                           'matching':'MATCHED' if equal else 'NOT MATCHED'})
    result['arm_object_sha256'] = hashlib.sha256(obj_data).hexdigest()
    result['raw_symbol_comparison'] = comparison
print(json.dumps(result, indent=2))
)NOVA43PY";
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--emit-oracle") {
        std::cout << oracle;
    } else if (argc == 2 && std::string(argv[1]) == "--vectors") {
        vectors(true);
    } else if (argc == 1) {
        vectors(false);
        std::printf("PASS: %u checks (synthetic fixtures; host semantics)\n", checks);
    } else {
        std::fprintf(stderr, "usage: %s [--vectors|--emit-oracle]\n", argv[0]);
        return 2;
    }
}
