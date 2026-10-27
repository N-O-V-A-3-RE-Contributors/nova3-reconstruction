// Standalone task test: include the implementation because no new header path
// is allocated to this issue. Link engine/{pvr_texture,etc1}.cpp.
// --oracle PYTHON ORIGINAL_SO is optional, local analysis only; ordinary tests
// and the source build do not require any proprietary input or Python package.
#include "../../../recovered/libNOVA3_neon/block_00781c78.cpp"
#include "../../../engine/etc1.hpp"

#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#if defined(__unix__)
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace {
using nova3::recovered::pvr::TextureDescription;
using nova3::recovered::pvr::load_texture_header;
using Bytes = std::vector<std::uint8_t>;

void check(bool ok, int line) {
    if (!ok) {
        std::cerr << "PVR descriptor check failed at line " << line << '\n';
        std::exit(1);
    }
}
#define CHECK(x) check((x), __LINE__)

void word(Bytes& bytes, std::size_t at, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        bytes[at + i] = static_cast<std::uint8_t>(value >> (8 * i));
}

Bytes header(std::uint32_t flags = 0x36) {
    Bytes result(52, 0);
    word(result, 0, 52); word(result, 4, 8); word(result, 8, 16);
    word(result, 16, flags); word(result, 20, 123); word(result, 24, 4);
    word(result, 44, 0x21525650); word(result, 48, 1);
    return result;
}

TextureDescription sentinel() {
    TextureDescription result;
    std::memset(&result, 0x5a, sizeof result);
    return result;
}

class TestServices final : public nova3::recovered::pvr::DataLoadServices {
public:
    std::uint32_t choice = 0;
    bool result = true;
    unsigned selections = 0, loads = 0;
    nova3::recovered::pvr::DataInfo info{};
    std::uint32_t choose_mipmap(std::uint32_t, std::uint32_t,
                               const TextureDescription&) override {
        ++selections;
        return choice;
    }
    bool load_data(const nova3::recovered::pvr::DataInfo& data,
                   const TextureDescription&) override {
        ++loads;
        info = data;
        return result;
    }
};

void data_tests() {
    using nova3::recovered::pvr::load_texture_data;
    auto bytes = header(0x136); word(bytes, 12, 4);
    auto description = sentinel(); description.width = 4; description.height = 2;
    const auto saved = description;
    TestServices services; services.choice = 2;
    CHECK(load_texture_data(bytes.data(), 52, 175, description, services));
    CHECK(services.selections == 1 && services.loads == 1);
    CHECK(services.info.file_data_size == 43 && services.info.face_start_offset == 80);
    CHECK(services.info.file_pitch(0) == 0 && services.info.file_pitch(255) == 0);
    CHECK(services.info.little_endian() && !services.info.needs_flip());
    CHECK(std::memcmp(&description, &saved, sizeof saved) == 0);

    services = TestServices{}; services.choice = 0xffffffffu;
    CHECK(!load_texture_data(bytes.data(), 52, 175, description, services));
    CHECK(services.selections == 1 && services.loads == 0);
    services = TestServices{}; services.choice = 1; services.result = false;
    CHECK(!load_texture_data(bytes.data(), 52, 175, description, services));
    CHECK(services.loads == 1);
    bytes = header(); services = TestServices{};
    CHECK(!load_texture_data(bytes.data(), 52, 175, description, services));
    CHECK(services.selections == 0 && services.loads == 0);
    description.width = 16; description.height = 8;
    CHECK(load_texture_data(bytes.data(), 52, 175, description, services));
    CHECK(services.selections == 0 && services.loads == 1);

    // Data wrapper only checks the header predicate: an unknown format can
    // reach the delegate even though load_texture_header would reject it.
    bytes = header(0xff); services = TestServices{};
    CHECK(load_texture_data(bytes.data(), 52, 175, description, services));
    bytes = header(0x136); word(bytes, 12, 4); word(bytes, 24, 32);
    services = TestServices{}; services.choice = 5;
    CHECK(load_texture_data(bytes.data(), 52, 175, description, services));
    CHECK(services.info.face_start_offset == 684);
    CHECK(services.info.file_data_size == std::uint32_t(123u - 684u));
    services = TestServices{};
    CHECK(!load_texture_data(nullptr, 52, 175, description, services));
    CHECK(services.loads == 0 && services.selections == 0);
    std::cout << "PVR data dispatch and mip planning tests passed\n";
}

void unit_tests() {
    // Smoke vector for the existing 0x7914a4 ETC1 branch. Broader synthetic
    // endpoint/selector/error tests remain in reconstruction/tests/etc1_test.cpp.
    Bytes block(8, 0), rgb;
    CHECK(nova3::decode_etc1(block.data(), block.size(), 4, 4, rgb) == nova3::Etc1Error::ok);
    CHECK(rgb == Bytes(48, 2));
    auto bytes = header();
    auto description = sentinel();
    CHECK(load_texture_header(bytes.data(), bytes.size(), 175, description));
    CHECK(description.type_code == 1 && description.format_code == 29);
    CHECK(description.width == 16 && description.height == 8 && description.depth == 1);
    CHECK(description.layout_code == 0 && description.mipmapped == 0);
    CHECK(description.reserved == 0x5a5a5a5a);
    for (auto b : description.padding) CHECK(b == 0x5a);

    // Independent synthetic expected codes; the optional oracle executes the
    // reference ARM switch separately, rather than assuming this table proves it.
    const std::array<std::array<unsigned, 2>, 34> formats{{
        {{0x00, 6}}, {{0x01, 8}}, {{0x02, 5}}, {{0x04, 10}},
        {{0x05, 13}}, {{0x07, 0}}, {{0x08, 4}}, {{0x0c, 21}},
        {{0x0d, 23}}, {{0x10, 7}}, {{0x11, 9}}, {{0x12, 14}},
        {{0x13, 5}}, {{0x15, 10}}, {{0x16, 0}}, {{0x17, 4}},
        {{0x18, 21}}, {{0x19, 23}}, {{0x1a, 13}}, {{0x1c, 25}},
        {{0x1d, 27}}, {{0x20, 17}}, {{0x21, 19}}, {{0x22, 19}},
        {{0x23, 20}}, {{0x24, 20}}, {{0x2a, 16}}, {{0x36, 29}},
        {{0x39, 2}}, {{0x3b, 1}}, {{0x50, 40}}, {{0x53, 39}},
        {{0x56, 38}}, {{0x60, 30}}
    }};
    for (const auto& entry : formats) {
        bytes = header(entry[0]); description = sentinel();
        CHECK(load_texture_header(bytes.data(), 52, 175, description));
        CHECK(description.format_code == entry[1]);
        bytes = header(entry[0] | 0x8000); description = sentinel();
        CHECK(load_texture_header(bytes.data(), 52, 175, description));
        const bool alpha_variant = entry[1] == 17 || entry[1] == 21 ||
            entry[1] == 23 || entry[1] == 25 || entry[1] == 27 || entry[1] == 30;
        CHECK(description.format_code == entry[1] + unsigned(alpha_variant));
        bytes = header(entry[0] | 0x200); description = sentinel();
        CHECK(load_texture_header(bytes.data(), 52, 175, description) ==
              (entry[1] >= 17 && entry[1] <= 31));
        CHECK(description.format_code == entry[1] && description.layout_code == 3);
    }

    // Cube has precedence over volume; it multiplies payload length by six.
    bytes = header(0x5036); word(bytes, 48, 6); description = sentinel();
    CHECK(load_texture_header(bytes.data(), 52, 790, description));
    CHECK(description.type_code == 3 && description.depth == 1);
    description = sentinel();
    CHECK(!load_texture_header(bytes.data(), 52, 175, description));
    CHECK(description.type_code == 3 && description.format_code == 0x5a5a5a5a);

    bytes = header(0x4336); word(bytes, 48, 32); word(bytes, 12, 5);
    description = sentinel();
    CHECK(load_texture_header(bytes.data(), 52, 175, description));
    CHECK(description.type_code == 2 && description.depth == 32);
    CHECK(description.mipmapped == 1 && description.layout_code == 3);

    // Preserve native metadata quirks. These outcomes do not certify a safe
    // payload, allocate pixels, or imply that rendering accepts these headers.
    bytes = header(); word(bytes, 4, 0); word(bytes, 8, 0); word(bytes, 24, 12345);
    word(bytes, 20, 0xffffffff); description = sentinel();
    CHECK(load_texture_header(bytes.data(), 52, 51, description));
    CHECK(description.width == 0 && description.height == 0);
    bytes = header(0x36); word(bytes, 12, 0xffffffff);
    CHECK(load_texture_header(bytes.data(), 52, 175, description));

    bytes = header();
    const auto saved = sentinel();
    for (std::size_t n = 0; n < 52; ++n) {
        description = saved;
        CHECK(!load_texture_header(bytes.data(), n, 175, description));
        CHECK(std::memcmp(&description, &saved, sizeof saved) == 0);
    }
    description = saved;
    CHECK(!load_texture_header(nullptr, 52, 175, description));
    CHECK(std::memcmp(&description, &saved, sizeof saved) == 0);
    word(bytes, 44, 0);
    CHECK(!load_texture_header(bytes.data(), 52, 175, description));
    CHECK(std::memcmp(&description, &saved, sizeof saved) == 0);

    // Unaligned storage, trailing bytes, and neighboring descriptor canaries.
    struct Guarded { std::uint32_t before; TextureDescription desc; std::uint32_t after; };
    Guarded guarded{0x12345678, sentinel(), 0x87654321};
    bytes = header(); bytes.insert(bytes.begin(), 0xab); bytes.push_back(0xcd);
    CHECK(load_texture_header(bytes.data() + 1, bytes.size() - 1, 175, guarded.desc));
    CHECK(guarded.before == 0x12345678 && guarded.after == 0x87654321);
    std::cout << "PVR descriptor synthetic tests passed\n";
}

int batch(bool data_mode, bool pixel_mode = false) {
    std::uint32_t total = 0;
    unsigned fill = 0;
    std::string encoded;
    std::int32_t choice = 0;
    unsigned delegate = 0;
    std::uint32_t width = 0, height = 0;
    unsigned flipped = 0;
    std::size_t pitch = 0;
    while (pixel_mode ? bool(std::cin >> width >> height >> flipped >> pitch >> encoded)
           : data_mode ? bool(std::cin >> choice >> delegate >> width >> height >> total >> encoded)
                     : bool(std::cin >> total >> fill >> encoded)) {
        if (fill > 255 || (encoded != "-" && encoded.size() % 2)) return 2;
        Bytes bytes;
        if (encoded != "-") {
            auto digit = [](char c) {
                if (c >= '0' && c <= '9') return c - '0';
                if (c >= 'a' && c <= 'f') return c - 'a' + 10;
                return -1;
            };
            for (std::size_t i = 0; i < encoded.size(); i += 2) {
                const int a = digit(encoded[i]), b = digit(encoded[i + 1]);
                if (a < 0 || b < 0) return 2;
                bytes.push_back(static_cast<std::uint8_t>((a << 4) | b));
            }
        }
        if (pixel_mode) {
            Bytes pixels;
            if (nova3::decode_etc1(bytes.data(), bytes.size(), width, height, pixels,
                                   flipped != 0, pitch) != nova3::Etc1Error::ok) return 2;
            constexpr char hex[] = "0123456789abcdef";
            for (auto b : pixels) std::cout << hex[b >> 4] << hex[b & 15];
            std::cout << '\n';
            continue;
        }
        TextureDescription description;
        std::memset(&description, int(fill), sizeof description);
        if (data_mode) {
            description = TextureDescription{1,29,0,0,width,height,1,1,{0,0,0}};
            TestServices services; services.choice = static_cast<std::uint32_t>(choice);
            services.result = delegate != 0;
            const bool result = nova3::recovered::pvr::load_texture_data(
                bytes.data(), bytes.size(), total, description, services);
            std::cout << unsigned(result) << ' ' << services.selections << ' ' << services.loads
                      << ' ' << services.info.file_data_size << ' ' << services.info.face_start_offset
                      << ' ' << services.info.file_pitch(0) << ' ' << services.info.little_endian()
                      << ' ' << services.info.needs_flip() << '\n';
            continue;
        }
        const bool accepted = nova3::valid_pvr_v2_header(bytes.data(), bytes.size());
        const bool loaded = load_texture_header(bytes.data(), bytes.size(), total, description);
        std::array<std::uint32_t, 8> words{};
        std::memcpy(words.data(), &description, sizeof description);
        std::cout << unsigned(accepted) << ' ' << unsigned(loaded);
        for (auto value : words) std::cout << ' ' << value;
        std::cout << '\n';
    }
    return std::cin.eof() ? 0 : 2;
}

const char oracle_script[] = R"PY(
import sys, subprocess
if not __debug__:
    raise SystemExit("Reference checks require Python assertions enabled")
binary = sys.argv[2]
expected_hash = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
import hashlib
with open(binary, "rb") as stream:
    actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
if actual_hash != expected_hash:
    raise SystemExit("Reference SHA256 mismatch; refusing oracle")
)PY"
R"PY(
import struct, json, hashlib, random
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UcError
from unicorn.arm_const import *
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
with open(binary,'rb') as f:
 for s in ELFFile(f).iter_segments():
  if s['p_type']=='PT_LOAD':
   lo=s['p_vaddr']&~4095;end=(s['p_vaddr']+s['p_memsz']+4095)&~4095;u.mem_map(lo,end-lo);u.mem_write(s['p_vaddr'],s.data())
