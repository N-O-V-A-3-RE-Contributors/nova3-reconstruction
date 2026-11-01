#!/usr/bin/env python3
"""nova3 — one entry point for the reconstruction workbench.

The repository has the pieces (index, Ghidra exporter, compiler pilots, CTest,
oracle suite) but a newcomer had to know which of a dozen scripts to run, in
which order, from which interpreter, and where the results land. This module is
the single front door:

    ./nova3                       # what this machine can do right now
    ./nova3 doctor                # PASS/WARN/FAIL per component, with the fix
    ./nova3 index                 # build analysis_db/index.sqlite
    ./nova3 decompile             # bounded, resumable Ghidra batch (default 8)
    ./nova3 decompile --address 0x9dad14
    ./nova3 status                # indexed / exported / remaining
    ./nova3 show 0x9dad14         # one function, from index + export
    ./nova3 search detect         # symbol search in the index
    ./nova3 build | test | verify # host build, CTest, full source-only audit

Everything here is local and read-only with respect to the reference inputs.
Exports land under the gitignored `generated/ghidra/exports/<sha256>/` and are
private: they contain decompiled pseudocode and must never be committed or
uploaded.

Exit codes: 0 ok, 1 failed check / failed command, 2 missing prerequisite,
3 maps to the existing push helper's "no credential source" contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREMIUM = "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
PREMIUM_SHA256 = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
FREEMIUM = "analysis/N.O.V.A.3_freemium/lib/armeabi-v7a/libGloftNAHM.so"
DB_REL = "analysis_db/index.sqlite"
EXPORTS_REL = "generated/ghidra/exports"
PY = sys.executable


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd=None, timeout=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or ROOT), text=True,
                          capture_output=True, timeout=timeout)


def db_path(root: Path) -> Path:
    return root / DB_REL


def rows_for(root: Path, digest: str) -> int:
    path = db_path(root)
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(path) as db:
            return db.execute("SELECT COUNT(*) FROM functions WHERE binary_hash=?",
                              (digest,)).fetchone()[0]
    except sqlite3.DatabaseError:
        return 0


def exports_for(root: Path, digest: str) -> int:
    return len(list((root / EXPORTS_REL / digest).glob("*.json")))


def human(n: int) -> str:
    return "{:,}".format(n)


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def _check(name, state, detail, fix=""):
    return {"check": name, "state": state, "detail": detail, "fix": fix}


def checks(root: Path) -> list[dict]:
    out: list[dict] = []

    # 1. reference inputs (the only things that cannot be reproduced from source)
    premium = root / PREMIUM
    if not premium.is_file():
        out.append(_check("reference: Premium engine ELF", "FAIL",
                          "%s is missing" % PREMIUM,
                          "copy the extracted APK libraries into analysis/ "
                          "(bash tools/setup/link_private_references.sh)"))
    else:
        digest = sha256_of(premium)
        if digest == PREMIUM_SHA256:
            out.append(_check("reference: Premium engine ELF", "PASS",
                              "%s  %s" % (digest[:16] + "...", premium.name)))
        else:
            out.append(_check("reference: Premium engine ELF", "FAIL",
                              "sha256 %s != expected %s" % (digest[:16] + "...",
                                                            PREMIUM_SHA256[:16] + "..."),
                              "every export is keyed on this hash; restore the "
                              "original file, do not re-extract a different build"))
    free = root / FREEMIUM
    out.append(_check("reference: Freemium engine ELF",
                      "PASS" if free.is_file() else "WARN",
                      "present" if free.is_file() else "absent (comparative target only)",
                      "" if free.is_file() else
                      "optional: only needed for edition-difference comparisons"))

    # 2. python environment
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    out.append(_check("python: project venv", "PASS" if in_venv else "WARN",
                      sys.executable,
                      "run through ./nova3 (it uses .venv/bin/python) or "
                      "bash tools/setup/bootstrap.sh"))
    missing = []
    for mod in ("elftools", "capstone"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    out.append(_check("python: pyelftools + capstone",
                      "PASS" if not missing else "FAIL",
                      "importable" if not missing else "missing: " + ", ".join(missing),
                      ".venv/bin/pip install -r tools/setup/requirements.txt"))

    # 3. Ghidra + JDK (only needed for decompile)
    sys.path.insert(0, str(root / "tools/pipeline"))
    try:
        from ghidra_env import ghidra_version, resolve_ghidra_install, resolve_java_home
        ghidra = resolve_ghidra_install()
        java = resolve_java_home()
        version = ghidra_version(ghidra)
    except Exception as exc:  # pragma: no cover - defensive
        ghidra = java = version = None
        out.append(_check("ghidra: resolver", "FAIL", repr(exc)))
    out.append(_check("ghidra: installation", "PASS" if ghidra else "FAIL",
                      "%s%s" % (ghidra, " (v%s)" % version if version else "")
                      if ghidra else "not found",
                      "install Ghidra 11/12 and set NOVA3_GHIDRA_HOME, or place it "
                      "under /opt/ghidra"))
    out.append(_check("ghidra: JDK 17+", "PASS" if java else "FAIL",
                      str(java) if java else "no JDK found",
                      "install jdk-openjdk and set JAVA_HOME"))
    try:
        import pyghidra  # noqa: F401
        have_pyghidra = True
    except Exception:
        have_pyghidra = False
    out.append(_check("ghidra: pyghidra", "PASS" if have_pyghidra else "FAIL",
                      "importable" if have_pyghidra else "missing",
                      ".venv/bin/pip install pyghidra==3.2.0"))

    # 4. Ghidra project location (must not contain a '.'-prefixed element)
    project = Path(os.environ.get("NOVA3_GHIDRA_PROJECT_DIR")
                   or Path.home() / "nova3-analysis" / "ghidra")
    bad = [p for p in project.parts if p.startswith(".") and p not in (".", "..")]
    if bad:
        out.append(_check("ghidra: project directory", "FAIL", str(project),
                          "Ghidra rejects '.'-prefixed path elements; use e.g. "
                          "~/nova3-analysis/ghidra via NOVA3_GHIDRA_PROJECT_DIR"))
    else:
        out.append(_check("ghidra: project directory", "PASS", str(project)))

    # 5. disk space for exports (each export ~ few KiB, but the project is GB-scale)
    try:
        free_gib = shutil.disk_usage(project if project.exists() else root).free / 2**30
        out.append(_check("disk: free space", "PASS" if free_gib >= 2 else "WARN",
                          "%.1f GiB available" % free_gib,
                          "free space in the pacman cache / journals before a "
                          "long Ghidra run"))
    except OSError as exc:
        out.append(_check("disk: free space", "WARN", repr(exc)))

    # 6. index + exports coverage
    if premium.is_file():
        digest = sha256_of(premium)
        indexed, exported = rows_for(root, digest), exports_for(root, digest)
        out.append(_check("index: function rows",
                          "PASS" if indexed else "WARN",
                          "%s indexed, %s exported" % (human(indexed), human(exported)),
                          "./nova3 index" if not indexed else ""))

    # 7. host build tools
    for tool, fix in (("cmake", "pacman -S cmake"), ("ninja", "pacman -S ninja")):
        out.append(_check("build: %s" % tool, "PASS" if shutil.which(tool) else "FAIL",
                          shutil.which(tool) or "missing", fix))

    # 8. optional analysis tools.  Each WARN must say how to fix it: a dead end
    # is not a report (and CI enforces that every non-PASS check has a fix).
    for tool, why, fix in (
            ("jadx", "Android/Java recovery", "install jadx 1.5+ (pacman -S jadx / brew install jadx)"),
            ("apktool", "resource decode", "install apktool 2.9+ (pacman -S apktool)"),
            ("7z", "OBB/archive inspection", "install p7zip (pacman -S p7zip)")):
        out.append(_check("optional: %s" % tool, "PASS" if shutil.which(tool) else "WARN",
                          (shutil.which(tool) or "missing") + " (%s)" % why, fix))

    # 9. historical compiler for the matching track
    ndk = Path(os.environ.get("NOVA3_R8E_NDK") or "~/.local/opt/android-ndk-r8e").expanduser()
    project_ndk = root / ".cache/toolchains/android-ndk-r8e"
    compiler = (project_ndk / "toolchains/arm-linux-androideabi-4.4.3/prebuilt/"
                "linux-x86_64/bin/arm-linux-androideabi-g++")
    candidates = [p for p in (ndk, project_ndk) if p.is_dir()]
    if project_ndk.is_dir() and not compiler.is_file():
        candidates.remove(project_ndk)
    present = candidates[0] if candidates else None
    out.append(_check("optional: NDK r8e (GCC 4.4.3)", "PASS" if present else "WARN",
                      str(present or ndk), "run 'bash tools/setup/fetch_gcc443.sh' "
                      "(verified download into .cache/toolchains); only needed for "
                      "compiler-match work, see docs/toolchain.md"))
    return out


def cmd_doctor(root: Path, as_json: bool) -> int:
    results = checks(root)
    if as_json:
        print(json.dumps({"root": str(root), "checks": results}, indent=2))
    else:
        width = max(len(c["check"]) for c in results)
        for c in results:
            print("%-4s  %-*s  %s" % (c["state"], width, c["check"], c["detail"]))
            if c["state"] != "PASS" and c["fix"]:
                print("      %-*s  -> %s" % (width, "", c["fix"]))
        counts = {s: sum(1 for c in results if c["state"] == s)
                  for s in ("PASS", "WARN", "FAIL")}
        print("\n%d pass, %d warn, %d fail" % (counts["PASS"], counts["WARN"], counts["FAIL"]))
        if counts["FAIL"]:
            print("Fix the FAIL lines above; WARN lines only disable optional paths.")
    return 1 if any(c["state"] == "FAIL" for c in results) else 0


# --------------------------------------------------------------------------- #
# index / decompile / status
# --------------------------------------------------------------------------- #
def cmd_index(root: Path) -> int:
    print("indexing reference binaries (tools/index/inventory.py, ~45 s)...")
    proc = run([PY, str(root / "tools/index/inventory.py")], timeout=1800)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:])
        sys.stderr.write(proc.stderr[-3000:])
        return proc.returncode
    path = db_path(root)
    if not path.exists():
        print("index reported success but %s is missing" % DB_REL, file=sys.stderr)
        return 1
    with sqlite3.connect(path) as db:
        binaries = db.execute("SELECT hash, path FROM binaries").fetchall()
    print("index built: %s" % DB_REL)
    for digest, name in binaries:
        print("  %-24s %9s functions  %s" % (Path(name).name, human(rows_for(root, digest)),
                                             digest[:12]))
    print("full ELF metadata: research/elf/inventory.json\nnext: ./nova3 decompile")
    return 0


def cmd_decompile(root: Path, args) -> int:
    driver = root / "tools/pipeline/auto_decompile.py"
    if args.address:
        cmd = [PY, str(root / "tools/ghidra/export_batch.py"),
               "--binary", PREMIUM, "--addresses", ",".join(args.address),
               "--limit", str(len(args.address))]
        if args.retry:
            cmd.append("--retry")
        cmd += ["--project-dir", args.project_dir]
        print("exporting %d function(s) by address; output: %s/<sha>/"
              % (len(args.address), EXPORTS_REL))
        proc = run(cmd, timeout=args.timeout)
        sys.stdout.write(proc.stdout[-6000:])
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr[-3000:])
            return proc.returncode
        return 0

    cmd = [PY, str(driver), "--binary", PREMIUM,
           "--expected-sha256", PREMIUM_SHA256,
           "--batch-size", str(args.batch_size),
           "--timeout", str(args.timeout),
           "--project-dir", args.project_dir]
    if args.all:
        print("running until no further progress. At the measured ~11 s/function "
              "warm rate the full 39,123-function ELF is ~100 h; Ctrl-C is safe "
              "and resumes from the last checkpoint.")
    else:
        cmd += ["--max-functions", str(args.max_functions)]
        print("bounded run: %d function(s), batch size %d. Re-run ./nova3 decompile "
              "to continue." % (args.max_functions, args.batch_size))
    proc = run(cmd, timeout=None if args.all else args.timeout * (
        args.max_functions // max(args.batch_size, 1) + 2))
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-3000:])
        if proc.returncode == 4:
            print("another decompilation run owns the lock; this one did nothing.")
        return proc.returncode
    print("\nexports: %s/<sha256>/%s\nstate:   .cache/pipeline/auto_decompile_state.json"
          % (EXPORTS_REL, "{addr:08x}.json"))
    print("inspect one: ./nova3 show 0x000d2f10")
    return 0


def cmd_status(root: Path, as_json: bool) -> int:
    path = db_path(root)
    table = []
    if path.exists():
        with sqlite3.connect(path) as db:
            binaries = db.execute("SELECT hash, path FROM binaries").fetchall()
            binaries.sort(key=lambda r: 0 if "premium" in r[1] else 1)
            for digest, name in binaries:
                indexed = db.execute("SELECT COUNT(*) FROM functions WHERE binary_hash=?",
                                     (digest,)).fetchone()[0]
                exported = exports_for(root, digest)
                table.append({"binary": name, "sha256": digest, "indexed": indexed,
                              "exported": exported,
                              "remaining": max(indexed - exported, 0)})
    state_file = root / ".cache/pipeline/auto_decompile_state.json"
    state = None
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except (OSError, ValueError):
            state = None
    if as_json:
        print(json.dumps({"binaries": table, "last_run": state}, indent=2))
        return 0
    if not table:
        print("no index yet: run ./nova3 index")
        return 0
    print("%-58s %10s %10s %10s" % ("binary", "indexed", "exported", "remaining"))
    for row in table:
        print("%-58s %10s %10s %10s" % (row["binary"].split("/")[-1],
                                        human(row["indexed"]), human(row["exported"]),
                                        human(row["remaining"])))
    if state:
        last = (state.get("batches") or [{}])[-1]
        print("\nlast run %s: %+d export(s), status=%s, updated %s"
              % (state.get("sha256", "")[:12], last.get("new", 0),
                 last.get("status", "?"), state.get("updated_at", "?")))
    return 0


# --------------------------------------------------------------------------- #
# show / search
# --------------------------------------------------------------------------- #
def _resolve_symbol(root: Path, needle: str):
    path = db_path(root)
    if not path.exists():
        return None
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        try:
            value = int(needle, 0)
        except ValueError:
            row = db.execute(
                "SELECT * FROM functions WHERE name=? "
                "ORDER BY (binary_hash=?) DESC, address LIMIT 1",
                (needle, PREMIUM_SHA256)).fetchone()
        else:
            row = db.execute(
                "SELECT * FROM functions WHERE binary_hash=? AND address=? LIMIT 1",
                (PREMIUM_SHA256, value)).fetchone()
        return dict(row) if row else None


def _names_for(root: Path, digest: str, hex_targets: list, image_base: int = 0) -> dict:
    """Map export call targets to symbol names from the index.

    Export targets are Ghidra addresses: `address + image_base` (the importer
    rebases the ELF at 0x10000). Subtract it before querying, or every lookup
    misses by exactly the image base — the bug this docstring exists to stop
    reintroducing.
    """
    path = db_path(root)
    if not path.exists() or not hex_targets:
        return {}
    wanted = []
    for value in hex_targets:
        try:
            wanted.append(int(str(value), 16) - image_base)
        except (TypeError, ValueError):
            continue
    if not wanted:
        return {}
    marks = ",".join("?" * len(wanted))
    try:
        with sqlite3.connect(path) as db:
            rows = db.execute(
                "SELECT address, name FROM functions WHERE binary_hash=? "
                "AND address IN (%s)" % marks, (digest, *wanted)).fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {"%08x" % (addr + image_base): name for addr, name in rows}


def cmd_show(root: Path, needle: str, c_lines: int) -> int:
    row = _resolve_symbol(root, needle)
    if row is None:
        print("not in the index: %s\n  try ./nova3 search %s   (index first: ./nova3 index)"
              % (needle, needle))
        return 1
    digest, addr = row["binary_hash"], row["address"]
    export = root / EXPORTS_REL / digest / ("%08x.json" % addr)
    mode = "Thumb" if row.get("thumb") else "A32"
    print("0x%08x  %s" % (addr, row["name"]))
    print("  binary    %s" % row["binary_hash"][:16])
    print("  size      %s B   mode %s   state %s" % (human(row["size"]), mode, row["state"]))
    if row.get("signature"):
        print("  signature %s" % row["signature"])
    print("  export    %s" % (export.relative_to(root) if export.exists()
                              else "not decompiled yet -> ./nova3 decompile --address 0x%08x" % addr))
    if not export.exists():
        return 0
    data = json.loads(export.read_text())
    calls = data.get("calls") or []
    print("  ghidra    %s, %d instruction(s), %d basic block(s), %d call(s)"
          % (data.get("ghidra_version", "?"), len(data.get("instructions") or []),
             len(data.get("basic_blocks") or []), len(calls)))
    if calls:
        names = _names_for(root, digest, [c.get("to") for c in calls if c.get("to")],
                           int(data.get("ghidra_image_base") or 0))
        for call in calls[:16]:
            target = call.get("to") or "?"
            label = names.get(str(target), "")
            print("    -> 0x%s  %s%s" % (target, label, ("  [%s]" % call["type"])
                                         if call.get("type") else ""))
    csrc = (data.get("c") or "").strip()
    if csrc and c_lines > 0:
        lines = csrc.splitlines()
        print("\n--- decompiled C (private local evidence, DECOMPILED not verified) ---")
        print("\n".join(lines[:c_lines]))
        if len(lines) > c_lines:
            print("... %d more line(s); --c-lines 0 hides the body" % (len(lines) - c_lines))
    elif csrc:
        print("\n(body hidden by --c-lines 0; %d line(s) available)" % len(csrc.splitlines()))
    if data.get("error"):
        print("\ndecompiler error: %s" % data["error"])
    return 0


def cmd_search(root: Path, pattern: str, limit: int) -> int:
    path = db_path(root)
    if not path.exists():
        print("no index yet: run ./nova3 index")
        return 1
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT address, size, thumb, name, state FROM functions "
            "WHERE binary_hash=? AND name LIKE ? ORDER BY address LIMIT ?",
            (PREMIUM_SHA256, "%" + pattern + "%", limit)).fetchall()
        total = db.execute(
            "SELECT COUNT(*) FROM functions WHERE binary_hash=? AND name LIKE ?",
            (PREMIUM_SHA256, "%" + pattern + "%")).fetchone()[0]
    if not rows:
        print("no symbol matching %r in the Premium engine" % pattern)
        return 1
    for addr, size, thumb, name, state in rows:
        print("0x%08x  %6d B  %-5s %-12s %s"
              % (addr, size, "Thumb" if thumb else "A32", state, name))
    print("\n%d match(es); showing %d. ./nova3 show 0x%08x"
          % (total, len(rows), rows[0][0]))
    return 0


# --------------------------------------------------------------------------- #
# build / test / verify / push
# --------------------------------------------------------------------------- #
def cmd_build(root: Path) -> int:
    for cmd in (["cmake", "-S", ".", "-B", "build/host", "-G", "Ninja",
                 "-DCMAKE_BUILD_TYPE=Debug"],
                ["cmake", "--build", "build/host"]):
        proc = run(cmd)
        sys.stdout.write(proc.stdout[-3000:])
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr[-3000:])
            return proc.returncode
    print("build OK: build/host")
    return 0


def cmd_test(root: Path) -> int:
    if not (root / "build/host/CMakeCache.txt").exists():
        code = cmd_build(root)
        if code:
            return code
    proc = run(["ctest", "--test-dir", "build/host", "--output-on-failure"])
    sys.stdout.write(proc.stdout[-6000:])
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-2000:])
    return proc.returncode


def cmd_verify(root: Path) -> int:
    proc = run(["bash", str(root / "tools/check_all.sh")], timeout=3600)
    sys.stdout.write(proc.stdout[-8000:])
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        return proc.returncode
    print("verify OK: build + CTest + source-only audit")
    return 0


def cmd_push(root: Path) -> int:
    proc = run(["bash", str(root / "tools/setup/push_branch.sh")])
    sys.stdout.write(proc.stdout[-4000:])
    sys.stderr.write(proc.stderr[-2000:])
    return proc.returncode


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
BANNER = """nova3 workbench — decompile the Premium engine, inspect it, verify the tree.

  ./nova3 doctor                 environment check (PASS/WARN/FAIL + the fix)
  ./nova3 index                  build analysis_db/index.sqlite (~45 s, once)
  ./nova3 decompile              run a bounded Ghidra batch (default 8 functions)
  ./nova3 decompile --all        run until no progress (~100 h for the full ELF)
  ./nova3 decompile --address 0x9dad14
  ./nova3 status                 indexed / exported / remaining per binary
  ./nova3 show 0x9dad14          one function: index facts + decompiled body
  ./nova3 search DetectNeon      symbol search (the symbol table is rich)
  ./nova3 build | test | verify  host build, CTest, full source-only audit
  ./nova3 push                   publish the branch (needs GitHub credentials)

