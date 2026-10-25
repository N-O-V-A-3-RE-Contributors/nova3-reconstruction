# Source-built third-party dependencies

## zlib 1.3.2 inflater subset

Source: https://zlib.net/fossils/zlib-1.3.2.tar.gz

SHA256: `bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16`, independently checked against https://www.zlib.net/ on 2026-10-25. `zlib_manifest.json` records individual source/header hashes, version, URL, license and selection. Reproduce with `python3 tools/setup/fetch_zlib.py`; the script validates the pinned archive checksum, size, member paths/types and uncompressed-size limit before copying selected files. Archive executables/install scripts are never run. All 17 retained upstream files are byte-for-byte unchanged. Generated upstream tables `crc32.h` and `inffixed.h` remain source headers; no binary library or executable is vendored.

License: [Zlib](zlib-1.3.2/LICENSE), a permissive source-available license allowing use, modification and redistribution, including commercial use. Preserve the notice, do not misrepresent authorship, and mark any future source modifications. Upstream copyright remains with Jean-loup Gailly and Mark Adler. Project-written CMake/bootstrap/check files are separate from unchanged upstream files. No proprietary original-game code or assets are involved.

Build target `nova3_zlib` is STATIC and position-independent for later Android linkage. Public include path provides `zlib.h`; link with `target_link_libraries(your_target PRIVATE nova3_zlib)` after adding this directory. Six units are compiled: `adler32.c`, `crc32.c`, `inflate.c`, `inffast.c`, `inftrees.c`, `zutil.c`. Recursive quoted includes were inspected and retained, including conditional s390x hooks; s390x acceleration is not enabled. Dependencies are the target platform C standard library and compiler headers. No system libz, generated executable, network service, original APK or native game library is needed to build/run this dependency.

This is an inflater subset. The upstream public header also declares APIs absent from this target: compressor APIs, gz-file wrappers, `uncompress`/`uncompress_z` convenience functions and related units are intentionally excluded. Use the `inflateInit2`/`inflate`/`inflateEnd` API, with `-MAX_WBITS` for raw ZIP DEFLATE. zlib internal checksum routines remain upstream functions; they do not replace the independently reconstructed NOVA CRC implementation. zlib is a dependency, not a recovered Gameloft function, and must not inflate reconstruction coverage totals.

Reproduction and local verification:

```sh
python3 tools/setup/fetch_zlib.py
cmake -S reconstruction/third_party -B build/zlib-standalone -G Ninja
cmake --build build/zlib-standalone --clean-first
ctest --test-dir build/zlib-standalone --output-on-failure
```

The standalone build uses GCC 16.2.1 and passed 1/1 CTest integration test: raw DEFLATE decoded ASCII `hello`; truncated input, reserved block type and bounded output produced expected statuses. This verifies local source build/linkage and basic inflater behavior, not original-game equivalence or complete upstream zlib conformance. `build_check.log` records clean compilation/testing. `link_check.log` shows only libc/platform loader dependencies for the test executable, with no dynamic libz. Tests are added only when this is configured as the top-level CMake project; inclusion in the main reconstruction build provides the library target alone.
