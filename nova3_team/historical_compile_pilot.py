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
REFERENCE_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
WINDOW_SHA256 = "435ee619bd5512e70580bde9ca41f789984afa3d70fb55a4ef6a6a85995604ca"
ADDRESS, SIZE = 0x000d2f10, 44
SYMBOL = "nova3_recovered_cpu_count"
MAX_HEADERS, MAX_HEADER_BYTES = 12000, 128 * 1024 * 1024
MAX_LOG = 2 * 1024 * 1024
SCHEMA = "nova3.historical-compile-pilot/1"


class PilotError(ValueError):
    pass


def profiles(count: int) -> list[list[str]]:
    if type(count) is not int or not 4 <= count <= 8:
        raise PilotError("require four to eight sequential hypotheses")
    # Cover all four optimization levels even in the smallest run.
    return [ht.validate_flags(ht.BASE_FLAGS + [opt, frame])
            for frame in ("-fomit-frame-pointer", "-fno-omit-frame-pointer")
            for opt in ("-O2", "-Os", "-O1", "-O3")][:count]


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
    temp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(out / "report.json")


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG, MAX_LOG))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 31))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _compile(command: list[str], directory: Path, env: dict, timeout: int) -> dict:
    """No shell, inherited search hooks, network commands or surviving children."""
    with (directory / "stdout.log").open("xb") as stdout, (directory / "stderr.log").open("xb") as stderr:
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


def compare(reference: bytes, obj: bytes, targets: dict[str, int]) -> dict:
    """All-byte diagnostic; resolved equality is never a real linked-ELF grade."""
    ArmElf(obj)  # Stronger EABI5 validation in addition to ET_REL comparator.
    raw = compare_window(reference, obj, address=ADDRESS, symbol=SYMBOL,
                         expected_reference_sha256=REFERENCE_SHA256)
    report = dict(raw=raw, relocated=dict(status="NOT_ATTEMPTED"),
                  linked_elf_comparison="NOT_PERFORMED")
    window = candidate_window(obj, SYMBOL)
    if window["thumb"] or window["size"] != SIZE:
        return report
    try:
        window, relocs, outside = object_relocations(obj, SYMBOL)
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


def _claims() -> dict:
    # Even raw/resolved equality cannot upgrade these independent claims.
    return dict(source_equivalence="NOT_MATCHED", historical_toolchain="UNKNOWN",
                original_object="NOT_AVAILABLE", original_relocations_recovered=False,
                behavior_verified=False, whole_so_sha_match=False,
                linked_window_match="NOT_TESTED", abi="DIFFERENT_CONTEXT_POINTER_CANDIDATE")


def run(*, compiler: Path, compiler_sha256: str, reference: Path, source: Path,
        out: Path, ndk_root: Path, sysroot: Path, includes: list[Path],
        tools: dict[str, tuple[Path, str]], count: int = 8, timeout: int = 30,
        targets: dict[str, int] | None = None) -> dict:
    variants = profiles(count)
    if type(timeout) is not int or not 1 <= timeout <= 30:
        raise PilotError("compile timeout must be one to thirty seconds")
    targets = {} if targets is None else dict(targets)
    if len(targets) > 16 or any(not isinstance(k, str) or not k or len(k) > 256
            or type(v) is not int or not 0 <= v < 2**32 for k, v in targets.items()):
        raise PilotError("invalid explicit symbol targets")
    out = _output(out)
    if source.expanduser().resolve(strict=True) != SOURCE.resolve(strict=True):
        raise PilotError("only the fixed existing source candidate is permitted")
    source = source.resolve()
    source_data = ht._read_bytes(source, MAX_SOURCE)
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
    report = dict(schema=SCHEMA, status="PREFLIGHT_STARTED", claims=_claims(),
                  reference_sha256=REFERENCE_SHA256, reference_address=ADDRESS,
                  reference_size=SIZE, reference_window_sha256=WINDOW_SHA256,
                  reference_file_offset=row["file_offset"], reference_thumb=False,
                  source_sha256=ht.digest(source_data), source=str(source),
                  tool_pins=pins, headers=manifest, targets=targets, profiles=[],
                  notes="Owner pins are execution authorization, not archive authentication. "
                        "Historical compiler/version/flags unknown; candidate source unchanged.")
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
            version = ht._query(pins[name], "--version")
            report.setdefault("support_metadata", {})[name] = version
            banner = version["stdout"]
            if version["exit_code"] != 0 or (name == "assembler" and (
                    "GNU assembler" not in banner or not re.search(
                        r"(?<![\w-])" + re.escape(ht.TARGET) + r"(?![\w-])", banner))):
                raise PilotError("support tool version/target rejected")
            if name == "cc1plus" and (not re.search(r"(?<![\d.])4\.4\.3(?![\d.])", banner)
                                       or "GNU C++" not in banner):
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
            # Explicit standard/system includes only; no injected legacy shims.
            command = [pins["compiler"]["path"], "-B" + str(search) + "/",
                       "--sysroot=" + str(sysroot), "-nostdinc", "-nostdinc++", *flags]
            for include in includes:
                command += ["-isystem", str(include)]
            command += ["-isystem", str(sysroot / "usr/include"), "-c", str(source), "-o", str(obj)]
            item = dict(index=index, flags=flags, flags_are_hypotheses=True,
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
                    item["comparison"] = compare(original, ht._read_bytes(obj, 64 * 1024 * 1024), targets)
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
        report["status"] = "NOT_MATCHED"  # Candidate ABI differs; no automatic promotion.
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
