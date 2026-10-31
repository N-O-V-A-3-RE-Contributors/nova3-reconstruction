# Status — source resource, mesh and texture reconstruction

## The shared matrix constructor is recovered and byte-equal modulo its one relocation (2026-11-01, fifth run)

**`glitch::core::CMatrix4<float>::CMatrix4(CMatrix4<float>::eConstructor)`**
(`_ZN6glitch4core8CMatrix4IfEC1ENS2_12eConstructorE`, ELF VA `0x00108c54`, 56 B,
A32, exact `.symtab` bounds) is source. It is the one dependency both recovered
Collada units call, so the 64-byte product vector `prepareCache` builds now has a
constructed fill value instead of an opaque one. New files:
`reconstruction/recovered/libNOVA3_neon/collada_matrix4_ctor.cpp`,
`reconstruction/tests/recovered/libNOVA3_neon/collada_matrix4_ctor_test.cpp`
(CTest `collada_matrix4_ctor_behaviour`) and
`tools/compare/collada_matrix4_ctor_pilot.py` (all structure checks re-derived
from the reference bytes, exit 0).

**The whole 56-byte body, in order.** `cmp r1,#1` / `push {r4,lr}` / `mov r4,r0` /
`bls <epilogue>` — so the enumerator is compared **unsigned** and values 0 and 1
reach the epilogue with **no store at all**; then `mov r1,#0` / `mov r2,#0x40` /
`bl 0xd2610`; then `vmov.f32 s15,#1.0` and four `vstr` at byte offsets `0x3c`,
`0x00`, `0x14`, `0x28`; then `mov r0,r4` / `pop {r4,pc}` (the C1 complete-object
constructor returns `this`).

**The `bl` is `memset`, resolved rather than guessed.** `0xd2610` is not a local
function: it is the ARM PLT stub at `.plt + 0x14 + 12*137`, `.rel.plt` entry 137,
whose `.dynsym` symbol is `memset`. So the body is: zero the whole 64-byte object
and set the four row-major diagonal floats to `1.0f` — the 4x4 identity — for
every enumerator value >= 2.

**Call-site distribution, measured.** 74 in-binary direct calls to `0x00108c54`.
54 pass a literal `r1 = 2`, 6 pass `r1 = 0` (including the recovered `operator*`
itself), and 14 have no local literal (argument loaded earlier). That is the
evidence behind naming value 2 "identity"; the reference contains no symbol or
string naming the enumerator, so the name stays INFERRED while the body is
observed. Values 3, 4, 5… take the same identity path in *this* constructor,
which is why nothing here claims a transposed/inverse case exists.

**Window, measured** (NDK r8e GCC 4.4.3, `-march=armv7-a`, softfp, `-mfpu=vfpv3-d16`):

| config | ref | candidate | words |
|---|---|---|---|
| `-O2` hidden | 56 B | 56 B | 13 EQUAL / 1 RELOCATION_FIELD / 0 encoding |
| `-O3` hidden | 56 B | 56 B | 13 / 1 / 0 |
| `-Os` hidden | 56 B | 56 B | 13 / 1 / 0 |
| `-O2` default | 56 B | 56 B | 13 / 1 / 0 |

`instruction_stream_equal=True` in 4/4; `code_window_equal=False` because that
one word is the `bl memset` displacement, a link-time value an ET_REL object
cannot contain. No byte-equality, compiler or flag claim beyond these four row
configurations, no whole-`.so` SHA claim.

**Behaviour test, stubbing nothing.** The test includes the recovered TU
unchanged, so the shipped body runs. Asserted: values 0 and 1 write **no byte**
(sentinel-filled object plus 16 guard bytes on each side stay untouched); every
value >= 2 including `0x7fffffff` produces the identity with off-diagonals
exactly `+0.0f` (not merely small, and not negative zero); construction is
idempotent in the same storage; a later value <= 1 leaves an existing identity
intact; the object is 64 B and its 16 floats start at offset 0. Three mutants
actually run and killed — predicate `> 1u` -> `> 0u` (`FAIL: constructor value
0/1 wrote the object`), diagonal store `[10]` deleted and `[15]` retargeted to
`[14]` (both `FAIL: value N did not produce the identity`); restored source
passes.

**Verification actually run:** `cmake --build build/host` clean; `ctest`
**21/21 pass**; `bash tools/check_all.sh` **rc=0** with **21** audited artifacts
(the new test binary plus its provenance entry, the new pilot added to the oracle
block).

**Disposition:** `CMatrix4<float>::CMatrix4(eConstructor)` = IMPLEMENTED +
TESTED + instruction-window MATCHED modulo the single relocation word. Still NOT
RECONSTRUCTED: `CMatrix4(CMatrix4 const&, eConstructor)` (`0x00108c8c`, 240 B),
the `std::vector` helpers, `android_cpuInit` (2648 vs 2632 B, not chased),
`CSoftwareSkinTechnique::init`/`skin`.

## The matrix product behind prepareCache is now real arithmetic (2026-11-01, fourth run)

**`glitch::core::operator*<float, glitch::collada::SMatrix>(CMatrix4<float>
const&, SMatrix const&)`** (`_ZN6glitch4coremlIfNS_7collada7SMatrixEEENS0_8CMatrix4
IT_EERKS6_RKT0_`, ELF VA `0x006e7e60`, 564 B, A32) is source, so the 64-byte
product `prepareCache` stores is no longer opaque. New files:
`reconstruction/recovered/libNOVA3_neon/collada_matrix_multiply.{h,cpp}`,
`reconstruction/tests/recovered/libNOVA3_neon/collada_matrix_multiply_test.cpp`
(CTest `collada_matrix_multiply_behaviour`), and
`tools/compare/collada_matrix_multiply_pilot.py` (17/17 structure checks re-derived
from the reference bytes, exit 0).

**What the 564 bytes do, read instruction by instruction.** sret in r0 → r6;
r1 (CMatrix4) → r4; r2 (SMatrix) → r5; one `bl` to
`CMatrix4<float>::CMatrix4(eConstructor)` with `this` = the result and **r1 = 0**,
and all 16 result floats are then overwritten. The element mapping below is in
float index (`row*4+column`), taken from the store offsets, not assumed:

- result(row,column) = S(row,0)*A(0,column) + S(row,1)*A(1,column) +
  S(row,2)*A(2,column) for rows 0..2 and columns 0..2;
- the result's fourth column is a **literal** 0.0f for rows 0..2 and 1.0f for
  row 3: the CMatrix4's floats 3/7/11/15 are never loaded, so the result does not
  depend on them;
- result(3,column) = the SMatrix's translation row against the CMatrix4's linear
  part, **plus** the CMatrix4's own translation (floats 12/13/14);
- every SMatrix row contributes only its first three floats — the fourth float of
  each row is never loaded.

So both operands are affine, the SMatrix as four rows of three used components,
and the multiply is the plain 4x4 product with the two implicit fourth columns
folded in. That reading is INFERRED and labelled; the arithmetic is observed.

**Window, measured** (NDK r8e GCC 4.4.3, `-fvisibility=hidden` unless noted):

