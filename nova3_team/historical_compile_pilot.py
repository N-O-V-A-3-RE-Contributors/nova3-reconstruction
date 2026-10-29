"""Offline GCC 4.4.3 candidate pilot for one fixed Premium A32 window.

Private diagnostics only. An owner pin authorizes execution, not distribution
provenance. Compilation, raw equality and relocation arithmetic never certify
source, original-object, historical-toolchain, behavioural or library matching.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys

from . import historical_toolchain as ht
from .compiler_matrix import MAX_SOURCE
from .object_compare import candidate_window, compare_window
from .relocation_probe import (ArmElf, comparison, linked_window,
                               object_relocations, relocate)

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "reconstruction/recovered/libNOVA3_neon/block_000d2f10.cpp"
NOARG_SOURCE = REPO / "reconstruction/research/arm32_cpu_query_gcc443.cpp"
REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
WINDOW_SHA256 = "435ee619bd5512e70580bde9ca41f789984afa3d70fb55a4ef6a6a85995604ca"
ADDRESS, SIZE = 0x000d2f10, 44
SYMBOL = "nova3_recovered_cpu_count"
NOARG_SYMBOL = "android_getCpuCount"
MAX_HEADERS, MAX_HEADER_BYTES = 12000, 128 * 1024 * 1024
MAX_LOG = 2 * 1024 * 1024
SCHEMA = "nova3.historical-compile-pilot/2"


class PilotError(ValueError):
    pass


def source_contract(source: Path, data: bytes) -> dict:
    """Recognize only two explicit candidates; never infer an ABI from ELF names.

    The new hypothesis has a deliberately tiny token contract, not a general
    C++ parser. Whitespace/comments may change; includes, declarations and body
    must be reviewed here before changing the experiment. In particular, C
    linkage alone does not prove a function takes no arguments.
    """
    if source == SOURCE.resolve(strict=True):
        return dict(candidate="legacy_context", symbol=SYMBOL, compatible=False,
                    abi="DIFFERENT_CONTEXT_POINTER_CANDIDATE",
                    reason="Legacy C++17 <cstdint>/nested namespaces and context-pointer ABI "
                           "are incompatible with this C++03 no-argument pilot")
    if source != NOARG_SOURCE.resolve(strict=True):
        raise PilotError("only the legacy candidate or explicit GCC443 hypothesis is permitted")
    text = data.decode("utf-8")
    text = re.sub(r"//[^\n]*|/\*.*?\*/", " ", text, flags=re.S)
    expected = '''#include <pthread.h>
        typedef char Nova3OnceIs32Bits[sizeof(pthread_once_t) == 4 ? 1 : -1];
        typedef char Nova3CountIs32Bits[sizeof(int) == 4 ? 1 : -1];
        extern "C" {
        extern pthread_once_t g_once;
        extern int g_cpuCount;
        void android_cpuInit(void);
        int android_getCpuCount(void) {
            (void)pthread_once(&g_once, android_cpuInit);
            return g_cpuCount;
        }
        }'''
    # Tokenize punctuation separately, so whitespace cannot join identifiers.
    tokens = lambda value: re.findall(r'[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]', value)
    compatible = tokens(text) == tokens(expected)
    return dict(candidate="gcc443_noarg", symbol=NOARG_SYMBOL,
                compatible=compatible, abi="NO_ARGUMENT_GLOBAL_STATE_HYPOTHESIS",
                reason=None if compatible else "C++03/no-argument source contract rejected; "
                       "check includes, extern C signature, external dependencies and body",
                external_dependencies=["android_cpuInit", "g_cpuCount", "g_once", "pthread_once"])


def normalize_targets(targets: dict[str, int]) -> dict[str, int]:
    if (len(targets) > 16 or any(not isinstance(k, str)
            or not re.fullmatch(r"[A-Za-z_.$][A-Za-z_0-9.$]{0,255}", k)
            or type(v) is not int or not 0 <= v < 2**32 for k, v in targets.items())):
        raise PilotError("invalid explicit symbol targets")
    return dict(sorted(targets.items()))


def profiles(count: int) -> list[list[str]]:
    if type(count) is not int or not 4 <= count <= 8:
        raise PilotError("require four to eight sequential hypotheses")
    # Cover all four optimization levels even in the smallest run.
    return [ht.validate_flags(ht.BASE_FLAGS + [opt, frame])
            for frame in ("-fomit-frame-pointer", "-fno-omit-frame-pointer")
            for opt in ("-O2", "-Os", "-O1", "-O3")][:count]


def gcc443_flags(flags: list[str]) -> list[str]:
    """GCC 4.4.3 names its pre-C++11 (including C++03 corrections) mode c++98.

    The shared policy's c++03 spelling is a later alias and is rejected by the
    actual r8e frontend. Preserve that policy; translate only this exact option
    after validation, recording requested and effective flags separately.
    """
    return ["-std=c++98" if flag == "-std=c++03" else flag
            for flag in ht.validate_flags(flags)]


def _directory(path: Path, root: Path) -> Path:
    path = path.expanduser().resolve(strict=True)
    if path == root or not path.is_relative_to(root) or not path.is_dir():
        raise PilotError("search directory must be beneath explicit NDK root")
    return path


def header_manifest(paths: list[Path]) -> dict:
    """Hash bounded explicit search trees; reject symlinks and special files.

    Directory hashes include relative names and contents, not absolute names.
    This is conservative search-tree provenance, not a recovered dependency set.
    """
    records, count, total = [], 0, 0
    for root in paths:
        if not root.is_dir() or root.is_symlink():
            raise PilotError("missing or symlinked header tree")
        entries = []
        def walk_error(error: OSError) -> None:
            raise error
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            dirs.sort()
            files.sort()
            count += len(dirs) + len(files)
            if count > MAX_HEADERS:
                raise PilotError("header search tree exceeds entry bound")
            for name in dirs + files:
                if (Path(directory) / name).is_symlink():
                    raise PilotError("symlinks in header search trees unsupported")
            for name in files:
                path = Path(directory) / name
                data = ht._read_bytes(path, MAX_SOURCE * 8)
                total += len(data)
                if total > MAX_HEADER_BYTES:
                    raise PilotError("header search trees exceed byte bound")
                entries.append([path.relative_to(root).as_posix(), ht.digest(data)])
        records.append(dict(path=str(root), files=len(entries),
                            sha256=ht.digest(json.dumps(entries, separators=(",", ":")).encode())))
    return dict(trees=records, entries=count, total_bytes=total)


def _reference(path: Path) -> tuple[bytes, dict]:
    data = ht._read_bytes(path, 64 * 1024 * 1024)
    if ht.digest(data) != REFERENCE_SHA256:
        raise PilotError("reference SHA-256 mismatch")
    code, row = linked_window(data, ADDRESS)  # ELF32/ARM/EABI5, symbol + PT_LOAD
    if len(code) != SIZE or ht.digest(code) != WINDOW_SHA256:
        raise PilotError("fixed A32 reference window mismatch")
    return data, row


def _output(path: Path) -> Path:
    cache = (REPO / ".cache").resolve()
    path = path.expanduser().absolute()
    # Never accept symlinked output ancestry, even when it resolves into cache.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise PilotError("symlinked output unsupported")
    path = path.resolve()
    if (not path.is_relative_to(cache) or path == cache or path.exists()
            or "/.cache/" not in (REPO / ".gitignore").read_text().splitlines()):
        raise PilotError("output must be a new ignored repository .cache directory")
    return path


def _save(out: Path, report: dict) -> None:
    temp = out / "report.new"
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   "w", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temp.replace(out / "report.json")


def _limits() -> None:
    os.umask(0o077)  # Candidate objects are private even without the shell wrapper.
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG, MAX_LOG))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 31))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _compile(command: list[str], directory: Path, env: dict, timeout: int) -> dict:
    """No shell, inherited search hooks, network commands or surviving children."""
    def private_log(name: str):
        return os.fdopen(os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o600), "wb")
    with private_log("stdout.log") as stdout, private_log("stderr.log") as stderr:
        with subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=stderr, start_new_session=True,
                              preexec_fn=_limits) as child:
            status = "COMPILE_FINISHED"
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                status = "COMPILE_TIMEOUT"
                code = None
            finally:
                # Also kill children left behind when the driver exits first.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
    return dict(status=status, exit_code=code, timeout_seconds=timeout,
                stdout_sha256=ht.file_sha256(directory / "stdout.log"),
                stderr_sha256=ht.file_sha256(directory / "stderr.log"))


def compare(reference: bytes, obj: bytes, targets: dict[str, int], *, symbol: str | None = None) -> dict:
    """All-byte diagnostic; resolved equality is never a real linked-ELF grade."""
    symbol = SYMBOL if symbol is None else symbol
    targets = normalize_targets(targets)
    ArmElf(obj)  # Stronger EABI5 validation in addition to ET_REL comparator.
    raw = compare_window(reference, obj, address=ADDRESS, symbol=symbol,
                         expected_reference_sha256=REFERENCE_SHA256)
    report = dict(raw=raw, relocated=dict(status="NOT_ATTEMPTED"),
                  linked_elf_comparison="NOT_PERFORMED")
    window = candidate_window(obj, symbol)
    if window["thumb"] or window["size"] != SIZE:
        return report
    try:
        window, relocs, outside = object_relocations(obj, symbol)
        resolved, rows = relocate(window["bytes"], relocs, ADDRESS, targets)
    except ValueError as exc:
        report["relocated"] = dict(status="UNSUPPORTED_OR_UNRESOLVED", error=str(exc))
        return report
    original, _ = linked_window(reference, ADDRESS)
    report["relocated"] = comparison(original, resolved, "RESOLVED_WINDOW_EQUAL",
                                       "RESOLVED_WINDOW_MISMATCH")
    report.update(relocations=rows, outside_function_relocations=outside,
                  policy="Explicit candidate target assumptions; all bytes compared in memory; "
                         "no masking, linked ELF, recovered .o or original relocations.")
    return report


def _claims(candidate: dict | None = None) -> dict:
    # Even raw/resolved equality cannot upgrade these independent claims.
    return dict(source_equivalence="NOT_MATCHED", historical_toolchain="UNKNOWN",
                original_object="NOT_AVAILABLE", original_relocations_recovered=False,
                behavior_verified=False, whole_so_sha_match=False,
                linked_window_match="NOT_TESTED", abi_verified=False,
                abi=candidate["abi"] if candidate else "DIFFERENT_CONTEXT_POINTER_CANDIDATE")


def _frontend_version(pin: dict, directory: Path) -> dict:
    # GCC 4.4.3 cc1plus --version exits zero without a banner. Its actual
    # -version output is on stderr. Syntax-only + /dev/null prevents artifacts.
    options = ["-quiet", "-version", "-fsyntax-only"]
    ht._pin(Path(pin["path"]), pin["sha256"])
    result = subprocess.run([pin["path"], *options], cwd=directory,
                            stdin=subprocess.DEVNULL, capture_output=True, check=False,
                            timeout=ht.QUERY_TIMEOUT,
                            env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"})
    ht._pin(Path(pin["path"]), pin["sha256"])
    if max(len(result.stdout), len(result.stderr)) > ht.MAX_OUTPUT:
        raise PilotError("frontend version output exceeds limit")
    return dict(option=options, exit_code=result.returncode,
                stdout=result.stdout.decode("utf-8", errors="replace"),
                stderr=result.stderr.decode("utf-8", errors="replace"),
                stdout_sha256=ht.digest(result.stdout), stderr_sha256=ht.digest(result.stderr))


def run(*, compiler: Path, compiler_sha256: str, reference: Path, source: Path,
        out: Path, ndk_root: Path, sysroot: Path, includes: list[Path],
        tools: dict[str, tuple[Path, str]], count: int = 8, timeout: int = 30,
        targets: dict[str, int] | None = None) -> dict:
    variants = profiles(count)
    if type(timeout) is not int or not 1 <= timeout <= 30:
        raise PilotError("compile timeout must be one to thirty seconds")
    targets = normalize_targets({} if targets is None else dict(targets))
    out = _output(out)
    source = source.expanduser().resolve(strict=True)
    if source not in (SOURCE.resolve(strict=True), NOARG_SOURCE.resolve(strict=True)):
        raise PilotError("only the two explicit source paths are permitted")
    source_data = ht._read_bytes(source, MAX_SOURCE)
    candidate = source_contract(source, source_data)
    if not candidate["compatible"]:
        out.mkdir(parents=True, mode=0o700, exist_ok=False)
        report = dict(schema=SCHEMA, status="BLOCKED", blocker="SOURCE_LANGUAGE_OR_ABI_INCOMPATIBLE",
                      candidate=candidate, source=str(source), source_sha256=ht.digest(source_data),
                      claims=_claims(candidate), profiles=[], error=candidate["reason"])
        _save(out, report)
        return report
    original, row = _reference(reference)  # Never invoke a tool for a bad reference.
    root = ndk_root.expanduser().resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor):
        raise PilotError("explicit bounded NDK root required")
    sysroot = _directory(sysroot, root)
    system_include = _directory(sysroot / "usr/include", root)
    if not 1 <= len(includes) <= 8:
        raise PilotError("require one to eight explicit include directories")
    includes = [_directory(p, root) for p in includes]
    if len(set(includes)) != len(includes):
        raise PilotError("duplicate include directory")
    manifest = header_manifest([system_include, *includes])
    if set(tools) != {"assembler", "cc1plus"}:
        raise PilotError("explicit assembler and cc1plus pins required")
    pins = {"compiler": ht._pin(compiler, compiler_sha256)}
    pins.update({name: ht._pin(*entry) for name, entry in tools.items()})
    if any(not Path(p["path"]).is_relative_to(root) for p in pins.values()):
        raise PilotError("all pinned executables must be beneath NDK root")
    # Record failures/timeouts durably before the first executable invocation.
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = dict(schema=SCHEMA, status="PREFLIGHT_STARTED", claims=_claims(candidate),
                  candidate=candidate,
                  reference_sha256=REFERENCE_SHA256, reference_address=ADDRESS,
                  reference_size=SIZE, reference_window_sha256=WINDOW_SHA256,
                  reference_file_offset=row["file_offset"], reference_thumb=False,
                  source_sha256=ht.digest(source_data), source=str(source),
                  tool_pins=pins, headers=manifest, targets=targets, profiles=[],
                  notes="Owner pins are execution authorization, not archive authentication. "
                        "Historical compiler/version/flags unknown; source-owned ABI hypothesis.")
    _save(out, report)
    try:
        metadata = ht.fingerprint(compiler, trusted_sha256=compiler_sha256)
        report["compiler_metadata"] = metadata
        if not metadata["GCC_4_4_3_CANDIDATE_AVAILABLE"]:
            raise PilotError("compiler version or target rejected")
        for name in ("assembler", "cc1plus"):
            query = metadata["queries"][name]
            if query["exit_code"] != 0:
                raise PilotError("support tool query failed")
            selected = Path(query["stdout"].strip())
            if not selected.is_absolute() or selected.resolve(strict=True) != Path(pins[name]["path"]):
                raise PilotError("compiler-selected support tool differs from explicit pin")
            version = (_frontend_version(pins[name], out) if name == "cc1plus"
                       else ht._query(pins[name], "--version"))
            report.setdefault("support_metadata", {})[name] = version
            banner = version["stdout"] + version.get("stderr", "")
            if version["exit_code"] != 0 or (name == "assembler" and (
                    "GNU assembler" not in banner or not re.search(
                        r"(?<![\w-])" + re.escape(ht.TARGET) + r"(?![\w-])", banner))):
                raise PilotError("support tool version/target rejected")
            if name == "cc1plus" and not re.search(
                    r"GNU C\+\+ \(GCC\) version 4\.4\.3(?![\d.])", banner):
                raise PilotError("cc1plus version rejected")
        # -B points only at pinned executables, not a broad PATH tool search.
        search = out / "pinned-tools"
        search.mkdir(mode=0o700)
        for name, alias in (("assembler", "as"), ("cc1plus", "cc1plus")):
            (search / alias).symlink_to(pins[name]["path"])
        for index, flags in enumerate(variants):
            directory = out / f"profile-{index:02d}"
            directory.mkdir(mode=0o700)
            obj = directory / "candidate.o"
            effective_flags = gcc443_flags(flags)
            # Explicit standard/system includes only; no injected legacy shims.
            command = [pins["compiler"]["path"], "-B" + str(search) + "/",
                       "--sysroot=" + str(sysroot), "-nostdinc", "-nostdinc++",
                       "-x", "c++", "-pedantic-errors", *effective_flags]
            for include in includes:
                command += ["-isystem", str(include)]
            command += ["-isystem", str(sysroot / "usr/include"), "-c", str(source), "-o", str(obj)]
            item = dict(index=index, flags=effective_flags, requested_flags=flags,
                        flags_are_hypotheses=True,
                        symbol=candidate["symbol"], source_sha256=ht.digest(source_data),
                        source_language="c++03", abi=candidate["abi"],
                        compiler_sha256=pins["compiler"]["sha256"],
                        compiler_version_sha256=metadata["queries"]["version"]["stdout_sha256"],
                        reference_sha256=REFERENCE_SHA256,
                        command=command, status="STARTED", comparison=None)
            report["profiles"].append(item)
            _save(out, report)
            for pin in pins.values():
                ht._pin(Path(pin["path"]), pin["sha256"])
            if (ht._read_bytes(source, MAX_SOURCE) != source_data
                    or header_manifest([system_include, *includes]) != manifest):
                raise PilotError("source or headers changed")
            env = dict(PATH=str(search), LC_ALL="C", LANG="C", TMPDIR=str(directory))
            result = _compile(command, directory, env, timeout)
            item.update(compile=result)
            if result["status"] == "COMPILE_TIMEOUT":
                item["status"] = "COMPILE_TIMEOUT"
            elif result["exit_code"] != 0:
                item["status"] = "COMPILE_FAILED"
            else:
                try:
                    obj_data = ht._read_bytes(obj, 64 * 1024 * 1024)
                    item["object_sha256"] = ht.digest(obj_data)
                    item["comparison"] = compare(original, obj_data, targets,
                                                  symbol=candidate["symbol"])
                    item["status"] = "COMPARED_NOT_CERTIFIED"
                except ValueError as exc:
                    item.update(status="COMPARISON_REJECTED", error=str(exc))
            for pin in pins.values():
                ht._pin(Path(pin["path"]), pin["sha256"])
            if (ht._read_bytes(source, MAX_SOURCE) != source_data
                    or header_manifest([system_include, *includes]) != manifest):
                raise PilotError("source or headers changed during compile")
            _save(out, report)
            if item["status"] == "COMPILE_TIMEOUT":
                break  # A timeout is not a reason to launch seven more compilers.
        report["status"] = "NOT_MATCHED"  # No automatic promotion from compile/equality.
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="BLOCKED", error_type=type(exc).__name__, error=str(exc))
    _save(out, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("compiler", "reference", "source", "out", "ndk-root", "sysroot", "assembler", "cc1plus"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("compiler", "assembler", "cc1plus"):
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--include", type=Path, action="append", required=True)
    parser.add_argument("--profiles", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--target", action="append", default=[], metavar="SYMBOL=VALUE")
    args = parser.parse_args(argv)
    try:
        targets = {}
        for target in args.target:
            name, value = target.split("=", 1)
            if not name or name in targets:
                raise PilotError("duplicate/empty target")
            targets[name] = int(value, 0)
        report = run(compiler=args.compiler, compiler_sha256=args.compiler_sha256,
                     reference=args.reference, source=args.source, out=args.out,
                     ndk_root=args.ndk_root, sysroot=args.sysroot, includes=args.include,
                     tools={k: (getattr(args, k), getattr(args, k + "_sha256"))
                            for k in ("assembler", "cc1plus")},
                     count=args.profiles, timeout=args.timeout, targets=targets)
        print(report["status"] + "; source, original object and whole library NOT MATCHED; private report retained")
        if report["status"] == "BLOCKED":
            return 2
        return 0 if all(p["status"] == "COMPARED_NOT_CERTIFIED" for p in report["profiles"]) else 1
    except (OSError, ValueError, subprocess.SubprocessError):
        print("historical pilot rejected input; check local paths, hashes and trust pins", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
