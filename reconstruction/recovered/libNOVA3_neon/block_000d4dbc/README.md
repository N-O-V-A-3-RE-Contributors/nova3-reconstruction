# Task 69: billing veneers, decimal conversion and JNI exception helper

This bounded recovery implements the four assigned entry points in
`../block_000d4dbc.cpp`. The header declares two external billing dependencies;
their bodies are **not reconstructed** here. The standalone tests provide test
doubles, never a production billing stub. No reference library is linked into
the source build or required by these source-owned implementations.

Observed A32 ELF symbols in the hash-pinned Premium reference:

| Address | Symbol | Size | Recovery |
| --- | --- | --- | --- |
| 0x000d4dbc | InAppBilling_ClearItemList | 4 | Forward to clearItemList at 0x000d4d58 |
| 0x000d4de4 | charToUI64 | 172 | Floating place-value accumulation into uint64 output |
| 0x000d4f74 | JNU_ThrowByName | 80 | FindClass, conditional ThrowNew, unconditional DeleteLocalRef |
| 0x000d5224 | InAppBilling_GetState | 4 | Forward to getState at 0x000d5148 |

The output/length/input interpretation of `charToUI64` follows register and
memory use. The original source return types of this function and the JNI
helper are unavailable; `void` is inferred. Incidental return-register contents
are outside their recovered contracts. `int` on the billing veneers preserves
the forwarded register; the original source return type of clearItemList is
uncertain. These declarations are not proof of complete original ABI recovery.

The numeric function initializes the output before reading any input. For
positive signed lengths, it evaluates `pow(10, length-1-i)` for each byte, reads
that byte as signed, subtracts 48, multiplies, adds the current accumulator
converted to double, then converts back and stores. It accepts neither a
validated decimal grammar nor a NUL-terminated string. Overlapping input/output
therefore has observable semantics. Nonpositive lengths write zero without
reading input. Rounding beyond 2^53 is intentional. Build without fast-math and
with `-ffp-contract=off`.

The ARM unsigned conversion helper at 0x00be323c uses saturating 32-bit halves.
The source expresses its combined binary64 result with defined C++ casts:
negative values and NaNs become zero, positive overflow becomes UINT64_MAX.
Tests directly execute the original helper separately from the numeric loop.

## Reproduce

Already installed tools used in this session: GCC 16.2.1, Clang 23.1.3 for
host sanitizers, Android NDK r29 (29.0.14206865, Clang 21.0.0), Unicorn 2.1.4,
pyelftools 0.33. No installation or network access is performed by these tests.
The production source needs C++17, platform JNI headers and platform libm.
JNI headers come from the source-available Android platform (Apache-2.0);
no JNI header or third-party runtime is vendored in this block.

From the repository root, without reference files:

```sh
bash reconstruction/tests/recovered/libNOVA3_neon/block_000d4dbc/verify.sh
```

To include offline reference comparison using locally installed Python modules:

```sh
NOVA69_PYTHON=/home/jason/Projects/nova3_re/.venv/bin/python \
bash reconstruction/tests/recovered/libNOVA3_neon/block_000d4dbc/verify.sh \
  --reference /home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so
```

`NOVA69_NDK` overrides `/opt/android-ndk`; `CXX` overrides `g++`. Only the
optional oracle reads the reference file. It checks its SHA256, reads loadable
segments into emulator memory, and emits aggregate counts and hashes, never
reference instruction bytes or decompiler exports. Original code is not patched.
All generated artifacts live in a temporary directory removed on exit. The
standalone test includes the implementation: do not additionally link its object.
The test DSO also contains test-only billing doubles; it is not a production
library. Sanitizer leak detection is disabled because of sandbox ptrace limits;
ASan and UBSan remain enabled. Host JNI headers use `-idirafter` so Android libc
headers do not override the host libc headers.

## Evidence and limits

The two veneers have exact four-byte A32 matches with NDK Clang and
`-fomit-frame-pointer`, linked at the reference entry addresses. The comparison
linker script resolves only external function addresses, never original code.
Its output is a comparison fixture, not a runnable billing library. No matching
claim extends to either billing target. Without frame-pointer omission, the
compiler emits extra frame setup and the wrappers do not match.

`charToUI64` and `JNU_ThrowByName` are **NOT MATCHED**: the ARM candidates have
different instruction regions and sizes. Passing emulator comparisons establish
only the recorded scope. Numeric comparisons supply host glibc libm's `pow` at
the original import boundary, so original Android libm rounding/overflow/errno
and floating exceptions remain **UNVERIFIED**. JNI tests supply synthetic
FindClass/ThrowNew/DeleteLocalRef calls and compare order, arguments and null
handling. VM exception internals and real Java billing behavior are untested.
Default nearest rounding is tested; asynchronous/reentrant mutation is excluded.
No Android APK, full game, renderer or integration test is produced here.

The machine-readable evidence is
`coordination/evidence/functions-be7f8ec1c46c9e9168d276.json`. It contains the
source hashes, exact compiler flags, comparison hashes, case counts and handoff.
Next work should obtain the original libm behavior and callers/type evidence
for the inferred signatures, then reconstruct the two billing target bodies in
a separately authorized task. Preserve NOT MATCHED for the two larger bodies
until a reproducible full-region match exists.