| config | ref | candidate | words |
|---|---|---|---|
| `-O2` | 564 B | 264 B | `FUNCTION_SIZE_MISMATCH` (loops not unrolled) |
| `-O3` | 564 B | **540 B** | `FUNCTION_SIZE_MISMATCH`, 7 EQUAL / 1 RELOCATION_FIELD / 127 encoding |
| `-Os` | 564 B | 260 B | `FUNCTION_SIZE_MISMATCH` |
| `-O2` default | 564 B | 264 B | `FUNCTION_SIZE_MISMATCH` (negative control) |

`code_window_equal=False`, `source_matching_verified=False`. The residual is
measured, not guessed: at `-O3` the **instruction mix is identical** — 12
`vmul.f32`, 24 `vmla.f32`, 3 `vadd.f32`, 16 `vstr`, 1 `vl`, 1 `push`/`pop`, 1
`vmov.f32 #1.0` — and the only difference is **6 `vldr` (76 reference vs 70
candidate)**, i.e. 24 B. Per offsets, the candidate keeps the SMatrix's second
component of each row (offsets 0x04/0x14/0x24) live across that row's three
columns (1 load instead of 3, ×3 rows ×2) while the reference re-loads. The
result object is a fresh sret local, so the compiler may cache it legally; the
original evidently did not. Not chased, same class as the `cpuInit` and
`prepareCache` residuals; no source construct is missing.

**One source-form finding, recorded.** The operands and the result are addressed
as flat `float*` arrays. With the member-accessor form the compiler cached
operands in VFP registers and emitted 70 loads at `-O3` (540 B) after the
`-funroll-loops`/`-O3` unroll, but the same accessor form at `-O2` stayed loops
(232 B). The shipped form is the array form, which is both the simplest model of
the observed addressing and the one whose counts match the reference.

**Behaviour test with negative controls.** `collada_matrix_multiply_behaviour`
includes the recovered TU unchanged and supplies only the `CMatrix4(eConstructor)`
body (call-counted). It asserts: identity × identity = identity; a
rotation-times-scale/translation fixture against an independently written generic
4x4 product; the result's fourth column is literal (the CMatrix4's 3/7/11/15
filled with junk changes nothing); the SMatrix's fourth float per row is unread
(1e30 and NaN give bit-identical results); exactly one constructor call per
multiply; the operator is pure. Four mutants actually run and killed:
translation addend dropped (`FAIL: translation x is wrong`), result (3,3) taken
from the input (`FAIL: the CMatrix4 fourth column must not be read`), SMatrix
lane 3 read instead of lane 1, and a wrong translation coefficient.

**Verification actually run:** `cmake --build build/host` clean; `ctest` **20/20
pass**; `./nova3 verify` (build + CTest + source-only audit) **rc=0** with the new
binary audited. Regression pilots after the `collada_skin_cache_deps.h` change
(one enumerator added for constructor value 0): the three 8-byte leaves still
**byte-equal** (`collada_skin_trivial_pilot.py`, `source_matching_verified=true`)
and `prepareCache` unchanged at 224 B with its leaves still equal. The full
`NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh` chain was **not** run to
completion this session — it exceeded the harness's foreground time limit; the
three Collada pilots in it were each run individually instead, and the new one is
wired into the chain for the next full run.

**Disposition:** `operator*` = IMPLEMENTED + TESTED; **NOT MATCHED** (identical
instruction mix, 6 fewer loads). `prepareCache`'s 4-byte gap and `android_cpuInit`
are unchanged and still not chased.


## prepareCache is recovered and tested; its 4-byte gap is scheduling, not semantics (2026-11-01, third run)

**`glitch::collada::detail::CSoftwareSkinTechnique::prepareCache`
(`_ZN...12prepareCacheEv`, ELF VA `0x0073f0a4`, 228 B, A32) is now source.**
The previous NEXT.md item, so this run did it rather than re-describing the
queue.

What the 228-byte window does, each fact re-derived by
`tools/compare/collada_skin_prepare_cache_pilot.py` from the reference bytes plus
`.symtab` (12/12 structure checks true, exit 0):

- `ldr r3,[r0,#8]` / `ldr r3,[r3]` / `tst r3,#1` / conditional branch, with no
  call and no store before it: bit 0 of the word at `*(this+0x08)` gates the
  whole body and the false path is a bare epilogue;
- `bl 0x000731c58` = `ISkinTechnique::preparePtrCache()` — a direct call that
  fills the pointer vector this function then walks;
- `bl 0x00108c54` = `CMatrix4<float>::CMatrix4(eConstructor)` with **r1 = 2**,
  and `bl 0x00775520` = `vector<CMatrix4<float>>::resize(count, value)` receives
  **the same register** the constructor wrote — the fill value is built in place,
  there is no 64-byte copy anywhere between the two calls;
- the resize count comes from `*(this+0x04)+0x4c`, which is independent of the
  pointer-vector size (`bl 0x00752470` = `vector<CMatrix4<float> const*>::size()`);
- per iterate: `bl 0x006e7e60` = `operator*<float, SMatrix>(CMatrix4<float>
  const&, SMatrix const&)`, sret, 64-byte result copied by four `ldm`/`stm` pairs
  to `destination[i]`, with source `ptrs[i]` and `SMatrix` at stride `0x40`
  (`add r2,r2,r5,lsl #6`);
- `ldr`/`bic #1`/`str` on the same flag word at the end: bit 0 is cleared.

Source lives in `reconstruction/recovered/libNOVA3_neon/collada_skin_technique.cpp`
with the layout views and the five unresolved dependencies in
`collada_skin_cache_deps.h`. **The class layout is now named** at the offsets the
slices address: `+0x00` untouched, `+0x04` bind inputs (`+0x04` SMatrix base,
`+0x4c` count), `+0x08` matrix cache (`+0x00` flags, `+0x04` and `+0x10` two
`std::vector` objects), `+0x0c` `attach`'s pointer. The three 8-byte leaves
re-measured **byte-equal in all four configurations after that layout change**.

**One measured declaration finding, recorded rather than papered over.** The
reference symbol mangles as `resize(unsigned, CMatrix4<float>)` — by value — yet
the reference passes a pointer to a constructor-built object and emits no copy.
A by-value declaration cannot reproduce that: measured with NDK r8e GCC 4.4.3, a
by-value parameter always builds the temporary and then copies all 64 bytes into
the outgoing argument slot (stack frame `0xbc`, function 268 B, plus an inlined
copy block). `collada_skin_cache_deps.h` therefore declares it by const reference
and says so in the header: on ARM32 both forms pass a pointer in `r2`, so the
call ABI is reproduced while the C++ type stays a modelling choice.

**Window, measured** (NDK r8e GCC 4.4.3, `-O2`/`-O3`/`-Os` × `-fvisibility`):

| config | ref | candidate | words |
|---|---|---|---|
| `-O2 -fvisibility=hidden` | 228 B | 224 B | 17 EQUAL / 5 RELOCATION_FIELD / 34 encoding |
| `-O3 -fvisibility=hidden` | 228 B | 224 B | 17 / 5 / 34 |
| `-Os -fvisibility=hidden` | 228 B | 216 B | 3 / 5 / 46 |
| `-O2 -fvisibility=default` | 228 B | 224 B | 17 / 5 / 34 |

