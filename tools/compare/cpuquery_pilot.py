#!/usr/bin/env python3
"""Bounded CPU-query thunk matching pilot (Premium ARMv7 reference).

Three real Premium functions are bounded by their own ELF symbols:
  android_getCpuCount    ELF VA 0x000d2f10, 44 bytes, A32
  android_getCpuFeatures ELF VA 0x000d2f3c, 48 bytes, A32
  android_getCpuFamily   ELF VA 0x000d2f6c, 44 bytes, A32

The pilot compiles two source variants with every available candidate
compiler, then runs the strict verdict (`nova3_team.object_compare`, which
reports FUNCTION_SIZE_MISMATCH / BYTES_DIFFERENT / EQUAL) and the
relocation-aware per-word diff (`tools/compare/window_diff.py`).

Variant A is the committed reconstruction candidate: the three thunks with the
observed no-argument ABI and the initializer left as an unresolved dependency.
Variant B is generated here, privately, and adds a stand-in initializer body so
the compiler can see that the state block is written. Variant B's numbers are
NOT a reconstruction of android_cpuInit; it exists only because the original
translation unit contained the initializer, and without a visible write modern
compilers constant-fold the thunks.

A caveat that is measured rather than assumed: none of this establishes the
original compiler, flags, translation unit, relocations or full `.so` hash.
Reference bytes stay local; the report never prints original code.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "compare"))

from nova3_team.object_compare import CompareError, compare_window  # noqa: E402
from nova3_team.object_matching import compiler_profile  # noqa: E402
from window_diff import diff_window  # noqa: E402

PREMIUM_SHA = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
REFERENCE = ROOT / "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
CANDIDATE = ROOT / "reconstruction/recovered/libNOVA3_neon/cpufeatures_queries.cpp"
TARGETS = (
    (0x000D2F10, "android_getCpuCount", 44),
    (0x000D2F3C, "android_getCpuFeatures", 48),
    (0x000D2F6C, "android_getCpuFamily", 44),
)
BASE_FLAGS = ("-marm", "-O2", "-fPIC", "-fno-exceptions", "-fno-rtti")

# Variant B. Explicitly a matching context, not recovered functionality.
CONTEXT_VARIANT = """\
// GENERATED MATCHING CONTEXT - NOT RECONSTRUCTED FUNCTIONALITY.
// android_cpuInit (ELF VA 0x000d2f98, 2632 bytes) is NOT reconstructed; this
// stand-in only makes the state block's writes visible to the compiler.
extern "C" {
typedef int once_t;
int pthread_once(once_t*, void (*)(void));
}
struct __attribute__((aligned(4))) CpuQueryState {
    once_t once;
    int count;
    unsigned long long features;
    int family;
};
static CpuQueryState g_state;

