#!/usr/bin/env python3
"""Compile the reconstructed cpu-features TU and diff it against the reference.

This drives the whole translation unit, not a single function: the four
recovered symbols of the Premium engine's `cpu-features.c` group
(android_getCpuCount / android_getCpuFeatures / android_getCpuFamily /
android_cpuInit) are each bounded in the reference ELF and compared word by
word against the freshly compiled ET_REL candidate using
`tools/compare/window_diff.py`.

Two modes:

  * `--flags-declared` (flag-row mode) compiles ONE candidate with one
    explicit, labelled flag list and records a row per reference window and
    per symbol.  This is the mode used to find out which flags the original
    `libcpu-features.a` was built with: it asks nothing, it only records.
  * legacy matrix mode (no flag argument) sweeps the historical NDK GCC 4.4.3
    toolchain matrix with the pilot's own guessed flags.

What this proves and what it does not:

  * A matching window shows the reconstructed source produces the same A32
    instruction stream for that symbol under the given compiler and flags.
  * It does NOT identify the original compiler, flags or linker, and it is not
    an original object-file recovery.  Every report carries
    `source_matching_verified=False`.
  * In flag-row mode every row is reporting state `MEASURED`: it records the
    compile flags, candidate byte size, the `sxtb` / `ldrsb` instruction counts
    and the number of matching words against the reference window.  No row ever
    carries a true byte-match claim; the per-row `byte_match` flag is always
    false, and `android_cpuInit` is reported NOT MATCHED or UNKNOWN.
  * Reference bytes are read locally and never printed; only sizes, counts and
    per-word SHA-256 digests appear in the report.  The original
    `libcpu-features.a` is never built, linked or loaded; `.mk` files are read
    as text evidence only.
  * `--flags-from-mk` extracts `LOCAL_CFLAGS` (and `LOCAL_CPPFLAGS`) from an
    Android.mk as text.  Expansion of `$(call my-dir)` is textual: the file's
    directory is substituted, and any variable that would resolve to a source
    tree or an absolute build path is refused and reported as UNKNOWN rather
    than compiled.

Example:
    .venv/bin/python tools/compare/cpufeatures_pilot.py \
        --reference analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so \
        --flags-declared
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
for extra in (SCRIPTS, Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from window_diff import diff_window  # noqa: E402

from elftools.elf.elffile import ELFFile  # noqa: E402

try:  # Capstone is optional: without it the instruction counts are UNKNOWN.
    import capstone  # noqa: E402
except ImportError:  # pragma: no cover - environment dependent
    capstone = None

# Recovered .bss layout of the reference TU, as offsets from g_once. The
# getters address these directly, so a candidate whose compiler orders the
# two int-sized objects the other way cannot reproduce the offset immediates
# even when its source is equivalent.
REFERENCE_BSS_LAYOUT = {
    "g_once": 0,
    "g_cpuCount": 4,
    "g_cpuFeatures": 8,
    "g_cpuFamily": 16,
}

DEFAULT_NDK_R8E = Path(os.environ.get("NOVA3_R8E_NDK", "/home/jason/.local/opt/android-ndk-r8e"))

REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"

# Prebuilt cpufeatures module shipped with the premium source tree.  Its
# redirect stub `sources/cpufeatures/Android.mk` includes
# `sources/android/cpufeatures/Android.mk`, which declares the module below.
DECLARED_MK_DIR = Path(os.environ.get(
    "NOVA3_REFERENCE_ROOT", "/home/jason/Projects/nova3_re")) / Path(
    "reference/nova3_source/NOVA3/extern/GLLegacy/AndroidPortTemplate/"
    "tools/android-ndk/sources/android/cpufeatures")
DECLARED_MK_FILES = (
    DECLARED_MK_DIR / "Android.mk",
    DECLARED_MK_DIR.parent / "cpufeatures" / "Android.mk",  # redirect stub
)
DECLARED_MK_LABEL = "prebuilt-cpufeatures-LOCAL_CFLAGS(declared)"

# The Android.mk declares no compiler options.  The ndk-build options that are
# therefore in force are stated here as named pilot defaults, not as original
# flags: the platform sysroot (android-14) and the armeabi-v7a ABI defaults of
# NDK r8e.  They are recorded in every row under `pilot_default_flags`.
PILOT_DEFAULT_FLAGS = ("-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16")

# Recovered symbols of the reference TU: (symbol, ELF VA, reported size).
TU_SYMBOLS = (
    ("android_getCpuCount", 0x000D2F10, 44),
    ("android_getCpuFeatures", 0x000D2F3C, 48),
    ("android_getCpuFamily", 0x000D2F6C, 44),
    ("android_cpuInit", 0x000D2F98, 2632),
)

# Symbols whose flags are under investigation.  These are reported NOT MATCHED
# or UNKNOWN, never as a match.
FLAG_ROW_SYMBOLS = tuple(s[0] for s in TU_SYMBOLS)

SIGN_EXTEND_MNEMONICS = ("sxtb", "ldrsb")


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------

_UNEXPANDABLE_VAR = re.compile(r"\$\(([^()$]+)\)|\$\{([^{}$]+)\}")


def split_flags(text: str) -> list[str]:
    """Split a flag string into argv tokens (shlex, no shell evaluation)."""
    if not text:
        return []
    return shlex.split(text, comments=False, posix=True)


def _expand_mk_var(expr: str, mk_dir: Path) -> str:
    """Expand only the variables that can be resolved without a build system.

    `$(call my-dir)` is the Android.mk directory.  Anything else is a build or
    source-tree path (e.g. `$(VOX_LOCAL_CFLAGS)`) whose value is unknown here;
    it raises ValueError so the caller records UNKNOWN instead of guessing.
    """
    def repl(match: re.Match) -> str:
        inner = (match.group(1) or match.group(2) or "").strip()
        if inner == "call my-dir":
            return str(mk_dir)
        raise ValueError(f"unresolvable make variable: $({inner})")

    return _UNEXPANDABLE_VAR.sub(repl, expr)


def parse_mk_assignments(text: str) -> dict[str, list[str]]:
    """`NAME := value` / `?=` / `+=` lines, in file order (no make evaluation)."""
    result: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(\+=|\?=|:=|=)\s*(.*)$", line)
        if not match:
            continue
        name, op, value = match.groups()
        if op == "+=":
            result.setdefault(name, []).append(value)
        else:
            result[name] = [value]
    return result


def declared_flags_from_mk(mk_path: Path) -> dict:
    """Read LOCAL_CFLAGS/LOCAL_CPPFLAGS text evidence from one Android.mk.

    Returns a dict with `flags` (expanded tokens), `raw` lines and an `error`
    (None when every variable resolved).  Does not build anything.
    """
    text = mk_path.read_text(encoding="utf-8", errors="replace")
    assigns = parse_mk_assignments(text)
    raw = {k: v for k, v in assigns.items() if k in ("LOCAL_CFLAGS", "LOCAL_CPPFLAGS")}
    flags: list[str] = []
    try:
        for name in ("LOCAL_CFLAGS", "LOCAL_CPPFLAGS"):
            for value in assigns.get(name, []):
                flags.extend(split_flags(_expand_mk_var(value, mk_path.parent)))
    except ValueError as exc:
        return {"mk": str(mk_path), "raw": raw, "flags": [], "error": str(exc)}
    return {"mk": str(mk_path), "raw": raw, "flags": flags, "error": None}


def declared_flag_set(repo: Path | None = None) -> dict:
    """The prebuilt cpufeatures module's declared flag set (text evidence).

    `flags` excludes the synopsis header (-I<module dir> adds the module's own
    C header directory only; the source's <sys/system_properties.h> and
    <machine/cpu-features.h> include dirs are NOT declared by the module and
    are not invented here).
    """
    del repo  # DECLARED_MK_DIR is absolute (private reference tree)
    evidence = []
    for p in DECLARED_MK_FILES:
        if not p.exists():
            return {"label": DECLARED_MK_LABEL, "flags": [], "mk": [],
                    "error": f"UNKNOWN: mk evidence absent: {p}"}
        evidence.append(declared_flags_from_mk(p))
    errors = [e["error"] for e in evidence if e["error"]]
    if errors:
        return {"label": DECLARED_MK_LABEL, "flags": [], "mk": evidence,
                "error": "; ".join(errors)}
    # The module's own assignments come from the real Android.mk (the redirect
    # stub only includes it); concatenate in file order.
    flags: list[str] = []
    for item in evidence:
        flags.extend(item["flags"])
    return {"label": DECLARED_MK_LABEL, "flags": flags, "mk": evidence, "error": None}


def default_flag_sets() -> list[dict]:
    """Labelled flag rows: the declared set alone, then +/- signedness rows."""
    declared = declared_flag_set()
    base = list(declared["flags"])
    rows = [{"label": DECLARED_MK_LABEL,
             "flags": base, "raw": None, "derived_from": None}]
    for label, fl in (
        ("declared+-fsigned-char", base + ["-fsigned-char"]),
        ("declared+-funsigned-char", base + ["-funsigned-char"]),
        ("signedness-unspecified(empty-LOCAL_CFLAGS)", []),
        ("declared+-munaligned-access", base + ["-munaligned-access"]),
        ("declared+-mno-unaligned-access", base + ["-mno-unaligned-access"]),
    ):
        rows.append({"label": label, "flags": fl, "raw": None, "derived_from": DECLARED_MK_LABEL})
    return rows


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def reference_window(reference: bytes, address: int) -> bytes | None:
    """Bytes of the bounded reference function at `address`, or None."""
    from nova3_team.object_matching import select_functions  # local import

    try:
        selected, _ = select_functions(reference, addresses={address}, limit=1)
    except Exception:  # noqa: BLE001 - any bounding failure means UNKNOWN
        return None
    if len(selected) != 1:
        return None
    row = selected[0]
    return reference[row["file_offset"]:row["file_offset"] + row["size"]]


def count_sign_extend(blob: bytes, address: int, thumb: bool = True) -> dict:
    """Count sxtb / ldrsb instructions in a code blob (UNKNOWN without Capstone)."""
    if capstone is None:
        return {"sxtb": None, "ldrsb": None, "counts_available": False}
    mode = capstone.CS_MODE_THUMB if thumb else capstone.CS_MODE_ARM
    md = capstone.Cs(capstone.CS_ARCH_ARM, mode)
    md.skipdata = True
    counts = {name: 0 for name in SIGN_EXTEND_MNEMONICS}
    for insn in md.disasm(blob, address):
        if insn.mnemonic in counts:
            counts[insn.mnemonic] += 1
    return {**counts, "counts_available": True}


def candidate_object(reference: bytes, address: int, symbol: str,
                     compiled: bytes, expected_size: int | None) -> tuple[bytes, bool]:
    """Bytes of a candidate ELF symbol; for a fixture row, its raw window."""
    try:
        from nova3_team.object_compare import candidate_window
        return candidate_window(compiled, symbol)["bytes"], True
    except Exception:  # noqa: BLE001 - fixture rows carry raw window bytes
        if expected_size is not None and len(compiled) != expected_size:
            raise ValueError(f"fixture candidate size {len(compiled)} != expected {expected_size}")
        return compiled, False


def _reference_blob(reference: bytes | None, row: dict) -> tuple[bytes | None, bool]:
    """Bounded reference window for a fixture row (raw file bytes fallback)."""
    if reference is None:
        return None, False
    address = row.get("address")
    size = row.get("size")
    # A bare image without an ELF symbol table cannot bound a function: use the
    # raw size window directly instead of treating it as unbounded.
    has_symtab = reference[:4] == b"\x7fELF" and b".symtab" in reference[:65536]
    if address is not None and has_symtab:
        try:
            blob = reference_window(reference, address)
            if blob is not None:
                return blob, True
        except Exception:  # noqa: BLE001 - no symbol table: raw window below
            pass
    if address is None or size is None:
        return None, False
    if not 0 <= address <= len(reference) - size:
        return None, False
    return reference[address:address + size], True


def score_rows(reference: bytes | None, candidates: list[dict],
               reference_sha256: str | None = None) -> list[dict]:
    """Score a fixture row list: one record per input row, one window per CA.

    `candidates` rows: `{label, flags, address?, size?, thumb?, symbol?,
    compiled?}`.  `compiled` is optional in a fixture: when absent, the
    candidate window is the `size`-byte window of `reference` at `address`
    (a stand-in instruction window, NOT an original object).  For real rows the
    caller (run_flag_rows) supplies size/sxtb/ldrsb/matching_words/status.

    Guarantees for every returned row:
      reporting_state == "MEASURED",  matched == False,  byte_match == False,
      fields: label, flags, size, sxtb, ldrsb, matching_words, ref_* counts,
      status in {NOT_MATCHED, UNKNOWN}.  No row is ever reported as matched.
    """
    rows: list[dict] = []
    for cand in candidates:
        flags = list(cand.get("flags") or [])
        symbol = cand.get("symbol") or "android_cpuInit"
        thumb = bool(cand.get("thumb", True))
        ref_blob, bounded = _reference_blob(reference, cand)
        ref_counts = (count_sign_extend(ref_blob, int(cand.get("address") or 0), thumb)
                      if ref_blob is not None else {"sxtb": None, "ldrsb": None,
                                                    "counts_available": False})
        row = {
            "label": cand.get("label"),
            "flags": flags,
            "flags_text": " ".join(flags),
            "derived_from": cand.get("derived_from"),
            "symbol": symbol,
            "reporting_state": "MEASURED",
            "matched": False,
            "byte_match": False,
            "compiled": cand.get("compiled", True) is not False,
            "reference_size": len(ref_blob) if ref_blob is not None else None,
            "reference_bounded": bounded,
            "reference_address": (f"0x{cand['address']:08x}"
                                  if isinstance(cand.get("address"), int) else None),
            "ref_sxtb": ref_counts["sxtb"],
            "ref_ldrsb": ref_counts["ldrsb"],
        }
        if cand.get("size") is not None and ref_blob is not None:
            if not 0 <= int(cand["size"]) <= len(ref_blob):
                ref_blob = None
                row["candidate_error"] = f"size {cand['size']} exceeds reference window"
        if ref_blob is None:
            row.update({"size": None, "sxtb": None, "ldrsb": None,
                        "matching_words": None, "words_compared": None,
                        "status": "UNKNOWN"})
            row.setdefault("candidate_error", "no bounded reference window")
            rows.append(row)
            continue
        size = int(cand.get("size", len(ref_blob)))
        compiled = cand.get("compiled")
        if isinstance(compiled, (bytes, bytearray)):
            cand_bytes = bytes(compiled)[:size]
        else:
            cand_bytes = ref_blob[:size]
        cand_counts = count_sign_extend(cand_bytes, int(cand.get("address") or 0), thumb)
        unit = 2 if thumb else 4
        if "matching_words" in cand and cand["matching_words"] is not None:
            matching = int(cand["matching_words"])
        else:
            matching = sum(1 for i in range(size // unit)
                           if ref_blob[i * unit:(i + 1) * unit] == cand_bytes[i * unit:(i + 1) * unit])
        words = row["reference_size"] // unit
        status = "UNKNOWN" if matching is None else (
            "NOT_MATCHED" if (size != len(ref_blob) or matching < words or words == 0)
            else "ALL_WORDS_EQUAL_NOT_A_MATCH")
        for key in ("sxtb", "ldrsb", "status"):
            if cand.get(key) is not None:
                cand_counts[key] = cand[key]
        row.update({
            "size": size,
            "sxtb": cand_counts.get("sxtb"),
            "ldrsb": cand_counts.get("ldrsb"),
            "matching_words": matching,
            "words_compared": words,
            "status": status,
            "bytes_from_reference_stand_in": not isinstance(compiled, (bytes, bytearray)),
        })
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# Compilation (legacy matrix mode)
# --------------------------------------------------------------------------


def candidate_bss_layout(obj_path: Path) -> dict:
    """Section-relative offsets of the TU's file-scope objects in an ET_REL."""
    layout: dict[str, int] = {}
    with open(obj_path, "rb") as fh:
        elf = ELFFile(fh)
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            return layout
        for sym in symtab.iter_symbols():
            if sym.name in REFERENCE_BSS_LAYOUT and sym["st_size"]:
                layout[sym.name] = sym["st_value"]
    if "g_once" in layout:
        base = layout["g_once"]
        layout = {name: value - base for name, value in layout.items()}
    return layout


