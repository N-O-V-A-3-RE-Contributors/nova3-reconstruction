#pragma once
#include "texture_bundle.hpp"
#include "zip_archive.hpp"
#include <string>
namespace nova3 {
// CCustomFileSystem ETC profile: ordered case-sensitive substring search,
// replacing the selected extension and everything after it. No path stripping.
std::string etc_texture_path(const std::string& name);
// Bounded profile only: texture rewrite enabled, mode=ETC, no low-quality
// normal-map suppression or game-specific alternate shader paths.
struct TextureResourceResult {
    ArchiveError archive=ArchiveError::ok;
    TextureError texture=TextureError::ok;
};
// Try the rewritten name across the mounts first, then the original name only
// if the resource read failed. A successfully read but invalid image does not
// trigger fallback. Output is replaced only when both reading and parsing pass.
TextureResourceResult load_etc_texture(ResourcePackages& packages,const std::string& name,TextureBundle& output);
}
