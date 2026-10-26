#pragma once
#include "pvr_texture.hpp"
namespace nova3 {
// Mip offsets in image are relative to this plane's owned PVR bytes, including
// its header; they are never offsets into the enclosing ZIP or resource file.
struct TexturePlane { Etc1Texture image;std::vector<std::uint8_t> bytes; };
struct TextureBundle { TexturePlane rgb,alpha;bool has_alpha=false;std::uint32_t alpha_mode=0; };
enum class TextureError { ok, image, archive, split, unsupported };
// CTextureManager::getTextureInternal0x7b13e0 data-selection portion: SPLIT
// marker must be4 bytes and mode<4; native-sorted first rgb*/alpha* members.
// Alpha-mode shader interpretation, cache and ITexture ownership are excluded.
// MULTI and other image formats are explicitly unsupported in this subset.
// Output is replaced only on success. A failed read of the first selected
// rgb*/alpha* member returns split, without trying later prefix matches. This
// corruption handling is checked-reader policy, not verified native behavior.
TextureError read_texture_bundle(std::vector<std::uint8_t> bytes,TextureBundle& output);
}
