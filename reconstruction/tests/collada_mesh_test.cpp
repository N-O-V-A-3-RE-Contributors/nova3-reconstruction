#include "collada_mesh.hpp"
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <vector>
static void require(bool ok){if(!ok){std::cerr<<"mesh assertion failed\n";std::exit(1);}}
static void put(std::vector<std::uint8_t>& b,std::size_t p,std::uint32_t value){for(unsigned i=0;i<4;++i)b[p+i]=static_cast<std::uint8_t>(value>>(i*8));}
static void floating(std::vector<std::uint8_t>& b,std::size_t p,float v){std::uint32_t bits;std::memcpy(&bits,&v,4);put(b,p,bits);}
int main(){
 std::vector<std::uint8_t> b(1024,0);b[0]='B';b[1]='R';b[2]='E';b[3]='S';
 for(auto p:std::vector<std::pair<std::size_t,std::uint32_t>>{{12,1024},{16,1},{24,60},{32,128},{40,64},{48,2},{60,24},{64,40},{68,600},{72,10},{76,644},{88,4},{128+0x84,1},{128+0x88,320},{320,92},{332,336},{336,1},{340,3},{344,1},{348,432},{352,1},{356,436},{360,1},{364,440},{368,1},{372,444},{384,600},{392,1},{396,480},{432,12},{436,0},{440,6},{444,3},{480+0x24,2},{480+0x28,3},{480+0x2c,644},{600,1},{644,1}})put(b,p.first,p.second);
 b[92]='t';b[93]='e';b[94]='s';b[95]='t';
 floating(b,604+12,1);floating(b,604+24+4,1);floating(b,336+0x40+12,1);floating(b,336+0x40+16,1);b[650]=1;b[652]=2;
 nova3::ColladaMesh out;
 require(nova3::read_collada_mesh(b.data(),b.size(),0,out)==nova3::MeshError::ok);
 require(out.name=="test" && out.positions.size()==3 && out.positions[1][0]==1 && out.positions[2][1]==1);
 require(out.buffers.size()==1 && out.buffers[0].indices==std::vector<std::uint32_t>({0,1,2}));
 require(nova3::read_collada_mesh(b.data(),b.size(),1,out)==nova3::MeshError::bounds);
 require(nova3::read_collada_mesh(b.data(),b.size(),0,out,2)==nova3::MeshError::limit);
 require(nova3::read_collada_mesh(b.data(),b.size(),0,out,3,2)==nova3::MeshError::limit);
 auto based=b;put(based,480+0x20,1);based[650]=1;based[652]=1;
 nova3::ColladaMesh rebased;require(nova3::read_collada_mesh(based.data(),based.size(),0,rebased)==nova3::MeshError::ok);
 require(rebased.buffers[0].indices==std::vector<std::uint32_t>({1,2,2}));
 auto bad=b;bad[652]=3;require(nova3::read_collada_mesh(bad.data(),bad.size(),0,out)==nova3::MeshError::invalid);
 bad=b;put(bad,432,4);require(nova3::read_collada_mesh(bad.data(),bad.size(),0,out)==nova3::MeshError::unsupported);
 bad=b;put(bad,600,1);put(bad,68,1020);require(nova3::read_collada_mesh(bad.data(),bad.size(),0,out)==nova3::MeshError::bounds);
 for(std::size_t n=0;n<60;++n)require(nova3::read_collada_mesh(b.data(),n,0,out)==nova3::MeshError::header);
 require(out.name=="test" && out.positions.size()==3);
 std::uint32_t random=0x4d455348;
 for(int i=0;i<10000;++i){auto malformed=b;
  for(int j=0;j<4;++j){random=random*1664525u+1013904223u;auto at=random%malformed.size();random=random*1664525u+1013904223u;malformed[at]^=static_cast<std::uint8_t>((random>>24)|1);}
  nova3::ColladaMesh candidate;candidate.name="sentinel";
  auto result=nova3::read_collada_mesh(malformed.data(),malformed.size(),0,candidate);
  if(result==nova3::MeshError::ok){for(auto& group:candidate.buffers)for(auto index:group.indices)require(index<candidate.positions.size());}
  else require(candidate.name=="sentinel");
 }
 std::cout<<"checked inline geometry, float attributes, triangles, limits/indices/offsets passed\n";
}