def toolchain_matrix(ndk: Path) -> list[dict]:
    """Candidate toolchains for legacy matrix mode."""
    r8e_bin = ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin"
    sysroot = ndk / "platforms/android-14/arch-arm/usr/include"
    arm_flags = ["-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=vfpv3-d16"]
    matrix: list[dict] = []
    if (r8e_bin / "arm-linux-androideabi-gcc").exists():
        for opt in ("-O2", "-O3", "-Os"):
            for prot in ("-fstack-protector", ""):
                # Base entry: toplevel reordering left on (the compiler's own
                # ordering, which is what the first pilot measured).
                matrix.append({
                    "name": f"ndk-r8e-gcc-4.4.3{opt}{prot}",
                    "cc": str(r8e_bin / "arm-linux-androideabi-gcc"),
                    "flags": [opt] + ([prot] if prot else []) + arm_flags + [f"-I{sysroot}"],
                })
                # Rejected-hypothesis bound, kept visible in every report:
                # -fno-toplevel-reorder does reproduce the reference .bss
                # offsets, but it removes the address caching the reference
                # uses, so the candidate getters grow to 48/52 bytes.  The
                # real explanation is getter definition order (see the note in
                # cpu_features.c); this row exists so that conclusion stays
                # measured rather than asserted.
                matrix.append({
                    "name": f"ndk-r8e-gcc-4.4.3{opt}{prot}-noToplevelReorder",
                    "cc": str(r8e_bin / "arm-linux-androideabi-gcc"),
                    "flags": [opt, "-fno-toplevel-reorder"]
                             + ([prot] if prot else []) + arm_flags + [f"-I{sysroot}"],
                })
                # Char signedness rows are historical GUESSED hypotheses, kept
                # so their effect stays measured.  The prebuilt cpufeatures
                # Android.mk declares no compiler flags at all; whether the
                # original TU needed -fsigned-char is UNKNOWN (the flag-row mode
                # `--flags-declared --flags-signedness-rows` records it).
                matrix.append({
                    "name": f"ndk-r8e-gcc-4.4.3{opt}{prot}-signedChar",
                    "cc": str(r8e_bin / "arm-linux-androideabi-gcc"),
                    "flags": [opt, "-fsigned-char"] + ([prot] if prot else [])
                             + arm_flags + [f"-I{sysroot}"],
                })
    return matrix