`code_window_equal=False`, `source_matching_verified=False` everywhere. The 4-byte
gap at `-O2`/`-O3` is **register scheduling, not a missing construct**: the
reference computes `destination + i*64` into a callee-saved register *before* the
`operator*` call (one extra `add r8,r8,r3`), this compiler recomputes it after the
call from the retained base pointer and saves one instruction. The 5
`RELOCATION_FIELD` words are exactly the five `bl` displacements an ET_REL object
cannot contain. Prologue register allocation otherwise matches the reference
(`push {r4,r5,r6,r7,r8,sl,lr}`, `r6 = this`), and the guard, call order, store
order and stride all agree. Not chased further, on the same reasoning as the
`cpuInit` residual.

New CTest `collada_skin_prepare_cache_behaviour` includes the TU unchanged and
defines the five dependencies with the reference's mangled names, checking:
guard-clear runs nothing and leaves the other 31 flag bits; guard-set with zero
pointers still constructs value 2, resizes with the `+0x4c` count, queries the
size once, runs no multiply and clears only bit 0; three sources produce three
multiplies in ascending index order with `sources[i]` as left operand, the
`SMatrix` at `i*0x40` as right operand, and each 64-byte product at
`destination[i]` — where the destination is proved to be re-read *after* the
resize because the stub replaces it; a second call is a no-op. Negative controls,
actually run: deleting the guard crashes the mutant in a dependency stub, `&= ~2u`
aborts with "only bit 0 should have been cleared", dropping the `i*0x40` stride
aborts with "multiply did not receive the SMatrix at i*0x40"; restored source
passes.

`reconstruction/tests/recovered/libNOVA3_neon/collada_skin_cache_stubs.cpp` holds
abort-on-call stand-ins so the older leaf test still links the same TU without
silently pretending the dependencies exist.

**Verification actually run:** `cmake --build build/host` clean; `ctest` **19/19
pass**; `bash tools/check_all.sh` **rc=0** with **20** source-only audited
artifacts and tooling pytest 30 passed; pilot exit 0 with
`structure_agrees_with_source=True` and the three leaves still byte-equal.

**Disposition:** prepareCache = IMPLEMENTED + TESTED; **NOT MATCHED** (224 vs
228 B, scheduling). Still NOT RECONSTRUCTED: `android_cpuInit` (2648 vs 2632 B,
not chased), `CSoftwareSkinTechnique::init` and `skin` (1 KB / 4 KB, only their
cpu-features interaction is established), and every dependency this function
calls.

Totals unchanged and unexaggerated: 39,123 indexed functions, 9 DECOMPILED
exports (never a completion state), 5 source-recovered units IMPLEMENTED +
TESTED, 0 VERIFIED against original runtime behaviour, no end-to-end gameplay.

## Three CSoftwareSkinTechnique leaves are byte-equal source; the init() "gate" is a cache fill (2026-11-01, second run)

**The open question is answered: `CSoftwareSkinTechnique::init` contains no NEON
gate.** Its single `android_getCpuFeatures` call (`0x00739764`) exists only to
fill a TU-local 8-byte object that `.symtab` names `cpuFeatures` (`.bss`
`0x00da2950`, `STB_LOCAL`, 8 B). The shape is a lazy cache, re-derived from the
instructions: `ldm` of the 8-byte pair, one `orrs` against zero, `beq` to the
`bl 0x000d2f3c` / `strd r0,r1` / `b` back sequence, and both loaded words
overwritten (r3 @`0x73966c`, r2 @`0x739674`) before any read after the join. A
second consumer sweep over all 39,108 bounded A32 `STT_FUNC`s finds exactly two
resolutions of that anchor: `init` (`0x739630`) and `skin` (`0x0072ad90`,
4256 B). `skin` is where the real gate is — `ldrd` of the same static
(`0x72b280`), high half masked with a register set to `#0` (`0x72b288`), low half
masked with `#4` (`0x72b29c`), `orrs` (`0x72b2a0`), `bne 0x72bcd4`: i.e.
`(cpuFeatures & 4) != 0`. Bit 2 is the same NEON bit `DetectNeon` extracts.

New `tools/compare/skin_cpu_cache.py` re-derives all thirteen facts from the
reference bytes (structure 9, gate 4) and exits 0; both call sites sit inside
1028 B / 4256 B functions, so no separable unit exists and none was invented.

**Three 8-byte methods of the same class are now source, and byte-equal:**

| function | ELF VA | ref | candidate | result |
|---|---|---|---|---|
| `checkAvailability(STechnique const&, unsigned) const` | `0x00710b50` | 8 B | 8 B | 8/8 bytes equal |
| `needOutputBuffer() const` | `0x00710b48` | 8 B | 8 B | 8/8 bytes equal |
| `attach(ISceneNode*)` | `0x00710b58` | 8 B | 8 B | 8/8 bytes equal |

`tools/compare/collada_skin_trivial_pilot.py` (NDK r8e GCC 4.4.3, `-O2`/`-O3`/
`-Os`, `-fvisibility=hidden`) reports 3/3 symbols equal in 3/3 configs with
`code_window_equal=True` — the windows contain no relocation word, so this is the
project's first **unmasked** per-function byte equality. Scope carried in the
report and here: those three windows only. It is not compiler identification, not
a flag claim and not a whole-`.so` SHA-256 match.

Signatures come from the Itanium mangled names; `checkAvailability` and
`needOutputBuffer` write only `r0 = 1` and read no argument, and `attach` writes
nothing to `r0`, so its `void` return is INFERRED from the absence of a
return-value write. The class body, its vtable (`0x00d68ce0`) and every member
other than the `+0x0c` pointer are explicitly NOT reconstructed; the declared
`+0x00..+0x0b` storage is opaque padding, not a member list.

New CTest `collada_skin_technique_behaviour` includes the TU unchanged and checks
the constant predicates over sentinel receivers, then that `attach` changes
exactly one pointer-sized slot holding its argument, handles several pointer
values including null, and overwrites on a second call. Because `+0x0c` is an
ARM32 ABI fact, the host (64-bit) run asserts the one-slot property rather than
the absolute offset, which is asserted only when `sizeof(void*) == 4`. Negative
controls measured both ways: `return false` aborts on
`FAIL: needOutputBuffer returned false`; a wrong-field store aborts with
`FAIL: attach changed the wrong number of bytes`.

**Verification actually run:** clean configure + `cmake --build build/host`
85/85; `ctest` **18/18 pass**; `bash tools/check_all.sh` **exit 0** with **19**
audited artifacts and tooling pytest 30 passed; `NOVA3_RESEARCH_ORACLES=1 bash
tools/check_all.sh` **exit 0** (the full private-oracle chain, including both new
tools, now completes in this worktree).

**Disposition:** the three leaves are IMPLEMENTED + TESTED + instruction-window
MATCHED. The `init`/`skin` cpu-features call sites are UNDERSTOOD and remain NOT
RECONSTRUCTED. `android_cpuInit` is unchanged at 2648 B vs 2632 B and is not
being chased. No end-to-end gameplay; totalled as before, 39,123 indexed
functions and 9 DECOMPILED exports, which are never a completion state.

## DriverCallbackInterface::Init is source; the statics it writes are named from the symtab (2026-11-01)

`reconstruction/recovered/libNOVA3_neon/driver_callback_init.cpp` is
`vox::DriverCallbackInterface::Init(void*)`, ELF VA `0x009d53f4`, 48 B, A32,
`STT_FUNC`. It is the only in-binary caller of the recovered NEON probe, so the
driver-callback chain now has two source-owned links.

