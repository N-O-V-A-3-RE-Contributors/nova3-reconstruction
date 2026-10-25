#include "collada_mesh.hpp"
#include "bres.hpp"
#include <cmath>
#include <cstring>
#include <limits>
#include <utility>
namespace nova3 {
namespace {
class Reader {
public:
    Reader(const std::uint8_t* p,std::size_t size):p_(p),size_(size){}
    bool range(std::uint64_t at,std::uint64_t size) const {return at<=size_ && size<=size_-at;}
    bool word(std::uint64_t at,std::uint32_t& v) const {
        if(!range(at,4))return false;
        v=std::uint32_t(p_[at])|(std::uint32_t(p_[at+1])<<8)|(std::uint32_t(p_[at+2])<<16)|(std::uint32_t(p_[at+3])<<24);return true;
    }
    bool floating(std::uint64_t at,float& f) const {
        static_assert(sizeof(float)==4 && std::numeric_limits<float>::is_iec559,"IEEE float32 required");
        std::uint32_t bits;if(!word(at,bits))return false;std::memcpy(&f,&bits,4);return std::isfinite(f);
    }
    bool text(std::uint32_t at,std::string& s)const {
        std::uint32_t n;if(at<4 || !word(at-4,n) || n>4096 || !range(at,n))return false;
        s.assign(reinterpret_cast<const char*>(p_+at),n);return true;
    }
    bool serialized(std::uint32_t table,std::uint32_t count,std::uint32_t offset,std::uint64_t needed,std::uint64_t& data)const {
        if(!range(table,std::uint64_t(count)*8))return false;
        for(std::uint32_t i=0;i<count;++i){
            std::uint32_t size,where;if(!word(std::uint64_t(table)+i*8,size) || !word(std::uint64_t(table)+i*8+4,where))return false;
            if(where==offset){if(size<4 || needed>size-4 || !range(where,size))return false;data=std::uint64_t(where)+4;return true;}
        }
        return false;
    }
private:const std::uint8_t* p_;std::uint64_t size_;
};
}
MeshError read_collada_mesh(const std::uint8_t* bytes,std::size_t size,std::size_t which,
    ColladaMesh& output,std::size_t vertex_limit,std::size_t index_limit){
    if(!bytes || size<60)return MeshError::header;
    Reader r(bytes,size);std::uint32_t flags,base,declared,root,tail_table,tail_count;
    if(!r.word(4,flags) || (flags&0x80000000u) || !r.word(20,base) || base)return MeshError::unsupported;
    std::vector<std::uint8_t> validated;
    if(relocate_bres32(bytes,size,0,validated)!=BresError::ok)return MeshError::header;
    if(!r.word(12,declared) || declared!=size || !r.word(32,root) || !r.range(root,0x8c) ||
       !r.word(40,tail_table) || !r.word(48,tail_count))return MeshError::bounds;
    if(tail_count>10000)return MeshError::limit;
    std::uint32_t count,array,root_flags;
    if(!r.word(std::uint64_t(root)+0x80,root_flags) || root_flags)return MeshError::unsupported;
    if(!r.word(std::uint64_t(root)+0x84,count) || !r.word(std::uint64_t(root)+0x88,array) ||
       which>=count || !r.range(array,std::uint64_t(count)*16))return MeshError::bounds;
    const auto geometry=std::uint64_t(array)+which*16;std::uint32_t tag,mesh,name;
    if(!r.word(geometry+8,tag) || tag)return MeshError::unsupported;
    if(!r.word(geometry+12,mesh) || !r.range(mesh,0x58) || !r.word(geometry,name))return MeshError::bounds;
    ColladaMesh next;if(!r.text(name,next.name))return MeshError::bounds;
    std::uint32_t interleaved,vertices,buffers,buffer_table,raw_vertex;
    if(!r.word(mesh,interleaved) || interleaved!=1)return MeshError::unsupported;
    if(!r.word(std::uint64_t(mesh)+4,vertices) || !r.word(std::uint64_t(mesh)+0x30,raw_vertex) ||
       !r.word(std::uint64_t(mesh)+0x38,buffers) || !r.word(std::uint64_t(mesh)+0x3c,buffer_table))return MeshError::bounds;
    if(vertices>vertex_limit || buffers>10000)return MeshError::limit;
    if(!r.range(buffer_table,std::uint64_t(buffers)*56))return MeshError::bounds;
    std::array<std::uint32_t,4> tables{},lengths{};
    for(unsigned field=0;field<4;++field){
        if(!r.word(std::uint64_t(mesh)+8+field*8,lengths[field]) || !r.word(std::uint64_t(mesh)+12+field*8,tables[field]))return MeshError::bounds;
        if(lengths[field]>64)return MeshError::limit;
        if(!r.range(tables[field],std::uint64_t(lengths[field])*4))return MeshError::bounds;
        if(field && lengths[field]!=lengths[0])return MeshError::invalid;
    }
    if(!lengths[0])return MeshError::unsupported;
    next.attributes.resize(lengths[0]);
    for(std::size_t i=0;i<next.attributes.size();++i){
        auto& a=next.attributes[i];r.word(std::uint64_t(tables[0])+i*4,a.stride);r.word(std::uint64_t(tables[1])+i*4,a.offset);
        r.word(std::uint64_t(tables[2])+i*4,a.type);r.word(std::uint64_t(tables[3])+i*4,a.components);
    }
    // The native first fixed stream selector is read from SMeshBuffer+0x0c.
    // This subset accepts primary selector0 consistently across all buffers.
    const auto& position=next.attributes[0];
    if(position.type!=6 || position.components!=3 || position.offset>position.stride || position.stride-position.offset<12)return MeshError::unsupported;
    std::uint64_t vertex_data;
    if(!r.serialized(tail_table,tail_count,raw_vertex,std::uint64_t(vertices)*position.stride,vertex_data))return MeshError::bounds;
    next.positions.resize(vertices);
    for(std::size_t i=0;i<vertices;++i)for(unsigned axis=0;axis<3;++axis)
        if(!r.floating(vertex_data+i*position.stride+position.offset+axis*4,next.positions[i][axis]))return MeshError::invalid;
    for(unsigned i=0;i<6;++i)if(!r.floating(std::uint64_t(mesh)+0x40+i*4,next.bounds[i]))return MeshError::invalid;
    std::size_t total_indices=0;
    for(std::uint32_t i=0;i<buffers;++i){
        const auto buffer=std::uint64_t(buffer_table)+i*56;std::uint32_t primitive,selectors,minimum,maximum,n,raw_index;
        if(!r.word(buffer,primitive) || !r.word(buffer+12,selectors))return MeshError::bounds;
        // Native serialized0 -> runtime6 -> GL_TRIANGLES4 is evidence-backed.
        if(primitive || (selectors&255u))return MeshError::unsupported;
        if(!r.word(buffer+0x20,minimum) || !r.word(buffer+0x24,maximum) || !r.word(buffer+0x28,n) || !r.word(buffer+0x2c,raw_index))return MeshError::bounds;
        if(minimum>maximum || maximum>=vertices || n%3)return MeshError::invalid;
        if(n>index_limit || total_indices>index_limit-n)return MeshError::limit;
        total_indices+=n;const unsigned width=maximum-minimum<=0xfffeu?2:4;
        std::uint64_t index_data;if(!r.serialized(tail_table,tail_count,raw_index,std::uint64_t(n)*width,index_data))return MeshError::bounds;
        TriangleBuffer decoded;decoded.indices.reserve(n);
        for(std::uint32_t j=0;j<n;++j){
            std::uint32_t index;
            if(width==4){if(!r.word(index_data+std::uint64_t(j)*4,index))return MeshError::bounds;}
            else{std::uint32_t pair;const auto at=index_data+std::uint64_t(j)*2;
                // Avoid a four-byte read beyond a two-byte final element.
                if(!r.range(at,2))return MeshError::bounds;
                pair=std::uint32_t(bytes[at])|(std::uint32_t(bytes[at+1])<<8);index=pair;
            }
            if(index>maximum-minimum)return MeshError::invalid;
            // addStream advances the GPU vertex base by minimum*stride.
            // Exporting a single global vertex array requires adding that base.
            decoded.indices.push_back(index+minimum);
        }
        next.buffers.push_back(std::move(decoded));
    }
    output=std::move(next);return MeshError::ok;
}
}
