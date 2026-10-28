"""Bounded, sequential ARM compiler-candidate experiments against original ELF windows.

Candidate flags and executables are explicit local profiles; GCC 4.4.3 in a
.comment section alone never establishes original compiler, flags or link order.
Source and reference bytes stay offline.  No synthetic linked-byte object is
accepted as independent decompilation proof.
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

from .object_compare import CompareError, compare_window
from .object_matching import SCHEMA as PROFILE_SCHEMA, compiler_profile


SCHEMA = "nova3.compiler-matrix/1"
MAX_SOURCE = 1024 * 1024
MAX_PROFILES = 8
_SAFE_FLAGS = re.compile(
    r"(?:-O[0123sgz]|-fPIC|-fpic|-fno-exceptions|-fno-rtti|"
    r"-ffunction-sections|-fdata-sections|-fno-stack-protector|"
    r"-fno-omit-frame-pointer|-fomit-frame-pointer|"
    r"-marm|-mthumb|-march=armv[5-8][A-Za-z0-9_.+-]*|"
    r"-mcpu=[A-Za-z0-9_.+-]+|-mtune=[A-Za-z0-9_.+-]+|"
    r"-mfloat-abi=(?:soft|softfp|hard)|-mfpu=[A-Za-z0-9_.+-]+|"
    r"-std=(?:c\+\+98|c\+\+03|c\+\+11|c\+\+14|c\+\+17)|"
    r"--target=armv7a-linux-androideabi[0-9]{2,3})\Z"
)


class MatrixError(ValueError):
    pass


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise MatrixError("input must be a bounded regular file")
        data = f.read(limit + 1)
        if len(data) != st.st_size:
            raise MatrixError("input changed while reading")
        return data


def _profile(path: Path) -> dict:
    payload = _read(path, 64 * 1024)
    try:
        profile = json.loads(payload)
    except json.JSONDecodeError:
        raise MatrixError("malformed compiler profile") from None
    if (not isinstance(profile, dict) or profile.get("schema") != PROFILE_SCHEMA
            or not isinstance(profile.get("compiler"), str)
            or not isinstance(profile.get("flags"), list)
            or not all(isinstance(v, str) and _SAFE_FLAGS.fullmatch(v)
                       for v in profile["flags"])):
        raise MatrixError("unsupported or unsafe compiler profile flags")
    if not isinstance(profile.get("version_sha256"), str) or len(profile["version_sha256"]) != 64:
        raise MatrixError("compiler version fingerprint missing")
    current = compiler_profile(Path(profile["compiler"]), profile["flags"])
    if current["version_sha256"] != profile["version_sha256"]:
        raise MatrixError("compiler changed since profile fingerprint")
    return dict(profile=profile, profile_sha256=_hash(payload), current=current)


def run_matrix(original: bytes, source: Path, profiles: list[Path], out: Path,
               *, address: int, symbol: str, compile_timeout: int = 120) -> dict:
    if not 1 <= len(profiles) <= MAX_PROFILES:
        raise MatrixError("require one to eight candidate compiler profiles")
    if compile_timeout < 1 or compile_timeout > 600:
        raise MatrixError("compiler timeout must be between 1 and 600 seconds")
    source_bytes = _read(source, MAX_SOURCE)
    if out.exists() and any(out.iterdir()):
        raise MatrixError("matrix output directory must be new or empty")
    out.mkdir(parents=True, exist_ok=True)
    reference_sha = _hash(original)
    experiments = []
    for number, path in enumerate(profiles, 1):
        candidate = dict(number=number, profile_name=path.name, status="NOT_RUN",
                         source_matching_verified=False)
        experiments.append(candidate)
        try:
            item = _profile(path)
            candidate.update(profile_sha256=item["profile_sha256"],
                             compiler_version_sha256=item["profile"]["version_sha256"],
                             flags=item["profile"]["flags"])
            obj = out / ("candidate_%02d.o" % number)
            cmd = [item["current"]["compiler"], *item["current"]["flags"],
                   "-c", "-x", "c++", str(source), "-o", str(obj)]
            result = subprocess.run(cmd, capture_output=True, check=False,
                                    timeout=compile_timeout)
            candidate["compile_exit_code"] = result.returncode
            if result.returncode != 0:
                candidate["status"] = "COMPILER_ERROR"
                continue
            object_bytes = _read(obj, 64 * 1024 * 1024)
            comparison = compare_window(original, object_bytes, address=address,
                                        symbol=symbol,
                                        expected_reference_sha256=reference_sha)
            candidate.update(status=comparison["status"], object_file=obj.name,
                             object_sha256=_hash(object_bytes),
                             comparison=comparison)
        except subprocess.TimeoutExpired:
            candidate["status"] = "COMPILER_TIMEOUT"
        except (OSError, MatrixError, CompareError, ValueError,
                subprocess.CalledProcessError) as exc:
            # Error class only: local toolchain paths and compile diagnostics
            # must not accidentally reach a GitHub PR or issue comment.
            candidate.update(status="BLOCKED", error_type=type(exc).__name__)
    report = dict(schema=SCHEMA, reference_sha256=reference_sha,
                  source_sha256=_hash(source_bytes), source_symbol=symbol,
                  reference_address=address, profiles_run=len(profiles),
                  experiments=experiments, original_compiler_verified=False,
                  source_matching_verified=False,
                  whole_shared_library_sha256_matched=False,
                  caveat="Code-window checks are evidence, not recovered original .o/relocations/linker inputs or full source equivalence.")
    (out / "matrix.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--address", type=lambda s: int(s, 0), required=True)
    p.add_argument("--profile", type=Path, action="append", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--compile-timeout", type=int, default=120)
    args = p.parse_args(argv)
    try:
        cache = (Path(__file__).resolve().parents[1] / ".cache").resolve()
        out = args.out.expanduser().resolve()
        if not out.is_relative_to(cache) or out == cache:
            raise MatrixError("output must be under private repository .cache")
        report = run_matrix(_read(args.reference, 64 * 1024 * 1024), args.source,
                            args.profile, out, address=args.address, symbol=args.symbol,
                            compile_timeout=args.compile_timeout)
        print("%d compiler candidates attempted; ORIGINAL SOURCE MATCH UNVERIFIED" %
              report["profiles_run"])
    except (MatrixError, OSError, ValueError) as exc:
        print("compiler matrix: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