u.mem_map(0x2000000,0x20000);u.mem_map(0x3000000,0x20000)
obj=0x2000000;vt=obj+0x100;dest=obj+0x1000;stop=0x300fffc
put=lambda p,v:u.mem_write(p,struct.pack('<I',v))
put(obj,vt)
for offset in range(0,0x100,4):put(vt+offset,0x3011000+offset*4)
u.mem_write(obj+0x3000,b'synthetic\0');current=b'';position=0;file_size=0;trace=[]
def hook(uc,addr,size,data):
 global position
 r0=uc.reg_read(UC_ARM_REG_R0);r1=uc.reg_read(UC_ARM_REG_R1);r2=uc.reg_read(UC_ARM_REG_R2)
 if addr>=0x3011000:
  slot=(addr-0x3011000)//4
  trace.append([slot,r1,r2])
  if slot==0x18:position=r1;uc.reg_write(UC_ARM_REG_R0,1)
  elif slot==0xc:
   block=current[position:position+r2]
   if block:uc.mem_write(r1,block)
   position+=len(block);uc.reg_write(UC_ARM_REG_R0,len(block))
  elif slot==0x28:uc.reg_write(UC_ARM_REG_R0,obj+0x3000)
  elif slot==0x20:uc.reg_write(UC_ARM_REG_R0,file_size)
  else:raise AssertionError(('unmodeled file method',slot))
 elif addr==0xd2da8:
  a,b=bytes(uc.mem_read(r0,r2)),bytes(uc.mem_read(r1,r2));uc.reg_write(UC_ARM_REG_R0,((a>b)-(a<b))&0xffffffff)
 elif addr==0x69ddac:uc.reg_write(UC_ARM_REG_R0,0)
 else:raise AssertionError(('unmodeled external',addr))
 uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
