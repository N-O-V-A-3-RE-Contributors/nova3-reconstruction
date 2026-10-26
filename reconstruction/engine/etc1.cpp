#include "etc1.hpp"
#include <algorithm>
#include <limits>
#include <utility>
namespace nova3 {
namespace {
constexpr int modifiers[8][4]={{2,8,-2,-8},{5,17,-5,-17},{9,29,-9,-29},{13,42,-13,-42},
                             {18,60,-18,-60},{24,80,-24,-80},{33,106,-33,-106},{47,183,-47,-183}};
int delta(unsigned value){return value<4?int(value):int(value)-8;}
int expand5(int value){return (value<<3)|(value>>2);}
}
Etc1Error decode_etc1(const std::uint8_t* data,std::size_t size,std::uint32_t width,
                     std::uint32_t height,std::vector<std::uint8_t>& output,
                     bool flip_y,std::size_t pitch,std::size_t pixel_limit){
    if(!width || !height || !data)return Etc1Error::bounds;
    const auto pixels=std::uint64_t(width)*height;
    // max_size can be smaller than SIZE_MAX (for example PTRDIFF_MAX on hosts).
    // Reject unrepresentable output before inspecting input or allocating it.
    std::vector<std::uint8_t> next;
    if(pixels>pixel_limit || pixels>next.max_size()/3)return Etc1Error::limit;
    const std::uint64_t columns=(std::uint64_t(width)+3)/4,rows=(std::uint64_t(height)+3)/4;
    const auto minimum_pitch=columns*8;
    if(!pitch){if(minimum_pitch>std::numeric_limits<std::size_t>::max())return Etc1Error::limit;pitch=static_cast<std::size_t>(minimum_pitch);}
    if(pitch<minimum_pitch || rows-1>(std::numeric_limits<std::size_t>::max()-minimum_pitch)/pitch ||
       (rows-1)*pitch+minimum_pitch>size)return Etc1Error::bounds;
    next.resize(static_cast<std::size_t>(pixels)*3);
    for(std::uint64_t by=0;by<rows;++by)for(std::uint64_t bx=0;bx<columns;++bx){
        const auto* p=data+by*pitch+bx*8;int colors[2][3];
        if(p[3]&2){
            for(int c=0;c<3;++c){const int first=p[c]>>3,second=first+delta(p[c]&7);
                if(second<0 || second>31)return Etc1Error::invalid_block;
                colors[0][c]=expand5(first);colors[1][c]=expand5(second);}
        }else for(int c=0;c<3;++c){colors[0][c]=(p[c]>>4)*17;colors[1][c]=(p[c]&15)*17;}
        const unsigned tables[2]={unsigned(p[3]>>5),unsigned((p[3]>>2)&7)};
        const std::uint32_t bits=(std::uint32_t(p[4])<<24)|(std::uint32_t(p[5])<<16)|(std::uint32_t(p[6])<<8)|p[7];
        for(unsigned y=0;y<4 && by*4+y<height;++y)for(unsigned x=0;x<4 && bx*4+x<width;++x){
            const unsigned sub=(p[3]&1)?y/2:x/2,bit=x*4+y;
            const unsigned selector=((bits>>bit)&1)|(((bits>>(bit+16))&1)<<1);
            const auto yy=flip_y?height-1-(by*4+y):by*4+y;
            const auto at=(yy*width+bx*4+x)*3;
            for(unsigned c=0;c<3;++c)next[static_cast<std::size_t>(at+c)]=static_cast<std::uint8_t>(std::clamp(colors[sub][c]+modifiers[tables[sub]][selector],0,255));
        }
    }
    output=std::move(next);return Etc1Error::ok;
}
}
