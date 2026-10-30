// CColladaDatabase::getVersion layout recovery.
//
// Reference ELF: Premium retail lib/armeabi-v7a/libNOVA3_neon.so
//   SHA-256 84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf
//
// The A32 window at ELF VA 0x0071017c (20 bytes, symbol
// `_ZNK6glitch7collada16CColladaDatabase10getVersionEv`) is a four-level
// pointer walk:
//
//   ldr r3,[r0]        ; r3 = *(this)             (root pointer at +0x00)
//   ldr r3,[r3,#0x10]  ; r3 = *(r3 + 0x10)        (pointer at +0x10)
//   ldr r3,[r3,#0x20]  ; r3 = *(r3 + 0x20)        (pointer at +0x20)
//   ldr r0,[r3]        ; r0 = *(r3)               (version word at +0x00)
//   bx  lr
//
// The chain node offsets (0x10, then 0x20, then 0x00) are expressed below
// as explicitly indexed 32-bit loads so a hostile/incorrect reconstruction
// that mistakes an offset is caught by the behavioural test, not silently
// accepted.

#pragma once

#include <cstdint>

namespace nova3::recovered::collada {

// External chain node: an opaque word-addressed record.
using ChainNode = std::uint32_t;

// Returns the version word reached by the four-level walk documented above.
inline std::uint32_t get_version(const void* database) noexcept {
    const auto* root = static_cast<const std::uint32_t*>(database);
    const auto* node1 = reinterpret_cast<const std::uint32_t*>(root[0]);       // +0x00
    const auto* node2 = reinterpret_cast<const std::uint32_t*>(node1[4]);      // +0x10
    const auto* node3 = reinterpret_cast<const std::uint32_t*>(node2[8]);      // +0x20
    return node3[0];                                                            // +0x00
}

}  // namespace nova3::recovered::collada