for address in [0x3011000+o*4 for o in range(0,0x100,4)]+[0xd2da8,0x69ddac]:u.hook_add(UC_HOOK_CODE,hook,begin=address,end=address)
def header(flags=0x36,w=16,h=8,levels=0,size=123,bpp=4,surfaces=1,alpha=0):
 return struct.pack('<13I',52,h,w,levels,flags,size,bpp,0,0,0,alpha,0x21525650,surfaces)
def native(data,entry=0x781e08,total=None,fill=0x5a):
 global current,position,file_size,trace
 current=data;position=0;file_size=total if total is not None else len(data)+struct.unpack_from('<I',data,20)[0];trace=[]
 u.mem_write(dest,bytes([fill])*96);u.mem_write(0x3000000,b'\0'*0x10000)
 regs=[(UC_ARM_REG_R0,obj+0x4000),(UC_ARM_REG_R1,obj),(UC_ARM_REG_R2,dest)] if entry!=0x781c78 else [(UC_ARM_REG_R0,obj),(UC_ARM_REG_R1,dest)]
 for reg,value in regs+[(UC_ARM_REG_SP,0x3008000),(UC_ARM_REG_LR,stop)]:u.reg_write(reg,value)
 u.emu_start(entry,stop,count=20000)
 assert u.reg_read(UC_ARM_REG_PC)==stop
 return u.reg_read(UC_ARM_REG_R0),list(struct.unpack('<8I',u.mem_read(dest,32))),trace