Reference inputs stay read-only under analysis/, originals/, Archive/.
Exports under generated/ghidra/ are private and never committed.
Docs: docs/DECOMPILE.md · docs/progress/AUTO_DECOMPILATION.md
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nova3", add_help=True,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=BANNER)
    ap.add_argument("--root", default=str(ROOT), help="repository root (default: this checkout)")
    sub = ap.add_subparsers(dest="command")

    p = sub.add_parser("doctor", help="environment health check")
    p.add_argument("--json", action="store_true")

    sub.add_parser("index", help="build the function index")

    p = sub.add_parser("decompile", help="bounded, resumable Ghidra decompilation")
    p.add_argument("--max-functions", type=int, default=8)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--all", action="store_true", help="run until no progress")
    p.add_argument("--address", action="append", default=None,
                   help="export one address (repeatable)")
    p.add_argument("--retry", action="store_true", help="re-export existing addresses")
    p.add_argument("--timeout", type=int, default=900,
                   help="seconds per batch or per address export")
    p.add_argument("--project-dir",
                   default=os.environ.get("NOVA3_GHIDRA_PROJECT_DIR")
                   or str(Path.home() / "nova3-analysis" / "ghidra"))

    p = sub.add_parser("status", help="coverage summary")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("show", help="one function from index + export")
    p.add_argument("target")
    p.add_argument("--c-lines", type=int, default=40, help="0 hides the body")

    p = sub.add_parser("search", help="symbol search")
    p.add_argument("pattern")
    p.add_argument("--limit", type=int, default=20)

    for name in ("build", "test", "verify", "push"):
        sub.add_parser(name)

    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command is None:
        print(BANNER)
        return 0
    handlers = {
        "doctor": lambda: cmd_doctor(root, args.json),
        "index": lambda: cmd_index(root),
        "decompile": lambda: cmd_decompile(root, args),
        "status": lambda: cmd_status(root, args.json),
        "show": lambda: cmd_show(root, args.target, args.c_lines),
        "search": lambda: cmd_search(root, args.pattern, args.limit),
        "build": lambda: cmd_build(root),
        "test": lambda: cmd_test(root),
        "verify": lambda: cmd_verify(root),
        "push": lambda: cmd_push(root),
    }
    return handlers[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
