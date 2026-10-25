#include "collada_mesh.hpp"
#include "zip_archive.hpp"
#include <fstream>
#include <iomanip>
#include <iostream>
int main(int argc,char** argv){
    if(argc!=4){std::cerr<<"usage: nova3_mesh ARCHIVE ASSET OUTPUT.obj\n";return 2;}
    nova3::ZipArchive archive;if(archive.open(argv[1])!=nova3::ArchiveError::ok)return 3;
    std::vector<std::uint8_t> bytes;if(archive.read(argv[2],bytes)!=nova3::ArchiveError::ok)return 4;
    nova3::ColladaMesh mesh;const auto error=nova3::read_collada_mesh(bytes.data(),bytes.size(),0,mesh);
    if(error!=nova3::MeshError::ok){std::cerr<<"mesh error "<<static_cast<int>(error)<<'\n';return 5;}
    std::ofstream out(argv[3]);out<<"# Recovered N.O.V.A.3 asset geometry; no material/scene transforms\n"<<std::setprecision(9);
    for(const auto& p:mesh.positions)out<<"v "<<p[0]<<' '<<p[1]<<' '<<p[2]<<'\n';
    for(const auto& b:mesh.buffers)for(std::size_t i=0;i<b.indices.size();i+=3)out<<"f "<<b.indices[i]+1<<' '<<b.indices[i+1]+1<<' '<<b.indices[i+2]+1<<'\n';
    if(!out)return 6;
    std::cout<<"vertices="<<mesh.positions.size()<<" buffers="<<mesh.buffers.size()<<'\n';return 0;
}