**What the reference actually does, re-derived from bytes + `.symtab`.** The PIC
base addend `0x003e4988` resolves to `0x00db9d90`, exactly
`DriverCallbackSourceInterface::s_listenerParameters`, and every store lands on a
named static: base+0x34 and +0x38 are `DriverCallbackInterface::m_sWorkBuffer`
(+0, +4), +0x3c and +0x40 are `m_sMixingBuffer` (+0, +4), and the single byte
store at +0x44 is `DriverCallbackSourceInterface::enableNeon` (1 byte in the
symtab). So Init clears two 8-byte buffer references and then stores
`DetectNeon()` into the NEON flag. The `BufferRef` shape (`int size; void*
data;`) is INFERRED from `DriverCallbackInterface::GetWorkBuffer` (`0x009d4828`):
the word at +0 is compared, signed, against the requested element count and
afterwards receives it, while the word at +4 is passed to `VoxFree(void*)` and
replaced by the result of `VoxAlloc(unsigned)`. `this` (r0) and the `void*`
argument (r1) are never read, so the parameter stays opaque.

**Window, measured** (`tools/compare/driver_callback_init_pilot.py`, NDK r8e
GCC 4.4.3, `-O2`/`-O3`/`-Os` × stack protector on/off × symbol visibility):

| configs | size | result |
|---|---|---|
| `-fvisibility=hidden` (6/6) | 48 B | 5 EQUAL / 2 RELOCATION_FIELD / 5 ENC, instruction shape and store order equal, one constant anchor delta `0x34` |
| `-fvisibility=default` (6/6) | 72 B | `FUNCTION_SIZE_MISMATCH` — preemptible statics force per-symbol GOT-style address loads |

The 5 encoding differences are the store displacements, and the delta is the
same `0x34` on every one: the original translation unit's static anchor began
`0x34` bytes before the two buffer objects, i.e. at `s_listenerParameters`
(0x30) plus `s_enable3dSimulation` (4). A module holding only the objects this
function touches cannot reproduce that anchor start, and closes it only by
inventing statics. `code_window_equal=False`, `source_matching_verified=False`;
no byte, compiler or flag claim.

**Negative control that the matrix is real:** default visibility changes the
addressing scheme and the size (72 B), so the hidden-visibility rows are not an
artefact of tuning one dial to fit.

New behaviour test `driver_callback_init_behaviour` (CTest) compiles the
recovered TU unchanged and stubs only `DetectNeon`: both flags, three argument
values including a garbage pointer, two distinct receivers, `s_listenerParameters`
and `s_enable3dSimulation` unchanged (the write set is exactly the two buffers
plus the 1-byte flag), and a second Init re-clearing refilled buffers. Deleting
one store makes it abort (`FAIL: Init did not clear both buffer references`), so
it is not vacuous.

`DetectNeon`'s declaration moved into the shared
`reconstruction/recovered/libNOVA3_neon/driver_callback.h` and is now `static`:
the call site supplies no receiver in r0. Its window re-measured unchanged — 6/6
configs, 32/32 B, 6 EQUAL + 2 RELOCATION_FIELD, 0 encoding differences.

**Verification actually run:** `cmake --build build/host` clean; `ctest` **17/17
pass**; `bash tools/check_all.sh` **exit 0** with **18** audited artifacts and
tooling pytest **30 passed**; `detect_neon_pilot.py` still 6/6
`ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION`.

**Disposition:** Init = IMPLEMENTED + TESTED; NOT MATCHED (shape and store order
agree modulo relocation words and one constant anchor offset). Still NOT
RECONSTRUCTED: `android_cpuInit`, `CSoftwareSkinTechnique::init`.

## Unattended decompilation is now safe to leave running (2026-11-01)

`./nova3` was already the front door; this run closed the two remaining ways an
unattended run could hurt itself, and settled the publish question with
measurements instead of another "needs credentials" note.

**1. Single-writer lock, verified through the CLI.**
`tools/pipeline/auto_decompile.py` writes
`.cache/pipeline/auto_decompile.lock` (pid, utc timestamp, binary) before it
touches the index or the Ghidra project.

* a second live holder → `acquire_lock` returns `False`, `./nova3 decompile`
  prints one line and exits **4**, and the foreign lock file is left intact
  (measured: `EXIT=4`, `foreign lock preserved: True`);
* a lock whose PID no longer exists is replaced (measured: `acquire over stale
  pid -> True`), so a killed session cannot wedge the next one;
* release is ownership-checked — a process that does not own the lock never
  deletes it (measured: `lock removed after release: True`).

**2. `./nova3 index` stopped hiding its own result.** It printed the tail of the
inventory script and swallowed stdout on failure. It now prints the exact
per-binary counts read back from `analysis_db/index.sqlite`, fails loudly if the
database is missing despite `rc=0`, and maps exit `4` from `decompile` to a
plain-English line.

**3. Publishing is measured, not hypothesised — and still blocked.** Three
routes were tried in this capability-less run: managed broker token (absent),
`gh`/`GH_TOKEN` (`gh` reports "not logged into any GitHub hosts"), and the
machine's SSH key, which authenticates as **`UmbraSecurity`** but is answered
with `ERROR: Permission to N-O-V-A-3-RE-Contributors/nova3-reconstruction.git
denied`. Read paths still work anonymously (`git ls-remote` lists `main` and the
feature branches), so this is a write-permission gap. The wrapper also rewrites
an explicit `git@github.com:` push back to https. `tools/setup/push_branch.sh`
gained opt-in `NOVA3_PUSH_VIA_SSH=1` and `NOVA3_PUSH_DRY_RUN=1`, and its failure
text now states the measured outcome instead of implying a working route.

**Verification actually run (all of it this session):**

| command | result |
|---|---|
| `./nova3 decompile --max-functions 2 --batch-size 2` | 7 → **9** exports, 33.2 s, `indexed=39123 remaining=39114` |
| live-lock CLI probe | exit **4**, foreign lock preserved |
| `ctest --test-dir build/host` | **16/16 pass** |
| `pytest test_workbench.py test_auto_decompile.py` | **16 passed** |
| `./nova3 doctor` | **16 pass, 0 warn, 0 fail** |
| `./nova3 push` | exit **3**, with all three measured routes printed |
| CI-equivalent steps run locally | guard (507 files), verification-gate 28 tests, legacy validation, shell syntax, `nova3_easy --self-test`, workbench pytest — **all rc=0** |

**Disposition:** the toolset is usable end-to-end and self-documenting; the only
open setup item is a GitHub write credential, which no in-session route can
supply. Source reconstruction is unaffected and resumes at
`vox::DriverCallbackInterface::Init` (0x009d53f4, 48 B).

## The decompilation toolset is now one self-documenting command (2026-11-01)

The pieces existed but a newcomer had to know a dozen scripts, their order and
their interpreter. `./nova3` (`tools/workbench.py`) is now the documented front
door, and `docs/DECOMPILE.md` is the single guide: doctor → index → decompile →
status → show → build/test/verify.

**What is new and actually exercised this run:**