# Synthetic metadata only; no game assets, exports, raw instructions or dumps.
cases = []
base = header()
for n in range(53):
 cases.append((base[:n],175,0x5a))
for extra in [0,0x200,0x8000,0x8200,0x1000,0x4000,0x5000,0x80005000]:
 for fmt in range(256):
  cube=bool(extra&0x1000)
  data=header(fmt|extra,surfaces=6 if cube else 8)
  cases.append((data,790 if cube else 175,0x5a))
for flags in [0x36,0x136,0x236,0x336,0x1036,0x1136,0x4036,0x4136,0x5136]:
 for w,h in [(0,0),(0,8),(1,1),(2,2),(3,7),(16,8),(0xffffffff,0xffffffff)]:
  for surfaces in [0,1,6,8,32,0xffffffff]:
   for levels in [0,1,3,4,5,31,0xffffffff]:
    data=header(flags,w=w,h=h,surfaces=surfaces,levels=levels)
    cases.append((data,790 if flags&0x1000 else 175,0x5a))
for payload in [0,1,123,0x7fffffff,0xffffffcb,0xffffffcc,0xffffffff]:
 for flags in [0x36,0x1036,0x4036]:
  correct=(52+payload*(6 if flags&0x1000 else 1))&0xffffffff
  for delta in [-1,0,1]:
   cases.append((header(flags,size=payload,surfaces=6), (correct+delta)&0xffffffff, 0xa5))
