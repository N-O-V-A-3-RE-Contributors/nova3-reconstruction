# Status — source resource, mesh and texture reconstruction

Current source milestone: **named loading from complete Premium OBBs, checked BRES relocation, real inline mesh extraction, actual ETC1 texture pixel decoding, and game-specific ETC filename routing**. No playable game or Android APK exists; no original executable enters production targets.

| Measure | Actual coverage |
| --- | --- |
| Native inventory |76,344 function-symbol rows /73,318 distinct binary+address pairs; four input ELFs |
| DWARF |20 compiler-runtime CUs,276 type-tag records; no recovered game class DWARF |
| Ghidra |62 byte-validated scoped exports; scrambled-archive follow-up completed; persistent project and SQLite saved |
| Complete native mappings |3 original utility functions verified in bounded domains: ComputeCRC,glf CRC32 wrapper,unscramble;317 differential cases |
| Additional source |Checked seekable ZIP index/read regions, native logical filename/heap/mount rules, contiguous BRES byte relocation, inline Collada geometry, PVR header/mip reader, split RGB/alpha texture containers, ETC1 RGB8 decoder, and bounded mode4 texture filename rewrite |
| Package source verification |4,281 index entries and60 named reads match independent inventory/Python ZIP bytes;32 BRES images relocate; first complete archive→name→inflate→CRC→BRES bytes match original ARM golden |
| Mesh source verification |31 first geometries from32 sampled BDAE assets;37,522 vertices/25,289 triangles; positions/indices match native-backed field reader and stored bounds. Remaining sampled resource has0 geometries |
| Native-backed branch evidence |184 lookup/normalization cases+3 duplicate orders;4 BRES bases+7 header variants;12 Collada accessor cases+version;40 GL enum/argument slices;30 stream-base slices |
| Texture source verification |388 original ARM/source header predicate cases; all1,332 texture assets parsed,359 split bundles,1,691 planes,17,505 mip regions independently crosschecked. All17,505 mip regions decode (1,829,884,023 pixels);272 original ARM/source pixel cases match. Rendering pending |
| Texture routing |165 original ARM/source filename cases;6 synthetic ZIP fallback cases and2 actual mesh image names resolve and decode identically to explicit .etc reads |
| Nested SB data source verification |30 archives,4,885 entries,2,703,534,940 decoded bytes; all source header fields and payload size/CRC checks agree with independent Python reader. First mesh effect/shader references located; SDIR/19other GLA variants pending |
| Game functionality |No world/scene renderer, materials/shaders, gameplay, controls, audio, campaign, saves or multiplayer |

Source modules live under reconstruction/engine/; source-built audited zlib under reconstruction/third_party/. Production builds need no original APK or native game library. Actual assets remain data supplied locally. New tools include nova3_resource, nova3_mesh, nova3_package_probe, nova3_bres_relocate and nova3_texture. First real geometry artifact build/room_a.obj has12vertices/6triangles; build/room_a_preview.svg is an explicitly diagnostic projection, not a game frame or recovered materials.

Validation at this checkpoint: host CTest13/13; Python guard/archive13/13; sanitizer ASan/UBSan13/13; clean source-only release build/tests13/13; latest ARMv7 source build succeeded, no device run. Extended native oracles and source-only artifact checks pass (exit0). See .cache/logs/scrambled-final-{checks,clean,sanitizer,armv7}.log. Source guard rejects both actual original APKs and all4 ELF inputs. Final APK/DEX/linker/source provenance and standard-runtime source-build/license audit remain pending; current guard is a packaging regression check, not final certification.

Native-equivalence scopes are limited deliberately: ZIP logic does not reproduce COW string/allocator ABI; BRES covers contiguous byte transformation and rejects control-field aliases; mesh reader covers inline tag0/interleaved primary float streams/triangle lists/serialized buffers, excludes skin/controller and shared runtime ownership. Shader attribute roles and normalized byte semantics remain unverified. Exact evidence: research/architecture/RESOURCE_SLICE.md, research/assets/{BRES,COLLADA,GL_ENUMS}.md, research/elf/*verification.json.

Premium package/version mismatch remains: APK GAND.GloftN3LT105/v1.0.8e versus expansions ANMP.GloftN3HM1050/1070. Resource-byte compatibility has concrete samples, but whole-game edition parity is unverified. ADB has no attached device. rev.ng probes timed out with0 successful C output. Original hashes remain preserved; optional asset OBB copies live only in ignored .cache/assets.

Resource reader milestone committed as bbb7447. Texture decoder extension has passing tests and inspectable raw RGB/alpha PPM/PNG outputs under build/; these are texture data, not game frames. ETC1 tiny-mip clipping and invalid differential rejection are explicit source policies beyond the tested native branch.

ETC1 decoder checkpoint committed as d57b632. Texture request routing is now implemented for an explicit ETC profile; full device quality/normal/shader aliases remain incomplete. Nested SB data now has source header/payload recovery; first-mesh effect references found. Next dependencies are actual material/technique/semantic binding, original scene transforms, and a newly implemented renderer.

Source implementation accounting:13 owned engine .cpp modules, comprising3 complete native utility mappings and17 additional scoped address-to-source mappings; mapping counts are not whole subsystem completion. Ghidra62 scoped exports are decompilation evidence. No end-to-end game functionality exists. Current source milestone builds independently for host and Android ARMv7, with no original executable inputs.