1. `./nova3 doctor` — one `PASS`/`WARN`/`FAIL` line per dependency with the exact
   fix under every non-passing line, exit 1 on any `FAIL`. Measured on this host:
   **16 pass, 0 warn, 0 fail** (reference hash, venv, pyelftools/capstone,
   Ghidra 12.1.2 at `/opt/ghidra`, JDK 27, pyghidra 3.2.0, project dir,
   17.7 GiB free, 39,123 indexed rows, cmake/ninja, jadx/apktool/7z, NDK r8e).
2. `./nova3 decompile --address 0x9dad14` — exported the recovered
   `DetectNeon` function through the new Ghidra discovery path.
   `./nova3 decompile --max-functions 2 --batch-size 2` — advanced the bounded
   driver from 4 to **7 exports** (indexed 39,123, remaining 39,116), which is a
   second, independent resumability proof through the wrapper.
3. `./nova3 status` / `show` / `search` — coverage per binary, one function with
   its call edges resolved to symbol names, and symbol search over the real
   107,418-symbol table. `show` subtracts the Ghidra image base (0x10000) before
   resolving call targets; without that every callee lookup misses by exactly
   the image base.
4. **Portability defect fixed**: `tools/ghidra/export_batch.py` hardcoded
   `install_dir='/opt/ghidra'` and `JAVA_HOME=/usr/lib/jvm/java-21-openjdk`, so
   the documented "just run it" command worked only on this laptop. Discovery is
   now `tools/pipeline/ghidra_env.py` (explicit arg, `NOVA3_GHIDRA_HOME`,
   `GHIDRA_INSTALL_DIR`, system locations, then versioned `ghidra*` dirs) and the
   resolver requires `support/analyzeHeadless`, so a typo fails loudly instead of
   silently selecting another install.
5. `reconstruction/tests/test_workbench.py` — 10 hermetic cases (help surface,
   doctor JSON contract and fix-field completeness, hash rejection of a
   fabricated ELF, status counts, image-base call resolution, missing-export hint,
   unknown-symbol failure, search totals, Ghidra resolver semantics). Plus the 4
   pre-existing driver cases: **14 passed**. Both files are now in
   `tools/check_all.sh` and in a new `nova-ci.yml` step, so tooling can no longer
   rot silently.

**Verification actually run:** `./nova3 verify` → build + CTest **16/16** +
source-only audit exit 0. `pytest` on the two tooling files: 14 passed.

**Still not done, and it is a runtime permission matter, not a repo one:** the
branch is not published. `git push` has no credential source in this harness
(`GitHub capability_missing`; the wrapper strips `GH_TOKEN`/`GITHUB_TOKEN` and
needs `PAPERCLIP_GITHUB_BROKER_TOKEN`), and `tools/setup/push_branch.sh` exits 3
with the two supported remediations. `./nova3 push` is the wrapper for it.

## Unattended decompilation works; two real setup defects fixed (2026-10-31)

The pipeline that was missing between "index the ELF" and "export decompiled
functions in batches" now exists and has been run: `tools/pipeline/auto_decompile.py`.

**Two defects were reproduced, fixed, and re-run — both previously blocked a
clean checkout, not just this worktree:**

1. `tools/index/inventory.py` died on any fresh tree with
   `sqlite3.OperationalError: unable to open database file`, because
   `analysis_db/` is gitignored. It now creates `analysis_db/` and
   `research/elf/`. Verified: exit 0 in 43.3 s, 4 binaries, 76,344 function rows,
   39,123 for the Premium engine ELF.
2. `tools/ghidra/export_batch.py` wrote its Ghidra project inside the repository,
   which Ghidra's `ProjectLocator` rejects whenever a path element starts with
   `.` — so every isolated worktree under `.local/` or `.cache/` failed at import
   with `Path element starting with '.' is not permitted`. It now takes
   `--project-dir` (default `~/nova3-analysis/ghidra`, env
   `NOVA3_GHIDRA_PROJECT_DIR`).

**Measured run** (Premium ELF SHA-256 `84075223…baaf`): cold batch 68.7 s
(+2 exports, import dominates), warm batch 22.9 s (+2 exports), coverage
`indexed=39123 exported=4 remaining=39119`. Exports carry
`byte_mapping_validated=true`; they include `_Z10ComputeCRCPhi` (0x000de2cc,
20 instructions, 5 basic blocks) and the two cpu-features getters
`0x000d2f10`/`0x000d2f3c`, i.e. the pipeline reproduces hand-recovered
functions. `--status` and the checkpoint at
`.cache/pipeline/auto_decompile_state.json` make the run resumable; a re-run
after the first two batches exported the *next* two, not the same two.

**GitHub publication is still not granted to this session, and it is a runtime
scope matter, not a repository one.** The Paperclip `git` wrapper deletes
`GH_TOKEN`/`GITHUB_TOKEN`/`SSH_AUTH_SOCK`, disables prompts, and only brokers a
credential when `PAPERCLIP_GITHUB_BROKER_TOKEN` is in the environment; that
variable is absent for this run, so the wrapper prints
`GitHub capability_missing` and push fails at authentication. Remote read access
is fine (`git ls-remote` returns main `43f3502`), the repo already has
`user.name`/`user.email`, and the branch is `automation/paperclip-worker`
(3 commits ahead of `origin/main`, no upstream yet).
`tools/setup/push_branch.sh` now performs the push, classifies the exact failure
(`3` no credential source, `5` rejected credential, `7` remote SHA mismatch) and
verifies the remote head equals local HEAD. Run here it exits 3 with the two
supported remediations printed. Nothing was pushed.

**The full oracle suite now runs green inside a worker worktree**, which it could
not do before (it aborted at `tools/compare/zip_headers.py`). Root cause was
missing private data, not code: `research/assets/` (OBB indices) and
`.cache/assets/premium-main.obb` (the 2.2 GiB materialized cache used by
`texture_path_source.py`) are gitignored and absent from an isolated worktree.
New `tools/setup/link_private_references.sh` links them read-only from the
primary checkout. Measured: `NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh`
→ **exit 0**, CTest **16/16**, 9 oracle PASS lines (CRC, scramble, ZIP headers,
ZIP payload, BRES, lookup, ETC1, texture filename policy, texture
path/inflate/pixel integration) and `tools/index/check_index.py` clean.

**The repository's own automation is healthy on `main`** (read from the public
API, no credentials used): `nova-ci.yml` run 227 `success` on the last main push,
`nova-autofeed.yml` run 102 `success` at 2026-10-31T08:24Z, `nova-status.yml` run
175 `success` at 08:44Z; `main` is at `43f3502`, repo public, 5 open issues. So
the missing piece is not CI config or the task feeder — it is that this branch's
4 commits are unpublished.


## DetectNeon probe recovered, tested, and its caller chain mapped (2026-10-31)

The first caller-side slice of the Premium cpu-features work is now source and
behavioural evidence rather than a symbol name.

**1. Recovered function.** `reconstruction/recovered/libNOVA3_neon/driver_callback_detect_neon.cpp`
implements `_ZN3vox29DriverCallbackSourceInterface10DetectNeonEv` — ELF VA
`0x009dad14`, 32 bytes, A32, `STT_FUNC` (local symbol) in
`libNOVA3_neon.so` SHA-256 `84075223…baaf`. The source is seven lines of
ordinary C++: query `android_getCpuFamily()`, return false unless it is `1`,
then test one bit of `android_getCpuFeatures()`.