rng=random.Random(0x33)
for _ in range(1000):
 words=list(struct.unpack("<13I",base))
 for __ in range(rng.randrange(1,6)):
  words[rng.randrange(13)]=rng.getrandbits(32)
 data=struct.pack("<13I",*words)
 total=(52+words[5]*(6 if words[4]&0x1000 else 1))&0xffffffff
 cases.append((data,total,rng.randrange(256)))
expected=[]
protocol=[]
transcript=hashlib.sha256()
for data,total,fill in cases:
 predicate,_,_=native(data,entry=0x781c78,total=total,fill=fill)
 result,description,_=native(data,total=total,fill=fill)
 record=[predicate,result]+description
 expected.append(record)
 line=f"{total} {fill} {data.hex() or '-'}"
 protocol.append(line)
 transcript.update((line+" "+str(record)+"\n").encode())
source=subprocess.run([sys.argv[1],"--batch"],input="\n".join(protocol)+"\n",
                      text=True,capture_output=True,check=True)
actual=[[int(n) for n in line.split()] for line in source.stdout.splitlines()]
if len(actual)!=len(expected):raise AssertionError("missing source results")
for i,(a,b) in enumerate(zip(actual,expected)):
 if a!=b:
  raise AssertionError(f"case {i}: synthetic={protocol[i]}, source={a}, ARM={b}")

# Separate original loadTextureData wrapper. Model its TWO engine dependencies
# and inspect the original CDataInfo accessors; no GPU success is asserted.
selected=0; delegate_result=0; selections=0; loads=0; info_address=None
def loader_hook(uc,addr,size,data):
 global selections,loads,info_address
 if addr==0x77f604:
  selections+=1
  assert uc.reg_read(UC_ARM_REG_R0)==struct.unpack_from('<I',current,8)[0]
  assert uc.reg_read(UC_ARM_REG_R1)==struct.unpack_from('<I',current,4)[0]
  assert uc.reg_read(UC_ARM_REG_R2)==dest+16
  uc.reg_write(UC_ARM_REG_R0,selected&0xffffffff)
 else:
  loads+=1;info_address=uc.reg_read(UC_ARM_REG_R1)
  assert uc.reg_read(UC_ARM_REG_R0)==obj
  assert uc.reg_read(UC_ARM_REG_R2)==dest
  assert uc.reg_read(UC_ARM_REG_R3)==obj+0x5000
  uc.reg_write(UC_ARM_REG_R0,delegate_result)
 uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
