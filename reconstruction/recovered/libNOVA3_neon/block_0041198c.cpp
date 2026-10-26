// Task 34: portable recovery of the payload-opening part of
// CScrambledZipReader::openFile(int), ELF VA 0x0041198c, size 0x260.
// Reference SHA256: 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf.
// NOT MATCHED. This is not the original class ABI or an engine replacement.
// scanLocalHeader (0x00411fc0), init (0x00412730), and
// glitch::res::File::Init (0x0069c6f0) are NOT RECONSTRUCTED here.
//
// Native observations: reader+0x10 points to 16-byte entry records. A record
// contains data offset (+0), output length (+4), packed length (+8), and a
// filename pointer (+12). Bit 31 selects raw DEFLATE; its low 31 bits give
// compressed length. Without bit 31, the packed length is ignored and output
// length is read directly. Successful opening creates an owning memory file.
// inflateInit2 uses windowBits=-15; inflate uses Z_FINISH.
//
// Source API policy: bounded byte views replace virtual seek/read operations;
// std::string/vector replace the original allocator and intrusive pointers.
// Invalid indices, bounds, output limits, incomplete input consumption, and
// incorrect decoded lengths are rejected, leaving output unchanged. These
// additional checks are not claims about the original's malformed-input
// behavior. Like the observed opener, this unit does not check a ZIP CRC.
// The reference executable is needed only for separate research comparisons.

#include <cstddef>
#include <cstdint>
#include <new>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include <zlib.h>

namespace nova3::recovered::block_0041198c {

struct ByteView {
    const std::uint8_t* data;
    std::size_t size;
};

// A semantic record, not a declaration of the original binary structure.
struct Entry {
    std::uint32_t data_offset;
    std::uint32_t output_size;
    std::uint32_t packed_size;
    std::string name;
};

struct MemoryFile {
    std::string name;
    std::vector<std::uint8_t> bytes;
};

enum class OpenError { ok, index, bounds, limit, allocation, inflate };

namespace {
constexpr std::uint32_t deflated = 0x80000000u;

class InflateState {
public:
    z_stream stream{};
    bool initialized = false;

    ~InflateState() {
        if (initialized) inflateEnd(&stream);
    }
};
} // namespace

OpenError open_file(ByteView archive, const std::vector<Entry>& entries,
                    int index, std::size_t output_limit, MemoryFile& output) {
    if (index < 0 || static_cast<std::size_t>(index) >= entries.size())
        return OpenError::index;
    const auto& entry = entries[static_cast<std::size_t>(index)];
    const bool compressed = (entry.packed_size & deflated) != 0;
    const std::size_t input_size = compressed ? entry.packed_size & ~deflated
                                              : entry.output_size;
    if ((!archive.data && archive.size != 0) ||
        entry.data_offset > archive.size ||
        input_size > archive.size - entry.data_offset)
        return OpenError::bounds;
    if (entry.output_size > output_limit) return OpenError::limit;

    try {
        MemoryFile next{entry.name, {}};
        next.bytes.resize(entry.output_size);
        // Avoid pointer arithmetic on a null view even for an empty file.
        const auto* input = archive.data ? archive.data + entry.data_offset : nullptr;
        if (!compressed) {
            if (input_size != 0)
                next.bytes.assign(input, input + input_size);
        } else {
            InflateState state;
            if (inflateInit2(&state.stream, -15) != Z_OK) return OpenError::inflate;
            state.initialized = true;
            state.stream.next_in = const_cast<Bytef*>(input);
            state.stream.avail_in = static_cast<uInt>(input_size);
            // A nonnull pointer gives zlib a valid buffer even when capacity is
            // zero. No writes are permitted in that case.
            std::uint8_t empty = 0;
            state.stream.next_out = next.bytes.empty() ? &empty : next.bytes.data();
            state.stream.avail_out = static_cast<uInt>(entry.output_size);
            const auto result = inflate(&state.stream, Z_FINISH);
            if (result != Z_STREAM_END || state.stream.total_in != input_size ||
                state.stream.total_out != entry.output_size)
                return OpenError::inflate;
        }
        output = std::move(next);
        return OpenError::ok;
    } catch (const std::bad_alloc&) {
        return OpenError::allocation;
    } catch (const std::length_error&) {
        return OpenError::limit;
    }
}

} // namespace nova3::recovered::block_0041198c
