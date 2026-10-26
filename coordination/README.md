# Shared reconstruction task metadata

`function_ledger.json` may be generated from local reference metadata using `python3 -m nova3_team.local_index --project . --write` then reviewed and committed. The importer reads original ELF binaries strictly locally, hashes them, parses locally present Ghidra **metadata** exports, and writes only SHA-256/address/size/boundary-status records. It does not upload game binaries, pseudocode or assets.

`tasks.json` takes precedence if provided: only tasks with `publish: true` and bounded, safe `allowed_paths` are automatically published. Actual model agents must not assert byte-for-byte matching unless independently verified.
