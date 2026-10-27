Task #65 recovers four billing wrappers from the hash-pinned Premium ARM ELF.
The implementation and public declarations are source owned. No original
executable, JNI implementation, asset or decompiler export is bundled.

The three integration dependencies in `api.h` must be supplied by independently
implemented billing services. Their original callees are outside this slice.
The two JNI wrappers forward the third argument without dereferencing JNI
handles. Their declared void return types remain inferred. The cleanup's
`char**` type is also inferred; native instructions establish signed count,
pointer stride and array-deallocation semantics. Non-null allocations must be
compatible with `new[]`. A positive count with a null outer array is invalid,
as are short arrays, duplicate owned entries or incompatible allocations.

Run host tests without reference inputs, from the repository root:

```sh
python3 reconstruction/tests/recovered/libNOVA3_neon/block_000d52fc/verify.py \
  --build-dir /tmp/nova65-host
```

For the optional local reference comparison, use an existing Python environment
with pyelftools and Unicorn, and existing `clang++`/`ld.lld` executables:

```sh
python3 reconstruction/tests/recovered/libNOVA3_neon/block_000d52fc/verify.py \
  --reference /path/to/lawfully-held/libNOVA3_neon.so \
  --build-dir /tmp/nova65-check --report /tmp/nova65-report.json
```

The script checks the reference hash and emits aggregate evidence only. It
compiles a research ARM ELF from our source, places the four functions at their
reference virtual addresses, and resolves branch targets to the observed
dependency addresses. Those target implementations are intercepted in isolated
offline emulation. The research ELF is not an application or runtime substitute.
Byte comparisons cover complete linked function bodies with no byte masking,
instruction normalization or reference bytes in the linker script.

Recorded result: all four functions passed 2,624 differential cases comparing
original ARM, compiled ARM and host C++ traces. All three forwarding wrappers
have exact function-body matches under the recorded link placement. Cleanup is
**NOT MATCHED** (80 compiled bytes versus 76 reference bytes); its tested
behavior agrees. Original allocator effects and billing/JNI service behavior,
exception unwinding, a final Android ABI/build, and gameplay are **UNVERIFIED**.

The 2,068 standalone host cases also pass AddressSanitizer and
UndefinedBehaviorSanitizer. LeakSanitizer is disabled because ptrace in the
session prevents it from running. Assertions check ordered deletion of every
owned test allocation, unchanged array slots and payloads at deletion, and
cleanup of deliberately unconsumed fixture allocations. This is not a leak
sanitizer result.

Exact compiler versions, flags, hashes, comparison results, limitations and
handoff are in `coordination/evidence/functions-4592177e5235d9c3f9f6f6.json`.
Build integration outside this block was not allocated. Compile the source and
test as separate translation units; the test supplies dependency spies and
allocation replacements that must never become production implementations.
