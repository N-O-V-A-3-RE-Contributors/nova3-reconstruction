"""Offline historical ARM GCC gate. JSON goes to stdout; no builds or downloads.

Discovery only checks fixed relative paths. A local SHA pin authorizes metadata
queries; it does not authenticate an NDK distribution or prove Gameloft used it.
Profiles remain nonexecuting plans until independently replayed and reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from .object_matching import SCHEMA as MATRIX_PROFILE_SCHEMA

SCHEMA = "nova3.historical-toolchain/1"
MAX_PROFILES = 8
QUERY_TIMEOUT = 10
MAX_FILE = 128 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
TARGET = "arm-linux-androideabi"
DEFAULT_ROOTS = tuple(Path("/opt/android-ndk" + suffix) for suffix in
                      ("", "-r7", "-r7b", "-r7c", "-r8", "-r8b", "-r8c", "-r8d", "-r8e")) + (
    Path("/opt/android-sdk/ndk-bundle"), Path.home() / "Android/Sdk/ndk-bundle")
QUERIES = {"version": "--version", "gcc_version": "-dumpversion",
           "target": "-dumpmachine", "specs": "-dumpspecs", "sysroot": "-print-sysroot",
           "linker": "-print-prog-name=ld", "assembler": "-print-prog-name=as",
           "cc1plus": "-print-prog-name=cc1plus"}
BASE_FLAGS = ["-marm", "-march=armv7-a", "-mfpu=neon", "-mfloat-abi=softfp",
              "-fPIC", "-std=c++03"]
SHA = re.compile(r"[0-9a-f]{64}\Z")


class ToolchainError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular_fd(path: Path, limit: int) -> int:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ToolchainError("expected bounded regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_bytes(path: Path, limit: int) -> bytes:
    with os.fdopen(_regular_fd(path, limit), "rb") as stream:
        before = os.fstat(stream.fileno())
        payload = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if (len(payload) != before.st_size or len(payload) > limit
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise ToolchainError("file changed while reading")
        return payload


def file_sha256(path: Path) -> str:
    """Bounded regular-file read, including FIFO rejection before reading."""
    fd = _regular_fd(path, MAX_FILE)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
            raise ToolchainError("expected bounded regular file")
        h = hashlib.sha256()
        size = 0
        while True:
            block = stream.read(min(1024 * 1024, MAX_FILE + 1 - size))
            if not block:
                break
            size += len(block)
            if size > MAX_FILE:
                raise ToolchainError("file grew beyond limit")
            h.update(block)
        after = os.fstat(stream.fileno())
        if (size != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ToolchainError("file changed while fingerprinting")
        return h.hexdigest()


def validate_flags(flags: list[str]) -> list[str]:
    """Only this one-function A32 C++03 experiment, never arbitrary GCC options."""
    allowed = set(BASE_FLAGS + ["-O0", "-O1", "-O2", "-O3", "-Os",
                               "-fomit-frame-pointer", "-fno-omit-frame-pointer"])
    if (not isinstance(flags, list) or not 7 <= len(flags) <= 8
            or not all(isinstance(f, str) and f in allowed for f in flags)
            or len(set(flags)) != len(flags) or not set(BASE_FLAGS).issubset(flags)
            or sum(f.startswith("-O") for f in flags) != 1
            or sum("omit-frame-pointer" in f for f in flags) > 1):
        raise ToolchainError("unsupported, contradictory or unsafe profile flags")
    return list(flags)


def profile_flags(count: int = 4) -> list[list[str]]:
    if type(count) is not int or not 1 <= count <= MAX_PROFILES:
        raise ToolchainError("require one to eight sequential profiles")
    return [BASE_FLAGS + [opt] + frame for opt in ("-O2", "-Os", "-O1", "-O3")
            for frame in ([], ["-fomit-frame-pointer"])][:count]


def discover(roots: list[Path] | None = None) -> dict:
    """No PATH execution, recursive scan, download or unbounded directory listing."""
    if roots is None:
        configured = os.environ.get("NOVA3_HISTORICAL_TOOLCHAIN_ROOT")
        roots = ([Path(configured)] if configured else []) + list(DEFAULT_ROOTS)
    if not 1 <= len(roots) <= 16:
        raise ToolchainError("require one to sixteen bounded candidate roots")
    records, seen = [], set()
    for root in roots:
        root = root.expanduser().absolute()
        candidates = [root / "bin" / (TARGET + "-g++")]
        for prefix in (root, root / "toolchains" / (TARGET + "-4.4.3")):
            for host in ("linux-x86", "linux-x86_64"):
                candidates.append(prefix / "prebuilt" / host / "bin" / (TARGET + "-g++"))
        for candidate in candidates:
            if str(candidate) in seen:
                continue
            seen.add(str(candidate))
            item = dict(path=str(candidate), present=candidate.is_file())
            if item["present"]:
                try:
                    item.update(sha256=file_sha256(candidate), status="UNTRUSTED_NOT_EXECUTED")
                except (OSError, ToolchainError) as exc:
                    item.update(status="UNREADABLE", error_type=type(exc).__name__)
            records.append(item)
    return dict(schema=SCHEMA, roots=[str(p.expanduser().absolute()) for p in roots],
                candidates=records,
                status="CANDIDATE_FILES_UNVERIFIED" if any(x["present"] for x in records)
                else "MISSING_HISTORICAL_COMPILER",
                GCC_4_4_3_CANDIDATE_AVAILABLE=False,
                EXACT_HISTORICAL_TOOLCHAIN_VERIFIED=False)


def _pin(path: Path, expected: str) -> dict:
    if not isinstance(expected, str) or not SHA.fullmatch(expected):
        raise ToolchainError("explicit lowercase SHA-256 trust pin required")
    resolved = path.expanduser().resolve(strict=True)
    actual = file_sha256(resolved)
    if actual != expected:
        raise ToolchainError("executable SHA-256 changed or trust pin mismatched")
    if not os.access(resolved, os.X_OK):
        raise ToolchainError("pinned file is not executable")
    return dict(path=str(resolved), sha256=actual)


def _query(tool: dict, *options: str) -> dict:
    # Do not inherit compiler search overrides, preload hooks, shell startup or locale.
    _pin(Path(tool["path"]), tool["sha256"])
    result = subprocess.run([tool["path"], *options], stdin=subprocess.DEVNULL,
                            capture_output=True, check=False, timeout=QUERY_TIMEOUT,
                            env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"})
    if len(result.stdout) > MAX_OUTPUT or len(result.stderr) > MAX_OUTPUT:
        raise ToolchainError("metadata output exceeds limit")
    _pin(Path(tool["path"]), tool["sha256"])
    return dict(option=list(options), exit_code=result.returncode,
                stdout=result.stdout.decode("utf-8", errors="replace"),
                stdout_sha256=digest(result.stdout), stderr_sha256=digest(result.stderr))


def assess_metadata(queries: dict) -> str:
    """Reject contradictions instead of using a directory or banner as identity."""
    required = ("version", "gcc_version", "target", "specs")
    if not isinstance(queries, dict):
        return "INCOMPLETE_COMPILER_METADATA"
    if any(not isinstance(queries.get(k), dict) or queries[k].get("exit_code") != 0
           or not isinstance(queries[k].get("stdout"), str)
           for k in required):
        return "INCOMPLETE_COMPILER_METADATA"
    version = queries["version"]["stdout"]
    if ("clang" in version.lower() or not re.search(r"\b(?:gcc|g\+\+|GCC)\b", version)
            or not re.search(r"(?<![\d.])4\.4\.3(?![\d.])", version)
            or queries["gcc_version"]["stdout"].strip() != "4.4.3"):
        return "UNSUPPORTED_COMPILER_VERSION"
    if queries["target"]["stdout"].strip() != TARGET:
        return "CONTRADICTORY_TARGET_TRIPLE"
    if not queries["specs"]["stdout"].strip():
        return "INCOMPLETE_COMPILER_METADATA"
    return "GCC_4_4_3_CANDIDATE_AVAILABLE"


def fingerprint(compiler: Path, *, trusted_sha256: str | None = None,
                tools: dict[str, tuple[Path, str]] | None = None,
                arm_object: Path | None = None) -> dict:
    """Metadata only. All queried executables need individual owner-approved pins.

    ld/as reported by GCC are recorded, never executed implicitly. Optional
    tools: linker, assembler, readelf. Attributes describe only the given object.
    """
    report = dict(schema=SCHEMA, compiler=str(compiler.expanduser().absolute()),
                  compiler_executable_sha256=file_sha256(compiler),
                  status="UNTRUSTED_NOT_EXECUTED", queries={}, tools={},
                  trust="LOCAL_OWNER_PIN_ONLY; DISTRIBUTION_NOT_AUTHENTICATED",
                  provenance_confidence="HASH_ONLY_UNAUTHENTICATED",
                  distribution_checksum_authenticated=False,
                  compiler_selected_binutils_verified=False,
                  GCC_4_4_3_CANDIDATE_AVAILABLE=False, PROFILE_REPRODUCIBLE=False,
                  EXACT_ORIGINAL_COMPILER_PROVEN=False,
                  EXACT_HISTORICAL_TOOLCHAIN_VERIFIED=False,
                  SOURCE_EQUIVALENCE_VERIFIED=False, WHOLE_SO_HASH_MATCH=False,
                  LINKED_WINDOW_MATCH="NOT_TESTED", arm_attributes={"status": "NOT_BUILT"})
    tools = tools or {}
    if set(tools) - {"linker", "assembler", "readelf"}:
        raise ToolchainError("unknown metadata tool")
    if trusted_sha256 is None:
        if tools or arm_object is not None:
            raise ToolchainError("compiler trust pin required before metadata queries")
        return report
    compiler_pin = _pin(compiler, trusted_sha256)
    pinned_tools = {name: _pin(*entry) for name, entry in tools.items()}
    report["compiler"] = compiler_pin["path"]
    report["compiler_executable_sha256"] = compiler_pin["sha256"]
    report["provenance_confidence"] = "OWNER_PINNED_METADATA_ONLY"
    for name, option in QUERIES.items():
        report["queries"][name] = _query(compiler_pin, option)
    for name, tool in pinned_tools.items():
        report["tools"][name] = dict(tool, version=_query(tool, "--version"))
    report["status"] = assess_metadata(report["queries"])
    report["GCC_4_4_3_CANDIDATE_AVAILABLE"] = report["status"] == "GCC_4_4_3_CANDIDATE_AVAILABLE"
    if arm_object is not None:
        from .relocation_probe import ArmElf
        payload = _read_bytes(arm_object, 64 * 1024 * 1024)
        elf = ArmElf(payload)
        if elf.kind != 1:
            raise ToolchainError("attributes input must be source-built ARM ET_REL")
        if "readelf" not in pinned_tools:
            raise ToolchainError("attributes require an explicitly pinned readelf")
        query = _query(pinned_tools["readelf"], "-A", str(arm_object.resolve()))
        if file_sha256(arm_object) != digest(payload):
            raise ToolchainError("attributes input changed while querying")
        report["arm_attributes"] = dict(status="OBSERVED_CANDIDATE_ONLY" if query["exit_code"] == 0
            else "ATTRIBUTE_QUERY_FAILED", object_sha256=digest(payload), query=query)
    return report


def generate_profiles(report: dict | None, *, count: int = 4,
                      flags: list[str] | None = None) -> list[dict]:
    """Return matrix-compatible profiles only for queried, version-gated compilers.

    Missing compiler still yields standalone nonexecuting flag plans. No profile
    here asserts replay, package authentication or source/code equivalence.
    """
    variants = profile_flags(count)
    if flags is not None:
        if count != 1:
            raise ToolchainError("explicit flags require exactly one profile")
        variants = [validate_flags(flags)]
    available = bool(report and report.get("GCC_4_4_3_CANDIDATE_AVAILABLE") is True
                     and assess_metadata(report.get("queries", {})) == "GCC_4_4_3_CANDIDATE_AVAILABLE")
    profiles = []
    for variant in variants:
        item = dict(schema=MATRIX_PROFILE_SCHEMA if available else SCHEMA + "/plan",
                    flags=validate_flags(variant), flags_are_hypotheses=True,
                    compile_timeout_seconds=30, link_timeout_seconds=30,
                    sequential=True, PROFILE_REPRODUCIBLE=False,
                    EXACT_HISTORICAL_TOOLCHAIN_VERIFIED=False,
                    SOURCE_EQUIVALENCE_VERIFIED=False, WHOLE_SO_HASH_MATCH=False,
                    LINKED_WINDOW_MATCH="NOT_TESTED", match_to_original="UNVERIFIED",
                    status="PROFILE_RECORDED_NOT_REPLAYED" if available else "MISSING_HISTORICAL_COMPILER")
        if available:
            item.update(compiler=report["compiler"],
                        compiler_executable_sha256=report["compiler_executable_sha256"],
                        version_sha256=report["queries"]["version"]["stdout_sha256"],
                        version=report["queries"]["version"]["stdout"],
                        provenance=report)
        profiles.append(item)
    return profiles


def validate_profile(profile: dict, *, trusted_sha256: str,
                     trusted_tool_sha256: tuple[str, ...] = ()) -> dict:
    """Requery pinned tools before exporting a profile to the older matrix CLI.

    The matrix itself checks version output only; it must not be used as this
    historical provenance gate. Replay also needs source/header/sysroot hashes.
    """
    if not isinstance(profile, dict) or profile.get("schema") != MATRIX_PROFILE_SCHEMA:
        raise ToolchainError("malformed or nonexecuting compiler profile")
    validate_flags(profile.get("flags"))
    prior = profile.get("provenance")
    if (not isinstance(prior, dict) or profile.get("compiler") != prior.get("compiler")
            or profile.get("compiler_executable_sha256") != prior.get("compiler_executable_sha256")
            or not isinstance(profile.get("version_sha256"), str)):
        raise ToolchainError("profile provenance missing or contradictory")
    tools = {k: (Path(v["path"]), v["sha256"]) for k, v in prior.get("tools", {}).items()}
    if (trusted_sha256 != profile["compiler_executable_sha256"]
            or any(pin not in trusted_tool_sha256 for _, pin in tools.values())):
        raise ToolchainError("profile requires independent owner trust pins")
    current = fingerprint(Path(profile["compiler"]),
                          trusted_sha256=profile["compiler_executable_sha256"], tools=tools)
    if not current["GCC_4_4_3_CANDIDATE_AVAILABLE"]:
        raise ToolchainError("historical compiler metadata rejected")
    if (profile["version_sha256"] != current["queries"]["version"]["stdout_sha256"]
            or current["queries"] != prior.get("queries") or current["tools"] != prior.get("tools")):
        raise ToolchainError("compiler metadata/specs/tools changed since profile")
    return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("discover")
    scan.add_argument("--root", type=Path, action="append")
    inspect = sub.add_parser("fingerprint")
    inspect.add_argument("compiler", type=Path)
    inspect.add_argument("--trusted-sha256")
    for name in ("linker", "assembler", "readelf"):
        inspect.add_argument("--" + name, type=Path)
        inspect.add_argument("--" + name + "-sha256")
    inspect.add_argument("--arm-object", type=Path)
    plans = sub.add_parser("profiles")
    plans.add_argument("--fingerprint", type=Path)
    plans.add_argument("--count", type=int, default=4)
    plans.add_argument("--flag", action="append")
    verify = sub.add_parser("validate")
    verify.add_argument("profile", type=Path)
    verify.add_argument("--trusted-sha256", required=True)
    verify.add_argument("--trusted-tool-sha256", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            result = discover(args.root)
        elif args.command == "fingerprint":
            tools = {}
            for name in ("linker", "assembler", "readelf"):
                path, pin = getattr(args, name), getattr(args, name + "_sha256")
                if (path is None) != (pin is None):
                    raise ToolchainError("metadata tool requires both path and SHA-256")
                if path is not None:
                    tools[name] = (path, pin)
            result = fingerprint(args.compiler, trusted_sha256=args.trusted_sha256,
                                 tools=tools, arm_object=args.arm_object)
        elif args.command == "profiles":
            report = _load_json(args.fingerprint) if args.fingerprint else None
            if report is not None:
                # Serialized reports do not authorize execution: profiles are plans;
                # validate requires a separate explicit invocation before any build.
                if not isinstance(report, dict) or report.get("schema") != SCHEMA:
                    raise ToolchainError("malformed fingerprint report")
            result = generate_profiles(report, count=args.count, flags=args.flag)
        else:
            result = validate_profile(_load_json(args.profile), trusted_sha256=args.trusted_sha256,
                                      trusted_tool_sha256=tuple(args.trusted_tool_sha256))
        print(json.dumps(result, indent=2, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("historical toolchain: " + type(exc).__name__, file=sys.stderr)
        return 2
    return 0


def _load_json(path: Path) -> dict:
    return json.loads(_read_bytes(path, MAX_OUTPUT).decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