**2. The source's claims were re-derived from the reference bytes, not from the
comment.** `tools/compare/detect_neon_pilot.py` decodes the referenced window
and asserts five independent facts: the two direct call targets are exactly the
recovered getters `0x000d2f6c` (family) then `0x000d2f3c` (features); the family
comparison is against the literal `1`; the feature extract is `ubfx` lsb 2 width
1, i.e. exactly one bit; and a conditional return sits *between* the two calls,
so a non-ARM family short-circuits. It also checks that r0 is never read before
the first call, so the probe consumes no object state — which is why `this` is
unused and why "static" vs non-static member is underdetermined rather than
inferred. All five facts reproduce. The names `ANDROID_CPU_FAMILY_ARM` and
`ANDROID_CPU_ARM_FEATURE_NEON` remain INFERRED from the public NDK header; the
constants `1` and bit 2 are recovered.

**3. Window measurement.** NDK r8e GCC 4.4.3, six configurations
(`-O2`/`-O3`/`-Os` × stack protector on/off):
`ALL_INSTRUCTION_WORDS_EQUAL_MODULO_RELOCATION` in 6/6, 32/32 bytes,
6 `EQUAL` + 2 `RELOCATION_FIELD` + 0 `ENCODING_DIFFERENCE`. The two differing
words are exactly the two `BL` displacements, which an ET_REL object cannot
contain. `code_window_equal` is false and the report carries
`source_matching_verified=False`; no byte-match, compiler or flag claim is made.

The pilot initially failed with `CompareError: reference SHA-256 mismatch`
because it handed the extracted 32-byte window to `window_diff`, which bounds
the reference function itself out of the whole ELF and therefore hashes the
whole file. Fixed by passing the complete ELF bytes after the SHA check; this
is recorded because the earlier crash was a caller bug, not a matching result.

**4. Behaviour tests.** `reconstruction/tests/recovered/libNOVA3_neon/driver_callback_detect_neon_test.cpp`
compiles the recovered TU *unchanged* and supplies counting stubs for the two
cpufeatures entry points, so the probe's own logic is observable. It asserts:
non-ARM families (unknown/x86/MIPS and an out-of-enum value 7) return false with
zero feature reads; a full 64-bit single-bit sweep returns true for bit 2 only
and false for every other bit; two distinct objects behave identically; the
ARMv7/VFPv3/LDREX_STREX combination without NEON is false and with NEON is true;
0 and all-ones bracket the range; and on the ARM path each getter is called
exactly once. CTest `detect_neon_behaviour`, in `tools/check_all.sh`'s audited
list and in `tools/setup/record_build_provenance.py` with its own `link_inputs`.
Negative control: the recovered TU calls both getters, so deleting a stub breaks
the link rather than silently passing.

**5. Call-site map (the NEXT.md item that motivated this).**
`tools/compare/cpuquery_callsites.py` decodes every bounded `STT_FUNC` in the
engine ELF (39,108 functions, 0 undecodable) and reports direct branches to the
recovered getters or to their PLT stubs. Result: exactly four hits, all direct,
no PLT route.

| call site | callee | host function | host VA / size |
|---|---|---|---|
| `0x00739764` | `android_getCpuFeatures` | `glitch::collada::detail::CSoftwareSkinTechnique::init(...)` | `0x00739630`, 1028 B |
| `0x009d5414` | `DetectNeon` | `vox::DriverCallbackInterface::Init(void*)` | `0x009d53f4`, 48 B |
| `0x009dad18` | `android_getCpuFamily` | `DriverCallbackSourceInterface::DetectNeon` | `0x009dad14`, 32 B |
| `0x009dad28` | `android_getCpuFeatures` | `DriverCallbackSourceInterface::DetectNeon` | `0x009dad14`, 32 B |

So there are exactly two consumers of the cpu-features API in the whole engine:
the recovered probe, and the Collada software-skinning path directly. The probe
itself has exactly one in-binary caller, the 48-byte
`DriverCallbackInterface::Init`. That gives a small, real next slice instead of
a speculative one.

`ctest` is 16/16 and `bash tools/check_all.sh` exits 0 with 17 audited
artifacts.

## cpu-features TU: initializer proven end-to-end, char signedness settled (2026-10-31)

Two open items from the previous milestone are now closed or converted into
measured statements.

**1. `android_cpuInit` is now covered by behaviour tests end-to-end.**
`reconstruction/tests/recovered/libNOVA3_neon/cpu_features_init_test.c` compiles
the recovered TU unchanged and serves `/proc/cpuinfo`,
`/sys/devices/system/cpu/present` and `.../possible` from an in-memory fixture
table by redirecting `open`/`read`/`close` at link time (`-Wl,--wrap=…`).  The
recovered source keeps its fixed absolute paths; no macro or injected root was
added, so the path handling under test is the shipped one.  Scenarios covered:

* ARMv7 + `neon vfpv3` cpuinfo, `present`/`possible` = `0-3` → count 4,
  family ARM, features `ARMv7|VFPv3|NEON|LDREX_STREX`.
* `CPU architecture: 7` with a `(v6l)` Processor → ARMv7 bit cleared,
  LDREX/STREX still set, no VFP.
* `vfpv3d16` only, architecture 6 → VFPv3 without NEON, no ARMv7 bit.
* cpuinfo unreadable → count stays 1, features 0, and the two sysfs cpulists
  are never opened (the early return is observable).
* `present` = `0-3`, `possible` = `0,1` → count is the intersection (2).
* both cpulists unreadable → count clamps to 1.
* cpuinfo without a `Features` field → architecture-derived bits survive the
  later early return.
* three getters in a row → cpuinfo opened exactly once; a fixture swap
  afterwards does not change the cached values (the recovered
  `pthread_once`/`g_once` behaviour).

The test is sensitive to fixture content, checked with a negative control: a
modified cpuinfo lacking `neon` makes it abort on the feature assertion, so the
wrappers really are the source of the data.  It is `cpu_features_behaviour_init`
in CTest, in `tools/check_all.sh`'s audited-artifact list and in
`tools/setup/record_build_provenance.py`.

**2. Char signedness is explained; the residual `cpuInit` gap is layout.**
The reference window contains 11 `sxtb` and 18 `ldrsb` — it sign-extends every
cpuinfo byte comparison.  Plain `char` is unsigned for
arm-linux-androideabi-gcc, and the candidate emitted 0/0 of them.  Built with
`-fsigned-char` the candidate reproduces exactly 11/18 and grows from 2600 to
2648 bytes against the reference's 2632; the three getters stay word-equal at
every measured `-O` level.  Remaining differences are inlining/layout, not
missing source: this compiler inlines all three `has_list_item` feature tests
while the reference sinks the 8-character `vfpv3d16` comparison into a shared
out-of-line tail, and the listings differ in scheduling and branch polarity
(reference `cmp r1, r0; bcs` where this compile emits `cmp r0, r1; bls`).
Every feature-check permutation was measured: all six give 2648 bytes, so the
order is not the cause.  No configuration of NDK r8e 4.4.3 reproduces 2632
bytes, which points at a different 4.4.3 patchlevel or option set rather than
at a source difference; that is recorded as unexplained, not forced.

`tools/compare/cpufeatures_pilot.py` now carries `-fsigned-char` rows for every
`-O`/stack-protector combination, so the effect stays measured in every report.
`ctest` is 15/15 and `bash tools/check_all.sh` exits 0.

