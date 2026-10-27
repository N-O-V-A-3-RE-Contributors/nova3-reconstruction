Task #71 recovers four small Premium ARM boundaries. These are standalone
source-owned adapters; they do not implement the installer or billing system.
The source compiles without original game files. Integration must supply the
two declared dependencies from independently reconstructed source.

| ELF VA | Observed behavior | Machine-code comparison |
| --- | --- | --- |
| `0x000d3cd0` | Tail-forward to `nativeStart`, `0x000d3c08` | MATCHED, 4 bytes |
| `0x000d3d0c` | Inactive `drmDebugLog`; temporary argument spill, then return | NOT MATCHED, original 12 bytes / candidate 4 |
| `0x000d4cd4` | Tail-forward to `InAppBilling::setJavaVM`, `0x000d4cc0` | MATCHED, 4 bytes |
| `0x000d4cd8` | Inactive `InAppBilling::launchInAppBilling(char const*)` | MATCHED, 4 bytes |

Reference SHA256:
`84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf`.
Local ELF symbols confirm the requested addresses and sizes, all in ARM mode.
A local DWARF scan found no subprogram with a matching low PC for these four
functions or their two dependencies. Source signatures remain uncertain:
`interface.h` exposes explicit register boundaries, rather than asserting JNI
types or exporting replacement JNI symbols. It covers four argument registers
and the r0 result for installer forwarding, and r0 for VM forwarding. It does
not establish stack arguments, wider results, source return types or exceptions.
The inactive function declarations are inferred source interfaces.

The exact matches use Clang 23.1.3 and LLD 23.1.3, with A32 Cortex-A8 softfp/NEON
and `-O2`. The verifier records all flags, linker placement, source/header/test
hashes and separate reference/candidate code hashes. The comparison linker
assigns external dependency symbols their reference addresses solely to resolve
branch displacements. It links only the newly compiled object. This proves the
stated instruction ranges, not an Android ABI or game-level implementation.

`drmDebugLog` deliberately omits the original writes immediately below SP.
Its tested equivalence excludes dead stack residue and fault behavior with an
unmapped stack. Neither inactive function returns a success status or performs
logging, billing, string reads or null checks. Empty source bodies express the
actual inactive reference behavior rather than substitute implementations.

From the repository root, host-only verification needs an existing g++:

```sh
python3 reconstruction/tests/recovered/libNOVA3_neon/block_000d3cd0/verify.py
```

For the full local comparison, use an existing Python environment with
pyelftools 0.33 and Unicorn 2.1.4, and installed clang++/ld.lld:

```sh
/home/jason/Projects/nova3_re/.venv/bin/python \
  reconstruction/tests/recovered/libNOVA3_neon/block_000d3cd0/verify.py \
  --reference /home/jason/Projects/nova3_re/analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --build-dir /tmp/nova3-task71 \
  --report /tmp/nova3-task71-report.json
```

The completed run passed 100 host cases in optimized, `NDEBUG` and ASan/UBSan
builds, plus host pointer-width checks. LeakSanitizer was disabled for the
session ptrace constraint. The differential oracle compared 4,496 cases per
function (17,984 total) across original ARM, rebuilt ARM and host traces, with
all 16 NZCV combinations. It checks dependency identity/count/arguments and r0
results, restoration of SP and callee-saved registers, and absence of external
memory access. Dependency bodies are intercepted; their effects are unverified.
The original reference is opened read-only, hashed before/after, and its code
remains solely in oracle memory. No reference bytes or decompiler output are
saved by the verifier. Build products and the aggregate report stay in `/tmp`.

Task evidence is in
`coordination/evidence/functions-d3bea957f63bce872aae71.json`. No build-system
integration is allocated in this task. A follow-on assignment should recover
the actual installer/JNI signatures and source implementations of the two
dependencies before application integration. No end-to-end game behavior was
tested here.
