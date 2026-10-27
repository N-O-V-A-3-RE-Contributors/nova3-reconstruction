Task #70 reconstructs four A32 forwarding boundaries from the local Premium
reference with SHA256
`84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`.
The source does not implement billing, purchases, JNI, or result allocation.

| ELF virtual address | Entry point | Dependency address | Boundary behavior |
| --- | --- | --- | --- |
| 0x000d80c0 | InAppBilling_FillIdArray | 0x000d7e5c | Forward output pointer; transport dependency r0 bits |
| 0x000d82e4 | InAppBilling_GetHeaders | 0x000d80c4 | Call with output storage; ignore dependency r0; return original storage pointer |
| 0x000d8580 | InAppBilling_FillBillingAttArrayByName | 0x000d82f8 | Forward output and name pointers; transport dependency r0 bits |
| 0x000d880c | InAppBilling_FillAttributeArrayByName | 0x000d8584 | Forward output and name pointers; transport dependency r0 bits |

The task's `14` size for GetHeaders is hexadecimal: local ELF symbol tables
identify a complete 20-byte (0x14) function. The other three functions are
four bytes each. Return types are register-transport models, not recovered
original C++ types. Mangled dependency names identify pointer parameters;
they do not establish return types, instance/static declarations, ownership,
or the layout and lifetime of the opaque GetHeaders result storage.
The wrappers add no validation. Null/opaque pointers in tests establish
forwarding only, not that the actual dependencies accept those values.

From the repository root, run the source-only test without any original:

```sh
cmake -S reconstruction/tests/recovered/libNOVA3_neon/block_000d80c0 -B /tmp/nova3-task70-build
cmake --build /tmp/nova3-task70-build
ctest --test-dir /tmp/nova3-task70-build --output-on-failure
```

The offline research verifier additionally needs the lawfully held local ELF,
existing pyelftools/Unicorn, GCC, Clang with ARM support, and LLD:

```sh
/home/jason/Projects/nova3_re/.venv/bin/python reconstruction/tests/recovered/libNOVA3_neon/block_000d80c0/verify.py --reference /home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
```

It compiles the source, resolves its declaration-only dependency symbols to
observed addresses in a temporary analysis ELF, and compares complete function
regions without masking bytes. It then compares original/rebuilt wrappers in
isolated ARM emulators with dependencies intercepted. Reference bytes remain
in memory; reports contain only hashes, metadata and results. This temporary
placement is a comparison step, not a production linker recipe or game runtime.

See `coordination/evidence/functions-cc73d4b062bb2e03ec0ba4.json` for executed
checks, compiler flags, hashes, matching scope and handoff. Integration requires
four independently reconstructed dependency implementations. Their behavior,
the original result ABI, exception metadata and gameplay remain UNVERIFIED.
