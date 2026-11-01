# Worker mission (owner-assigned 2026-11-02): byte-equality under GCC 4.4.3

Scope: this file names what the next autonomous passes must do. It is read by
anything that picks up this checkout (the DeepSeek Harness loop, a Codex/Claude
session, or a human). Commit specific, verified increments; never push, merge,
rewrite history, or touch `originals/`, `Archive/`, `analysis/`, `generated/`
or `research/`.

## Context

The engine's compiler is known and installed: Android NDK r8e's
`arm-linux-androideabi-g++ (GCC) 4.4.3`. The remaining question for each
recovered function is *source spelling* and *flags*, not compiler identity.

Tools (added 2026-11-02, all safe to run):

```sh
GCC=$(bash tools/setup/fetch_gcc443.sh | tail -1)
.venv/bin/python tools/compare/gcc443_sweep.py --gcc "$GCC" \
  --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --case reconstruction/recovered/libNOVA3_neon/<file>.cpp:0x<VA>:<mangled>\
;reconstruction/recovered/libNOVA3_neon --out .cache/gcc443-sweep/<name>.json
.venv/bin/python tools/compare/gcc443_explain.py --gcc "$GCC" \
  --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
  --source reconstruction/recovered/libNOVA3_neon/<file>.cpp --address 0x<VA> \
  --symbol <mangled> --flags "<flags>"
```

Read first: `AGENTS.md`, `docs/NOVA_TEAM.md`, `docs/OBJECT_MATCHING.md`,
`docs/progress/{HANDOFF,STATUS,NEXT}.md`.

## Tasks, in order

1. **`operator*<float, collada::SMatrix>` @0x6e7e60 (564 B).** Best state:
   exact instruction classes at `-std=gnu++98 -marm -mfpu=vfpv3-d16 -O2
   -funroll-loops -fPIC -fno-rtti -fno-exceptions`, with 6 extra loads of the
   *first* operand. The original multiplies as soon as it has one pair
   (`vldr s14,[r5]; vldr s15,[r4]; vmul s15,s14,s15`) instead of hoisting both
   operands. Try: typed member access instead of `float*`/`float const*`
   pointers, separate `row`/`col` pointer increments, an unrolled macro/helper
   form, `#pragma GCC unroll`, and different loop nesting. Measure with
   `gcc443_explain.py`; require a 564 B candidate with zero class deltas before
   checking byte equality with `gcc443_sweep.py`.
2. **`CMatrix4<float>::CMatrix4(eConstructor)` @0x108c54 (56 B).** Size already
   matches at `-mfpu=vfpv3-d16 -O2`; explain the first divergence and try
   spellings (`float v[16] = {...}`, member-wise assignment, memset+set).
3. **Whole-function matrix with GCC 4.4.3** for every recovered TU in
   `reconstruction/recovered/libNOVA3_neon/`, replacing the old clang-21
   numbers: produce a table {function, original size, best size, flags,
   matched / size-only / behavioural} in `docs/progress/STATUS.md` with report
   paths. Do not call a size match a match.
4. **`CMatrix4<float>::getInverse` @0x000ebb08 (776 B)** is the next
   unimplemented dependency of the Collada matrix group. Decode it from the
   bytes (element addressing, determinant path, failure contract) before
   writing any arithmetic; leave `INFERRED` labels where the bytes do not say.
5. After every change run `bash tools/check_all.sh`; a pass that breaks the
   suite is not done. Record exact flags, report paths and what is *not* proven
   in `docs/progress/{STATUS,NEXT,SESSION_LOG}.md`.

## Off-limits

Mass-generating pseudocode, inventing arithmetic the bytes do not show,
"matching" by embedding original instruction bytes, synthetic `.byte`/`.s`
objects claimed as source, and editing files outside the assigned scope.