static void android_cpuInit(void) {
    g_state.count = 1;
    g_state.features = 0;
    g_state.family = 1;
}
extern "C" int android_getCpuCount(void) {
    (void)pthread_once(&g_state.once, android_cpuInit);
    return g_state.count;
}
extern "C" unsigned long long android_getCpuFeatures(void) {
    (void)pthread_once(&g_state.once, android_cpuInit);
    return g_state.features;
}
extern "C" int android_getCpuFamily(void) {
    (void)pthread_once(&g_state.once, android_cpuInit);
    return g_state.family;
}
"""


def candidates() -> list[dict]:
    """Discovered local ARM compilers; a missing one is reported, not fatal."""
    found = []
    ndk = Path("/opt/android-ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++")
    if ndk.is_file():
        found.append(dict(
            name="ndk-clang",
            compiler=str(ndk),
            flags=[*BASE_FLAGS, "--target=armv7a-linux-androideabi21",
                   "-std=c++17", "-fomit-frame-pointer"]))
    gcc = Path("/usr/bin/arm-none-eabi-g++")
    if gcc.is_file():
        found.append(dict(
            name="gcc-arm-none-eabi",
            compiler=str(gcc),
            flags=[*BASE_FLAGS, "-march=armv7-a", "-mfpu=vfpv3-d16",
                   "-mfloat-abi=softfp"]))
    return found


def historical_candidates() -> list[dict]:
    """The real GCC 4.4.3 Android ARM candidate, when its NDK is present.

    NDK r8e is a source of the `GCC: (GNU) 4.4.3` string seen in the reference
    `.comment`; selecting it is a compiler candidate, never a verified match.
    """
    root = Path(os.environ.get("NOVA3_R8E_NDK", "/home/jason/.local/opt/android-ndk-r8e"))
    compiler = root / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-g++"
    sysroot = root / "platforms/android-9/arch-arm"
    if not compiler.is_file() or not sysroot.is_dir():
        return []
    return [dict(name="ndk-r8e-gcc-4.4.3", compiler=str(compiler),
                 flags=[*BASE_FLAGS, "-march=armv7-a", "-mfpu=vfpv3-d16",
                        "-mfloat-abi=softfp", "-std=c++98",
                        f"--sysroot={sysroot}"])]


def compile_source(compiler: dict, source: Path, obj: Path, timeout: int = 120) -> dict:
    cmd = [compiler["compiler"], *compiler["flags"], "-c", str(source), "-o", str(obj)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(compiled=False, error_type=type(exc).__name__)
    if proc.returncode != 0:
        return dict(compiled=False, error_type="CompilerError",
                    exit_code=proc.returncode)
    return dict(compiled=True, object=obj.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True,
                        help="report JSON under the private repository .cache")
    args = parser.parse_args(argv)
    cache = (ROOT / ".cache").resolve()
    out = args.out.expanduser().resolve()
    if not out.is_relative_to(cache) or out.suffix != ".json":
        print("cpuquery pilot: output must be JSON under repository .cache",
              file=sys.stderr)
        return 2
    if not REFERENCE.is_file() or not CANDIDATE.is_file():
        print("cpuquery pilot: local Premium reference or candidate source missing",
              file=sys.stderr)
        return 2
    reference = REFERENCE.read_bytes()
    run = out.parent
    run.mkdir(parents=True, exist_ok=True)
    context_source = run / "matching_context.cpp"
    context_source.write_text(CONTEXT_VARIANT, encoding="utf-8")

    report = dict(
        schema="nova3.cpuquery-pilot/1",
        reference_sha256=PREMIUM_SHA,
        candidate_source="reconstruction/recovered/libNOVA3_neon/cpufeatures_queries.cpp",
        targets=[dict(address=a, symbol=s, size=n) for a, s, n in TARGETS],
        compilers=[], results=[], source_matching_verified=False,
        original_compiler_verified=False,
        caveat=("Size and per-word evidence only. No evaluated candidate is an "
                "original compiler, translation unit, relocation set or .so match."))

    for compiler in candidates() + historical_candidates():
        fingerprint = compiler_profile(Path(compiler["compiler"]), compiler["flags"])
        record = dict(name=compiler["name"], flags=compiler["flags"],
                      version_sha256=fingerprint["version_sha256"],
                      version=fingerprint.get("version_text", "")[:200])
        report["compilers"].append(record)
        for variant, source in (("candidate", CANDIDATE),
                                ("matching-context", context_source)):
            obj = run / f"{compiler['name']}.{variant}.o"
            outcome = compile_source(compiler, source, obj)
            row = dict(compiler=compiler["name"], variant=variant, **outcome)
            report["results"].append(row)
            if not outcome.get("compiled"):
                continue
            data = obj.read_bytes()
            words = {}
            for address, symbol, _size in TARGETS:
                verdict = compare_window(reference, data, address=address,
                                         symbol=symbol,
                                         expected_reference_sha256=PREMIUM_SHA)
                per_word = diff_window(reference, data, address=address,
                                       symbol=symbol,
                                       expected_reference_sha256=PREMIUM_SHA)
                words[symbol] = dict(verdict=verdict["status"],
                                     reference_size=verdict["reference_size"],
                                     candidate_size=verdict["candidate_size"],
                                     equal_words=per_word["counts"]["EQUAL"],
                                     relocation_field_words=per_word["counts"]["RELOCATION_FIELD"],
                                     literal_pool_words=per_word["counts"]["LITERAL_POOL"],
                                     encoding_difference_words=per_word["counts"]["ENCODING_DIFFERENCE"],
                                     instruction_stream_equal=per_word["instruction_stream_equal"])
            row["functions"] = words
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for row in report["results"]:
        label = f"{row['compiler']}/{row['variant']}"
        if not row.get("compiled"):
            print(f"{label}: NOT COMPILED ({row.get('error_type')})")
            continue
        for address, symbol, _size in TARGETS:
            item = row["functions"][symbol]
            print(f"{label} {symbol}: {item['verdict']} "
                  f"ref={item['reference_size']}B cand={item['candidate_size']}B "
                  f"equal={item['equal_words']} reloc={item['relocation_field_words']} "
                  f"literal={item['literal_pool_words']} "
                  f"encoding-diff={item['encoding_difference_words']}")
    aligned = [f"{r['compiler']}/{r['variant']}/{s}"
               for r in report["results"] if r.get("compiled")
               for s in (t[1] for t in TARGETS)
               if r["functions"][s]["instruction_stream_equal"]]
    byte_equal = [f"{r['compiler']}/{r['variant']}/{s}"
                  for r in report["results"] if r.get("compiled")
                  for s in (t[1] for t in TARGETS)
                  if r["functions"][s]["verdict"] == "LINKED_FUNCTION_WINDOW_EQUAL"]
    report["instruction_stream_equal_functions"] = len(aligned)
    report["byte_equal_functions"] = len(byte_equal)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Instruction streams equal modulo relocation: {len(aligned)} of "
          f"{3 * sum(1 for r in report['results'] if r.get('compiled'))} candidates")
    print(f"Byte-equal linked windows: {len(byte_equal)}")
    print("Source matching independently verified: NO")
    print("Report:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
