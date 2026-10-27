Task #64: billing forwarding boundaries
=====================================

The interrupted C++ implementation and header are preserved. The test's three
mixed `nullptr`/`void*` initializer lists were repaired so it actually compiles.
No original executable, decompiler text, asset or original billing implementation
is included. Dependencies remain unresolved in production; only tests supply
recording doubles. Integration must provide source implementations.

Recovered boundaries (ELF virtual addresses, ARM mode):

| Address | Source entry | Observed forwarding |
| --- | --- | --- |
| 0x000d60d0 | `native_init` | Drop environment; forward class/object to 0x000d58c0. |
| 0x000d6bc8 | `restore_transactions` | Tail dispatch to 0x000d6a98. |
| 0x000d6e30 | `send_notify_confirmation` | Forward borrowed text pointer to 0x000d6cdc. |
| 0x000d6f88 | `get_item_list` | Forward borrowed text pointer to 0x000d6e34. |

`ResultBits` represents the forwarded 32-bit ARM result register. Original return
types and their meaning are unknown. The namespaced public API and dependency
names are source-owned integration seams, not recovered JNI/C++ declarations.
The wrappers do not validate or read their arguments.

Reproduction from the repository root, with existing offline tools:

```sh
g++ -std=c++17 -O2 -DNDEBUG -Wall -Wextra -Wpedantic -Werror \
  reconstruction/recovered/libNOVA3_neon/block_000d60d0.cpp \
  reconstruction/tests/recovered/libNOVA3_neon/block_000d60d0_test.cpp \
  -o /tmp/nova3-task64-test
/tmp/nova3-task64-test

python3 reconstruction/tests/recovered/libNOVA3_neon/block_000d60d0/verify.py \
  --reference /home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --oracle --sanitizers --output /tmp/nova3-task64-report.json
```

The host tests require no originals. The optional verification driver needs
pyelftools 0.33, Capstone 5.0.7 and, with `--oracle`, Unicorn 2.1.4. It uses GCC
16.2.1, Clang/LLD 23.1.3 as recorded in the evidence. No installation or network
access is performed. It rejects a reference with the wrong SHA256 before builds.
The verifier creates and deletes only temporary source-compiled build artifacts.

Actual results: 291 checks passed with GCC release and Clang ASan/UBSan; 2,048
original/candidate ARM forwarding cases passed (512 per function). Leak checking
is disabled for the sanitizer invocation; no leak-check result is claimed.
All 24 wrapper bytes matched exactly after LLD placed source-compiled functions
at the reference addresses and resolved source dependency names to the observed
absolute callee addresses. No bytes were masked, patched or normalized. Compiler
flags, source/reference/region hashes and executed commands are recorded in
`coordination/evidence/functions-2243f3be0ab59188729029.json`.

The fixed-address link output is a comparison probe, not a usable billing library:
it contains no callee implementations and is never linked into the game. It has
different exported names. The oracle executes only each wrapper in isolated test
memory and replaces its entire billing callee with a register-recording hook.
Consequently, the exact matches cover the wrapper instruction bodies only.
Full library: **NOT MATCHED**. Original native ABI and billing: **UNVERIFIED**.
No Android build, Java call, purchase, restore, network request or gameplay is
tested. No end-to-end game functionality is produced by this slice.

Handoff: no CMake or global-header changes were allowed. Compile the production
and test translation units together using the command above. Next work needs
separate allocation to recover the four downstream InAppBilling methods, establish
their true return types/JNI contracts, then wire source-owned dependencies and
exported ABI integration. Existing source and tests outside task paths were not
changed. No packages, host settings or GitHub state were changed.
