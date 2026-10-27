// Portable semantic adapters for task #37, not replacements for the ARM ABI.
// Reference: libNOVA3_neon.so, SHA256
// 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// Matching status: NOT MATCHED. See the task's coordination/evidence JSON.
//
// 0x002af5c4, CCustomResFactory::getTexture(...).clone.36:
// NOT RECONSTRUCTED. In particular, the callback below does not implement its
// hash-name lookup, intrusive ownership, filter/anisotropy or device policies.
// No success stub or guessed texture implementation is supplied here.

#include "../../engine/scramble.hpp"

#include <array>
#include <cstdint>

namespace nova3::recovered::block_002af5c4 {

// A view, not a recovered SImage layout. The ARM wrapper loads the filename
// pointer from image+8. No other SImage members/layouts are established here.
struct ImageReference {
    const char* filename;
};

// Explicit destination represents the ARM hidden result pointer. Ownership and
// construction belong to the lookup dependency; this adapter never copies or
// destroys the result. A callback return value is deliberately ignored.
struct TextureLookup {
    void* context;
    void* (*get_texture)(void* context, void* destination, const char* filename);
};

// 0x002af8b8, CCustomColladaFactory::createImage.
// Database and video-driver arguments are unused in this specialized wrapper,
// and are omitted from this portable interface. Destination, image and callback
// must be valid. The filename is passed through, including a null filename.
void* create_image_into(void* destination, const ImageReference& image,
                        const TextureLookup& lookup) {
    lookup.get_texture(lookup.context, destination, image.filename);
    return destination;
}

// Semantic test seam, not an ABI-compatible recreation of glitch::io::IReadFile.
// Positions preserve the 32-bit ARM bit pattern, including high-bit values.
// Implementations must fill all four requested bytes for the reference-backed
// domain. A return count alone does not establish whether bytes were written.
class FileProbe {
public:
    virtual ~FileProbe() = default;
    virtual std::uint32_t position() = 0;
    virtual bool seek(std::uint32_t offset, bool relative) = 0;
    virtual std::uint32_t read(std::uint8_t* destination, std::uint32_t count) = 0;
};

// 0x00411518, CScrambledZipReader::isValid (static: file arrives in ARM r0).
// Observed virtual slots: position=9, seek=6, read=3. Always restores the saved
// position with an absolute seek, even when the first seek/read reports failure.
// It compares the bytes supplied by read, ignoring all three return values.
//
// Safety extension: zero initialization avoids the original uninitialized stack
// read after a short write. Equivalence is NOT claimed when read writes fewer
// than four bytes; no archive-length or full-format validation is implied.
bool is_valid_scrambled_zip(FileProbe& file) {
    const auto saved_position = file.position();
    (void)file.seek(0, false);
    std::array<std::uint8_t, 4> header{};
    (void)file.read(header.data(), 4);
    (void)file.seek(saved_position, false);
    return header[0] == 'S' &&
           (header[1] == 'B' ||
            (header[1] == 'E' && header[2] == 'N' && header[3] == 'D'));
}

// 0x00411610, CScrambledZipReader::unscramble.
// Reuse the existing independently compiled reconstruction. On the reference
// domain (valid buffers and a nonempty, nonoverlapping NUL-terminated key), this
// performs in-place XOR with a repeating key, restarting at key[0] each call.
// The bool result/invalid-input rejection are the existing checked source API,
// not evidence about the original void-returning routine's invalid inputs.
bool unscramble(std::uint8_t* data, std::uint32_t count, const char* key) noexcept {
    return nova3::unscramble(data, count, key);
}

} // namespace nova3::recovered::block_002af5c4