for address in [0x77f604,0x7a4988]:
 u.hook_add(UC_HOOK_CODE,loader_hook,begin=address,end=address)
data_cases=[]
for flags in [0x36,0x136,0x1036,0x1136,0x4036,0x4136,0x10036,0x10136,0xff]:
 for w,h,levels in [(16,8,4),(2,2,1),(3,7,2),(0xffffffff,2,31),(0,8,0xffffffff)]:
  for bpp in [0,4,7,32,0xffffffff]:
   for selected in [-1,0,1,2,5,32,257]:
    data=header(flags,w=w,h=h,levels=levels,bpp=bpp,surfaces=6 if flags&0x1000 else 1)
    # Both matching and mismatched destination dimensions; preserve return
    # propagation of loadData for both success and failure.
    for delegate_result in [0,1]:
     total=[0,52,175,0xffffffff,(w*3+h*17+bpp*31)&0xffffffff][bpp%5]
     data_cases.append((data,selected,delegate_result,w if delegate_result else 4,h if delegate_result else 2,total))
data_cases.extend((base[:n],0,1,16,8,175) for n in range(52))
data_expected=[];data_protocol=[]
for current,selected,delegate_result,tw,th,total in data_cases:
 position=0;file_size=total;selections=0;loads=0;info_address=None
 target=struct.pack('<8I',1,29,0,0,tw,th,1,1)
 u.mem_write(dest,target);u.mem_write(0x3000000,b'\0'*0x10000)
 for reg,value in [(UC_ARM_REG_R0,obj+0x4000),(UC_ARM_REG_R1,obj),
                   (UC_ARM_REG_R2,obj+0x5000),(UC_ARM_REG_R3,dest),
                   (UC_ARM_REG_SP,0x3008000),(UC_ARM_REG_LR,stop)]:
  u.reg_write(reg,value)
 u.emu_start(0x7a5088,stop,count=20000)
 assert u.reg_read(UC_ARM_REG_PC)==stop
 assert bytes(u.mem_read(dest,32))==target
 result=u.reg_read(UC_ARM_REG_R0)
 values=[0,0,0,1,0]
 if info_address is not None:
  values=[]
  for method in [0x7b8974,0x7b8984,0x7b897c,0x7b898c,0x7b8994]:
   for reg,value in [(UC_ARM_REG_R0,info_address),(UC_ARM_REG_R1,0),
                     (UC_ARM_REG_SP,0x3004000),(UC_ARM_REG_LR,stop)]:
    u.reg_write(reg,value)
   u.emu_start(method,stop,count=100)
   assert u.reg_read(UC_ARM_REG_PC)==stop
   values.append(u.reg_read(UC_ARM_REG_R0))
 row=[result,selections,loads]+values
 data_expected.append(row)
 line=f"{selected} {delegate_result} {tw} {th} {total} {current.hex() or '-'}"
 data_protocol.append(line)
 transcript.update((line+' '+str(row)+'\n').encode())
source=subprocess.run([sys.argv[1],'--data-batch'],input='\n'.join(data_protocol)+'\n',
                      text=True,capture_output=True,check=True)
data_actual=[[int(n) for n in line.split()] for line in source.stdout.splitlines()]
assert len(data_actual)==len(data_expected)
for i,(a,b) in enumerate(zip(data_actual,data_expected)):
 if a!=b:raise AssertionError(f"data case {i}: synthetic={data_protocol[i]}, source={a}, ARM={b}")