def compile_candidate(entry: dict, source: Path, include_dirs: list[Path], out: Path) -> tuple[bool, str]:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [entry["cc"], "-c", *entry["flags"]]
    for inc in include_dirs:
        cmd += ["-I", str(inc)]
    cmd += [str(source), "-o", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, ""


def run_matrix(reference: Path, source: Path, workdir: Path, include_dirs: list[Path],
               matrix: list[dict], keep_objects: bool) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    ref_bytes = reference.read_bytes()
    results = []
    for entry in matrix:
        obj_path = workdir / f"candidate_{entry['name']}.o"
        ok, err = compile_candidate(entry, source, include_dirs, obj_path)
        if not ok:
            results.append({"toolchain": entry["name"], "compiled": False, "error": err[:800]})
            continue
        obj_bytes = obj_path.read_bytes()
        windows = []
        for symbol, address, expected in TU_SYMBOLS:
            report = diff_window(ref_bytes, obj_bytes, address=address, symbol=symbol,
                                 expected_reference_sha256=REFERENCE_SHA256)
            windows.append({
                "symbol": symbol,
                "reference_address": f"0x{address:08x}",
                "expected_reference_size": expected,
                "reference_size": report["reference_size"],
                "candidate_size": report["candidate_size"],
                "status": report["status"],
                "counts": report.get("counts"),
                "words_compared": report.get("words_compared"),
                "instruction_stream_equal": report.get("instruction_stream_equal"),
                "code_window_equal": report.get("code_window_equal"),
                "divergence_offsets": [
                    w["offset"] for w in report.get("word_details", [])
                    if w["kind"] == "ENCODING_DIFFERENCE"
                ][:40],
            })
        all_equal = all(w["instruction_stream_equal"] for w in windows)
        exact = all(w["code_window_equal"] for w in windows)
        layout = candidate_bss_layout(obj_path)
        results.append({
            "toolchain": entry["name"],
            "compiled": True,
            "candidate_object": str(obj_path),
            "all_windows_instruction_equal": all_equal,
            "all_windows_code_equal": exact,
            "candidate_bss_layout": layout,
            "reference_bss_layout": REFERENCE_BSS_LAYOUT,
            "bss_layout_matches": layout == REFERENCE_BSS_LAYOUT,
            "windows": windows,
        })
        if not keep_objects:
            obj_path.unlink(missing_ok=True)
    return {
        "schema": "nova3.cpufeatures-pilot/1",
        "mode": "matrix",
        "reference": str(reference),
        "reference_sha256": REFERENCE_SHA256,
        "source": str(source),
        "toolchains": results,
        "source_matching_verified": False,
        "original_compiler_verified": False,
        "evidence_scope": (
            "Per-window A32 comparison of an ET_REL candidate against the linked "
            "reference. Agreement, when present, bounds instruction-selection "
            "equivalence for the stated toolchain only; it is not proof of the "
            "original compiler, options or linker, and relocation-driven words "
            "cannot match by construction."
        ),
    }


def cc_identity(cc: str) -> dict:
    try:
        proc = subprocess.run([cc, "--version"], capture_output=True, text=True)
        first = (proc.stdout or proc.stderr).splitlines()[:1]
        return {"path": cc, "version": first[0] if first else None}
    except OSError as exc:
        return {"path": cc, "version": None, "error": str(exc)}


def cpuInit_verdict(rows: list[dict]) -> str:
    """NOT_MATCHED or UNKNOWN for android_cpuInit; never MATCHED."""
    seen = []
    for row in rows:
        if not row.get("compiled", True):
            continue  # the flag effect could not be measured for this row
        for win in row.get("windows", []):
            if win.get("symbol") == "android_cpuInit":
                seen.append(win.get("status"))
    if seen and all(s == "NOT_MATCHED" for s in seen):
        return "NOT_MATCHED"
    return "UNKNOWN"


def run_flag_rows(reference: Path, source: Path, workdir: Path, include_dirs: list[Path],
                  flag_rows: list[dict], cc: Path, keep_objects: bool) -> dict:
    """Flag-row mode: one compile per explicit flag list, rows for every window."""
    workdir.mkdir(parents=True, exist_ok=True)
    ref_bytes = reference.read_bytes()
    rows_out: list[dict] = []
    for item in flag_rows:
        name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", item["label"])
        obj_path = workdir / f"candidate_{name}.o"
        sysroot = Path(cc).parents[5] / "platforms/android-14/arch-arm/usr/include"
        entry = {"cc": str(cc), "flags": [*PILOT_DEFAULT_FLAGS, *item["flags"]]}
        incs = include_dirs + ([sysroot] if sysroot.is_dir() else [])
        ok, err = compile_candidate(entry, source, incs, obj_path)
        row = {
            "label": item["label"],
            "compile_command_flags": list(entry["flags"]),
            "pilot_default_flags": list(PILOT_DEFAULT_FLAGS),
            "pilot_sysroot": str(sysroot) if sysroot.is_dir() else None,
            "tools_flags": list(entry["flags"]),
            "flags": list(item["flags"]),
            "flags_text": " ".join(item["flags"]),
            "derived_from": item.get("derived_from"),
            "reporting_state": "MEASURED",
            "byte_match": False,
            "compiled": ok,
            "windows": [],
        }
        if not ok:
            # One UNKNOWN window per symbol: the flag effect could not be
            # measured, and nothing is guessed.
            row["compile_error"] = err[:1200]
            row["android_cpuInit_status"] = "UNKNOWN"
            for symbol, address, _expected in TU_SYMBOLS:
                row["windows"].append({
                    "symbol": symbol, "reference_address": f"0x{address:08x}",
                    "flags": list(item["flags"]), "candidate_size": None,
                    "sxtb": None, "ldrsb": None, "matching_words": None,
                    "words_compared": None, "status": "UNKNOWN", "byte_match": False,
                })
            rows_out.append(row)
            continue
        obj_bytes = obj_path.read_bytes()
        layout = candidate_bss_layout(obj_path)
        windows = []
        for symbol, address, expected in TU_SYMBOLS:
            rep = diff_window(ref_bytes, obj_bytes, address=address, symbol=symbol,
                              expected_reference_sha256=REFERENCE_SHA256)
            ref_blob = reference_window(ref_bytes, address) or b""
            cand_c, from_object = candidate_object(ref_bytes, address, symbol, obj_bytes, None)
            cand_counts = count_sign_extend(cand_c, address)
            ref_counts = count_sign_extend(ref_blob, address)
            matching = rep["counts"]["EQUAL"] + rep["counts"]["RELOCATION_FIELD"]
            status = "NOT_MATCHED" if (
                rep["reference_size"] != rep["candidate_size"]
                or rep["counts"]["ENCODING_DIFFERENCE"] != 0) else "ALL_WORDS_EQUAL_NOT_A_MATCH"
            windows.append({
                "symbol": symbol,
                "reference_address": f"0x{address:08x}",
                "reference_size": rep["reference_size"],
                "expected_reference_size": expected,
                "candidate_size": rep["candidate_size"],
                "reference_sxtb": ref_counts["sxtb"],
                "reference_ldrsb": ref_counts["ldrsb"],
                "sxtb": cand_counts["sxtb"],
                "ldrsb": cand_counts["ldrsb"],
                "matching_words": matching,
                "words_compared": rep["words_compared"],
                "equal_words": rep["counts"]["EQUAL"],
                "relocation_field_words": rep["counts"]["RELOCATION_FIELD"],
                "literal_pool_words": rep["counts"]["LITERAL_POOL"],
                "encoding_difference_words": rep["counts"]["ENCODING_DIFFERENCE"],
                "window_status": rep["status"],
                "status": status,
                "byte_match": False,
                "from_object": from_object,
                "flags": list(item["flags"]),
            })
        cpu = next(w for w in windows if w["symbol"] == "android_cpuInit")
        row.update({
            "android_cpuInit_status": "NOT_MATCHED" if cpu["status"] == "NOT_MATCHED" else "UNKNOWN",
            "candidate_bss_layout": layout,
            "bss_layout_matches": layout == REFERENCE_BSS_LAYOUT,
            "windows": windows,
        })
        rows_out.append(row)
        if not keep_objects:
            obj_path.unlink(missing_ok=True)
    return {
        "schema": "nova3.cpufeatures-flag-rows/1",
        "mode": "flag-rows",
        "reference": str(reference),
        "reference_sha256": REFERENCE_SHA256,
        "source": str(source),
        "compiler": cc_identity(str(cc)),
        "reference_windows": [
            {"symbol": s, "address": f"0x{a:08x}", "reported_size": z}
            for s, a, z in TU_SYMBOLS
        ],
        "rows": rows_out,
        "android_cpuInit_result": cpuInit_verdict(rows_out),
        "rows_measured_not_matched": all(
            r.get("reporting_state") == "MEASURED" and r.get("byte_match") is False
            for r in rows_out),
        "source_matching_verified": False,
        "original_compiler_verified": False,
        "original_toolchain_verified": False,
        "evidence_scope": (
            "Each row is a measurement of one explicit, labelled flag list, not a "
            "match claim: it records the compile flags, candidate byte size, the "
            "sxtb/ldrsb instruction counts and the number of words equal to (or "
            "relocation-masked-equal to) the reference window.  The original "
            "libcpu-features.a was not built, linked or loaded; .mk files were read "
            "as text evidence only.  android_cpuInit is NOT MATCHED or UNKNOWN."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--source", type=Path,
                        default=SCRIPTS / "reconstruction/recovered/libNOVA3_neon/cpu_features.c")
    parser.add_argument("--workdir", type=Path,
                        default=SCRIPTS / ".cache/cpufeat/pilot")
    parser.add_argument("--ndk", type=Path, default=DEFAULT_NDK_R8E)
    parser.add_argument("--cc", type=Path,
                        help="compiler for flag-row mode (default: NDK r8e GCC 4.4.3)")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--keep-objects", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--flags-declared", action="store_true",
                      help="flag-row mode: prebuilt cpufeatures LOCAL_CFLAGS only")
    mode.add_argument("--flags-from-mk", type=Path,
                      help="flag-row mode: LOCAL_CFLAGS from this Android.mk")
    mode.add_argument("--flags", help="flag-row mode: explicit flag string")
    parser.add_argument("--label", help="row label for --flags / --flags-from-mk")
    parser.add_argument("--flags-signedness-rows", action="store_true",
                        help="with --flags-declared, also add signed/unsigned rows")
    args = parser.parse_args(argv)

    if args.flags_declared or args.flags_from_mk or args.flags is not None:
        if args.cc is not None:
            cc = args.cc
        else:
            cc = args.ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/linux-x86_64/bin/arm-linux-androideabi-gcc"
        if not Path(cc).exists():
            print(f"flag-row mode needs a compiler; not found: {cc}", file=sys.stderr)
            return 2
        extra = split_flags(args.flags) if args.flags else []
        if args.flags_declared:
            if args.flags_signedness_rows:
                flag_rows = default_flag_sets()
            else:
                declared = declared_flag_set()
                if declared["error"]:
                    print(f"declared flags UNKNOWN: {declared['error']}", file=sys.stderr)
                    return 2
                flag_rows = [{"label": DECLARED_MK_LABEL, "flags": declared["flags"],
                              "derived_from": None}]
                for mk in declared["mk"]:  # redirect stub, then the module
                    raw = {k: v for k, v in mk["raw"].items()}
                    print(f"mk evidence: {mk['mk']} flags_vars={raw or 'none (no LOCAL_CFLAGS)'}")
                print(f"declared LOCAL_CFLAGS: {declared['flags'] or '[] (none declared)'}")
                print("pilot defaults (not original flags): " + " ".join(PILOT_DEFAULT_FLAGS))
        elif args.flags_from_mk:
            parsed = declared_flags_from_mk(args.flags_from_mk)
            if parsed["error"]:
                print(f"flags from {args.flags_from_mk} UNKNOWN: {parsed['error']}",
                      file=sys.stderr)
                return 2
            flag_rows = [{"label": args.label or f"{args.flags_from_mk}:LOCAL_CFLAGS",
                          "flags": extra + parsed["flags"], "derived_from": None}]
        else:
            flag_rows = [{"label": args.label or "explicit", "flags": extra,
                          "derived_from": None}]
        include_dirs = [args.source.parent]
        report = run_flag_rows(args.reference, args.source, args.workdir, include_dirs,
                               flag_rows, Path(cc), args.keep_objects)
        out = args.out or (args.workdir / "cpufeatures_flag_rows.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        rows_file = args.workdir / "cpufeatures_flag_rows.json"
        if rows_file != out:
            rows_file.parent.mkdir(parents=True, exist_ok=True)
            rows_file.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        for row in report["rows"]:
            if not row["compiled"]:
                print(f"{row['label']:<50} COMPILE_FAILED: {row['compile_error'][:160]}")
                continue
            for w in row["windows"]:
                print(f"{row['label']:<50} {w['symbol']:<24} size={w['candidate_size']}/{w['reference_size']}"
                      f" sxtb={w['sxtb']}(ref {w['reference_sxtb']})"
                      f" ldrsb={w['ldrsb']}(ref {w['reference_ldrsb']})"
                      f" match={w['matching_words']}/{w['words_compared']} {w['status']}")
        print(f"android_cpuInit: {report['android_cpuInit_result']} (NOT A MATCH CLAIM)")
        print(f"report: {out}")
        return 0

    matrix = toolchain_matrix(args.ndk)
    if not matrix:
        print(f"no usable compiler found under {args.ndk}", file=sys.stderr)
        return 2
    include_dirs = [args.source.parent]
    report = run_matrix(args.reference, args.source, args.workdir, include_dirs, matrix,
                        args.keep_objects)
    out = args.out or (args.workdir / "cpufeatures_pilot.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for entry in report["toolchains"]:
        if not entry["compiled"]:
            print(f"{entry['toolchain']:<34} COMPILE_FAILED")
            continue
        flags = []
        for w in entry["windows"]:
            mark = "EQ" if w["instruction_stream_equal"] else (
                "SIZE" if w["reference_size"] != w["candidate_size"] else "DIFF")
            flags.append(f"{w['symbol'].replace('android_getCpu','get').replace('android_cpuInit','cpuInit')}"
                         f"[{w['candidate_size']}/{w['reference_size']}]{mark}")
        layout = entry.get("candidate_bss_layout", {})
        note = "bss=ref" if entry.get("bss_layout_matches") else \
               f"bss_offsets={layout} (ref {REFERENCE_BSS_LAYOUT})"
        print(f"{entry['toolchain']:<34} " + "  ".join(flags) + f"   [{note}]")
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