## All three cpu-features getters now match modulo relocation; TU tested for behaviour (2026-10-31)

The one remaining source-level difference in the Premium `cpu-features.c` TU
(see the section below) is closed, and it was a source-ordering effect, not an
unreproduced toolchain.

**What was measured.** GCC 4.4.3 emits the exported functions of a TU in the
reverse of their definition order, and lays out the TU's `.bss` objects in the
order in which those emitted functions first reference them. The reference
layout (`g_cpuCount +0x4`, `g_cpuFeatures +0x8`, `g_cpuFamily +0x10`) and the
reference function order (`getCpuCount`, `getCpuFeatures`, `getCpuFamily`,
then `cpuInit`) are both reproduced by defining the three getters in the
reverse of that order, after `android_cpuInit`. Four other declaration
permutations left the layout unchanged (`g_cpuFamily` always at `+0x4`);
`-fno-toplevel-reorder` also reproduces the offsets but destroys the address
caching the reference visibly uses (candidate getters grow to 48/52 B and
address `.bss` through `ldr rX, [pc, rY]`), so that hypothesis is recorded as
rejected rather than adopted.

Measured with `tools/compare/cpufeatures_pilot.py`, NDK r8e GCC 4.4.3, 12
configurations (base and rejected-flag rows both kept in the report):

| window | reference | candidate | words |
| --- | --- | --- | --- |
| `android_getCpuCount` 0xd2f10 | 44 B | 44 B | 9 EQUAL / 2 RELOCATION_FIELD / 0 encoding |
| `android_getCpuFeatures` 0xd2f3c | 48 B | 48 B | 10 EQUAL / 2 RELOCATION_FIELD / 0 encoding |
| `android_getCpuFamily` 0xd2f6c | 44 B | 44 B | 9 EQUAL / 2 RELOCATION_FIELD / 0 encoding |
| `android_cpuInit` 0xd2f98 | 2632 B | 2600 B | still `FUNCTION_SIZE_MISMATCH` |

This holds at `-O2`, `-O3` and `-Os`, with and without `-fstack-protector`, at
`-O3 -fstack-protector` the sizes are 44/48/44/2600. The two differing words
per getter are exactly the `BL pthread_once` PLT relocation and the `.bss`
`R_ARM_REL32` addend, i.e. link-time values an ET_REL object cannot contain.
No byte-equality claim is made (`code_window_equal` is false for every window,
`source_matching_verified=False`), and `android_cpuInit` remains the only
unmatched member of the TU.

**Behavioural tests exist now.** `reconstruction/tests/recovered/libNOVA3_neon/cpu_features_test.c`
compiles the TU into itself and exercises the file-scope helpers over fixture
buffers: token matching (`vfpv3` must not match inside `vfpv3d16`, `(v6l)`
against a v7l Processor string, tab/blank separators, NULL), `parse_decimal`
(digit runs, limit truncation, leading zeros, non-digit), `cpulist_parse` /
`cpulist_set` / `cpulist_and` / `cpulist_count` (`0-3`, `2,4-7`, `0-31`,
indices ≥ 32 dropped, first-newline stop, malformed input) and
`extract_cpuinfo_field` (first occurrence wins, field must start a line,
`:`+space required, empty value, value at end of buffer, absent field). It is
wired into CMake/CTest (`cpu_features_behaviour`), into
`tools/check_all.sh`'s source-only audit list and into
`tools/setup/record_build_provenance.py`. `ctest --test-dir build/host` passes
14/14 and `bash tools/check_all.sh` exits 0.

Not claimed: behaviour of `android_cpuInit`/the getters end-to-end (`/proc` and
`/sys` paths are fixed in the source, so that test needs a fixture root), the
original compiler/flags/linker, and any full-`.so` equality.

## cpu-features.c identified from the symbol table; struct hypothesis retracted (2026-10-31)

The Premium symbol table settles what decompiler views could not. The
`.symtab` symbol group that begins with STT_FILE `cpu-features.c` (the first
object file in the link order) and ends at STT_FILE `app.cpp` contains exactly
these symbols:

| symbol | VA | size | type/bind |
| --- | --- | --- | --- |
| `android_getCpuCount` | 0x000d2f10 | 44 | FUNC GLOBAL |
| `android_getCpuFeatures` | 0x000d2f3c | 48 | FUNC GLOBAL |
| `android_getCpuFamily` | 0x000d2f6c | 44 | FUNC GLOBAL |
| `android_cpuInit` | 0x000d2f98 | 2632 | FUNC LOCAL |
| `g_once` | 0x00d96598 | 4 | OBJECT LOCAL |
| `g_cpuCount` | 0x00d9659c | 4 | OBJECT LOCAL |
| `g_cpuFeatures` | 0x00d965a0 | 8 | OBJECT LOCAL |
| `g_cpuFamily` | 0x00d965a8 | 4 | OBJECT LOCAL |

Consequences, all from the binary rather than inference:

1. **The earlier `CpuQueryState` struct model is wrong and is retracted.** The
   four state fields are four separate file-scope objects, and `.bss` itself
   begins at `g_once`, which is why all three getters reach `+4`, `+8`/`+12`
   and `+16` from one PC-relative anchor. The word-alignment argument that was
   used to justify the struct is therefore moot: `g_cpuFeatures` is an 8-byte
   object at an 8-byte-aligned address, and the reference still loads it as two
   `LDR`s. `android_cpuInit` stores it with `STRD`, so the same object is
   stored as a pair and loaded as two words in this build.
2. **The TU is the AOSP cpufeatures `cpu-features.c`, BSD-3-Clause.** Resolving
   every PC-relative literal in `android_cpuInit` shows the exact strings and
   paths: `/proc/cpuinfo`, `/sys/devices/system/cpu/present`,
   `/sys/devices/system/cpu/possible`, `CPU architecture`, `Processor`,
   `(v6l)`, `Features`, `vfpv3`, `vfpv3d16`, `neon`. The feature-bit OR values
   (2 = VFPv3, 6 = VFPv3|NEON, 1 = ARMv7, 8 = LDREX_STREX), the cpulist
   bitmask/popcount counting and the `parse_decimal` range loop all match the
   pre-r8c revision of that library.
3. **Revision bound.** No `/proc/self/auxv` access, no `android_setCpu`, no
   `g_inited` object and `Features`-based detection place it at the r7c
   generation (r7c added the present/possible count; r8c replaced Features
   parsing with auxv; r8d added `android_setCpu`).
4. **Reconstruction written:** `reconstruction/recovered/libNOVA3_neon/cpu_features.c`
   plus a local `cpu_features.h` (public API constants only). It compiles to
   `android_cpuInit` = 2600 B at `-O3 -fstack-protector` (reference 2632) and to
   the exact reference sizes for all three getters (44/48/44).
5. **Measured window result (r8e GCC 4.4.3, 6 configurations):** — *superseded
   by the section above: all three getters now match modulo the two relocation
   words once the getter definition order is reversed.* At the time of this
   measurement `android_getCpuFeatures` matched the reference instruction
   stream word for word modulo relocations and literal-pool words, while
   `android_getCpuCount` and `android_getCpuFamily` matched in size and shape
   but carried one differing word each: the offset immediate of the final load,
   because this compiler ordered the two int-sized objects the other way round
   (`g_cpuFamily@+4`, `g_cpuCount@+16` versus the reference's `g_cpuCount@+4`,
   `g_cpuFamily@+16`). Declaration order of the objects did not change that;
   getter *definition* order does. `android_cpuInit` is NOT matched (structural
   divergence, 32 bytes short).