# Existing engine/etc1.cpp is a partial reconstruction of convert 0x7914a4.
# Execute this original path with NO helper substitutions; source uses its
# existing API. The other pixel formats and tiny/malformed blocks are excluded.
pixel_expected=[];pixel_protocol=[]
pixel_source=obj+0x8000;pixel_dest=obj+0x10000
for flip in [0,1]:
 for differential in [0,1]:
  for _ in range(60):
   w=rng.choice([4,8,12,16]);h=rng.choice([4,8,12]);pitch=w//4*8+rng.choice([0,8])
   data=bytearray((h//4)*pitch)
   for y in range(h//4):
    for x in range(w//4):
     block=bytearray(rng.randbytes(8));block[3]=(block[3]&~2)|(differential<<1)
     if differential:
      for c in range(3):
       first=rng.randrange(32)
       delta=rng.choice([n for n in range(-4,4) if 0<=first+n<32])
       block[c]=(first<<3)|(delta&7)
     data[y*pitch+x*8:y*pitch+x*8+8]=block
   dp=w*3+rng.choice([0,4,12]);canary=bytes([0x5a])*(dp*h+32)
   u.mem_write(pixel_source,bytes(data));u.mem_write(pixel_dest,canary)
   stack=0x3008000
   u.mem_write(stack,struct.pack('<5I',pixel_dest,dp,w,h,flip))
   for reg,value in [(UC_ARM_REG_R0,29),(UC_ARM_REG_R1,pixel_source),
                     (UC_ARM_REG_R2,pitch),(UC_ARM_REG_R3,10),
                     (UC_ARM_REG_SP,stack),(UC_ARM_REG_LR,stop)]:
    u.reg_write(reg,value)
   u.emu_start(0x7914a4,stop,count=2000000)
   assert u.reg_read(UC_ARM_REG_PC)==stop and u.reg_read(UC_ARM_REG_R0)==1
   native_pixels=bytes(u.mem_read(pixel_dest,dp*h+32))
   for row in range(h):
    assert native_pixels[row*dp+w*3:(row+1)*dp]==canary[row*dp+w*3:(row+1)*dp]
   assert native_pixels[dp*h:]==canary[dp*h:]
   packed=b''.join(native_pixels[row*dp:row*dp+w*3] for row in range(h))
   pixel_expected.append(packed.hex())
   line=f'{w} {h} {flip} {pitch} {data.hex()}'
   pixel_protocol.append(line)
   transcript.update((line+' '+packed.hex()+'\n').encode())
source=subprocess.run([sys.argv[1],'--pixels-batch'],input='\n'.join(pixel_protocol)+'\n',
                      text=True,capture_output=True,check=True)
assert source.stdout.splitlines()==pixel_expected, 'ETC1 pixel mismatch'
print(json.dumps({
 "status":"PASS", "predicate_cases":len(cases), "descriptor_cases":len(cases),
 "data_dispatch_cases":len(data_cases),
 "etc1_pixel_cases":len(pixel_expected),
 "reference_sha256":actual_hash, "transcript_sha256":transcript.hexdigest(),
 "python":sys.version.split()[0], "unicorn":__import__("unicorn").__version__,
 "pyelftools":__import__("elftools").__version__,
 "hooks":["IReadFile seek/read/getSize/getFileName", "strncmp", "logf sink",
          "getMipmapToLoad selector", "IImageLoader::loadData result and argument capture"],
 "scope":"Header returns and 32 descriptor bytes; data dispatch and original CDataInfo getters with modeled dependencies; ETC1->RGB8 pixels with explicit pitches and Y flip, no hooks on that path",
 "matching":"NOT MATCHED"
},sort_keys=True))
)PY";

int run_oracle(const char* python, const char* binary, const char* executable) {
#if defined(__unix__)
    const auto child = fork();
    if (child < 0) return 2;
    if (child == 0) {
        execl(python, python, "-c", oracle_script, executable, binary, static_cast<char*>(nullptr));
        _exit(127);
    }
    int status = 0;
    if (waitpid(child, &status, 0) != child) return 2;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 2;
#else
    (void)python; (void)binary; (void)executable;
    std::cerr << "The optional local reference driver requires POSIX process APIs\n";
    return 2;
#endif
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--batch") return batch(false);
    if (argc == 2 && std::string(argv[1]) == "--data-batch") return batch(true);
    if (argc == 2 && std::string(argv[1]) == "--pixels-batch") return batch(false, true);
    if (argc == 4 && std::string(argv[1]) == "--oracle")
        return run_oracle(argv[2], argv[3], argv[0]);
    if (argc != 1) return 2;
    unit_tests();
    data_tests();
}