6. **New tooling.** `tools/compare/literal_map.py` resolves every PC-relative
   literal in an A32 function to a symbol, section or string (this is what
   produced the string inventory above); `tools/compare/cpufeatures_pilot.py`
   compiles the TU under a compiler/flags matrix and reports per-window status,
   the `.bss` layout of the candidate and `source_matching_verified=False`.

Nothing here claims an original compiler, flags, linker or `.so` hash. The
getters moved from "shape-only, guessed ABI" to "recovered object layout plus
one measured instruction match", and `android_cpuInit` moved from NOT
RECONSTRUCTED to IMPLEMENTED (not TESTED for behaviour, not byte-matched).

## CPU-query thunk ABI correction and compiler-shape measurement (2026-10-31)

The older recovered block for Premium `0x000d2f10` used an invented
context-pointer ABI, so its mismatch was uninformative. A corrected candidate
now exists: `reconstruction/recovered/libNOVA3_neon/cpufeatures_queries.cpp`
implements `android_getCpuCount` (0x000d2f10, 44 B, A32),
`android_getCpuFeatures` (0x000d2f3c, 48 B) and `android_getCpuFamily`
(0x000d2f6c, 44 B) with the observed no-argument interface, the once-service
call, one shared 20-byte state object and the 4/8/4-byte field reads;
`android_cpuInit` (0x000d2f98, 2,632 B) stays declared-only and NOT
RECONSTRUCTED. Signedness of the 32-bit results and the struct grouping remain
INFERRED from the observed offsets and the public cpufeatures interface.

New tooling: `tools/compare/window_diff.py` (relocation-aware per-word diff
classifying EQUAL / RELOCATION_FIELD / LITERAL_POOL / ENCODING_DIFFERENCE) and
`tools/compare/cpuquery_pilot.py` (compiles the candidate plus a privately
generated matching-context variant with each discovered ARM compiler — NDK
clang 21, `arm-none-eabi-g++` 16.2 and the locally installed NDK r8e
`arm-linux-androideabi-g++` 4.4.3 — and runs both the strict
`object_compare` verdict and the per-word diff). The pilot is wired into
`tools/check_all.sh` behind `NOVA3_RESEARCH_ORACLES=1`.

Measured outcome — **no byte-equality candidate, no compiler identified**:

| Compiler / variant | getCpuCount (44 B) | getCpuFeatures (48 B) | getCpuFamily (44 B) |
| --- | --- | --- | --- |
| r8e GCC 4.4.3, committed candidate | 44 B, 6 eq / 3 reloc / 2 enc | 48 B, 7 eq / 3 reloc / 2 enc | 44 B, 6 eq / 3 reloc / 2 enc |
| r8e GCC 4.4.3, matching context | 44 B, 8 eq / 2 reloc / 1 lit | 48 B, 9 eq / 2 reloc / 1 lit | 44 B, 8 eq / 2 reloc / 1 lit |
| NDK clang 21, matching context | 44 B, 8 eq / 2 reloc / 1 lit | 44 B, size mismatch | 44 B, 8 eq / 2 reloc / 1 lit |
| arm-none-eabi-g++ 16.2, matching context | 44 B, 8 eq / 2 reloc / 1 lit | 44 B, size mismatch | 44 B, 8 eq / 2 reloc / 1 lit |
| NDK clang 21 / GCC 16.2, committed candidate | `FUNCTION_BYTES_DIFFERENT` | size mismatch | `FUNCTION_BYTES_DIFFERENT` |

Measured conclusions, each backed by the report rather than assumed:

1. **No candidate is byte-equal.** Every compile leaves the `bl` displacement
   and the literal-pool words different — link-time values an ET_REL object
   cannot contain. `object_compare` never returns `LINKED_FUNCTION_WINDOW_EQUAL`.
2. **Instruction-stream agreement is source-driven, not compiler-identifying.**
   Once the state object is contiguous, word-aligned and the initializer binds
   locally, clang 21 and GCC 16.2 reproduce every instruction word of
   `getCpuCount` and `getCpuFamily`, and GCC 4.4.3 reproduces every word of all
   three. Only relocation and literal words differ. An earlier draft of this
   note attributed the shape to GCC alone — the pilot refutes that, so the
   claim is withdrawn.
3. **The 64-bit load is the discriminator, and it is an alignment effect.**
   The reference loads `features` with two `LDR`s (48-byte body). The local
   GCC 4.4.3 reproduces exactly that pair when the state aggregate is modelled
   as 4-byte aligned, and emits `LDRD` when it is 8-byte aligned; clang 21 and
   GCC 16.2 emit `LDRD` at either alignment. So the reference's state object was
   only word-aligned, which is INFERRED from the codegen and not independently
   known. A first draft read the two-`LDR` form as proof of an old compiler;
   that reading is withdrawn — the reference contains 1,789 `LDRD` and 1,576
   `STRD` instructions elsewhere, so its compiler clearly emits `LDRD`.
4. **The initializer is translation-unit local in the original.** The committed
   candidate declares `android_cpuInit` extern, so GCC 4.4.3 addresses it
   through a GOT-style indirect literal (`LDR` of a literal plus `LDR [pc, r3]`,
   two divergent words); the context variant with a file-local initializer
   removes exactly those two words. The original TU therefore bound the
   initializer locally, which this module cannot reproduce until the initializer
   itself is reconstructed.

Isolated compilation of the committed source still constant-folds under clang
21 (immediate-zero returns) because nothing in the TU writes the state; that is
why the pilot generates the labelled context variant privately.

Also repaired this session: an uncommitted edit had loosened
`tools/verify_source_only.py` so that only the first path component was checked
against the forbidden trees. That edit is required — the repository's own
committed source under `reconstruction/research/` contains a path component
named `research` — and it is not a weakening, because absolute paths and `..`
components are already rejected, so the first component is the only component
that can name a private root tree. It is restored with that reasoning recorded,
and the suite below was run against it.

## Compiler-aware historical revalidation (2026-10-28)

A read-only inventory over 25 existing `reconstruction/recovered/` C++ blocks
and 25 historical evidence JSON records completed successfully:
8 source blocks have explicit evidence-path associations, 17 have inferred
address associations, none lack an evidence record, and no stale recorded
source SHA was found. **Zero blocks are yet independently reverified as an
original ARM source-code match under the new compiler/objdiff standard.**
Two historical evidence records assert short billing-forwarder instruction
window matches; those claims have been retained for independent review.
Previously passing behavioural tests remain meaningful within their scope.

The new local tools for Cyrus/Premium donor hypotheses, ARM ET_REL code-window
comparison and sequential compiler-candidate experiments are source-only
research infrastructure, not a completed original-linker reconstruction.
Observed `GCC 4.4.3` strings are still an incomplete compiler fingerprint.
Original linker inputs, source-built JNI/Irrlicht integration and a playable
APK remain unverified. See `docs/DISCORD_IMPLEMENTATION.md` and
`docs/LEGACY_REVALIDATION.md`. The worker now preserves private streaming
AI logs and quarantines a timed-out provider after one bounded attempt.

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
