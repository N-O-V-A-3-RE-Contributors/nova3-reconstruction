"""Hermetic tests for the `./nova3` workbench entry point.

No private reference ELF, no Ghidra, no network: every case builds a throwaway
repository root containing just the files the command under test reads.

The workbench is the documented front door (docs/DECOMPILE.md), so its contract
is tested the way a user exercises it — as a subprocess with `--root`.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "tools/workbench.py"
PREMIUM_SHA = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WORKBENCH), "--root", str(root), *args],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=120)


def make_index(root: Path) -> None:
    """Minimal index with the schema tools/index/inventory.py creates."""
    (root / "analysis_db").mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "analysis_db/index.sqlite")
    db.executescript(
        """
        CREATE TABLE binaries(hash TEXT PRIMARY KEY,path TEXT,architecture TEXT,
                              size INTEGER,metadata TEXT);
        CREATE TABLE functions(id TEXT PRIMARY KEY,binary_hash TEXT,address INTEGER,
                               raw_value INTEGER,thumb INTEGER,size INTEGER,name TEXT,
                               state TEXT,provenance TEXT,confidence TEXT,
                               file_offset INTEGER,tool TEXT,signature TEXT,
                               unresolved TEXT);
        CREATE TABLE types(id TEXT PRIMARY KEY,binary_hash TEXT,die_offset INTEGER,
                           tag TEXT,name TEXT,byte_size INTEGER,provenance TEXT,
                           attributes TEXT);
        CREATE TABLE edges(source_id TEXT,target_address INTEGER,kind TEXT,
                           evidence TEXT,PRIMARY KEY(source_id,target_address,kind));
        CREATE TABLE analyses(id TEXT PRIMARY KEY,function_id TEXT,tool TEXT,
                              state TEXT,evidence_path TEXT,error TEXT);
        """
    )
    db.execute("INSERT INTO binaries VALUES(?,?,?,?,?)",
               (PREMIUM_SHA, "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so",
                "ARM", 18696442, "{}"))
    db.executemany(
        "INSERT INTO functions(id,binary_hash,address,raw_value,thumb,size,name,state)"
        " VALUES(?,?,?,?,?,?,?,?)",
        [(PREMIUM_SHA + ":9dad14", PREMIUM_SHA, 0x9DAD14, 0x9DAD14, 0, 32,
          "_ZN3vox29DriverCallbackSourceInterface10DetectNeonEv", "DECOMPILED"),
         (PREMIUM_SHA + ":e2f6c", PREMIUM_SHA, 0xD2F6C, 0xD2F6C, 0, 44,
          "android_getCpuFamily", "IMPLEMENTED")],
    )
    db.commit()
    db.close()


def make_export(root: Path, address: int = 0x9DAD14) -> Path:
    out = root / "generated/ghidra/exports" / PREMIUM_SHA
    out.mkdir(parents=True, exist_ok=True)
    path = out / ("%08x.json" % address)
    path.write_text(json.dumps({
        "ghidra_version": "12.1.2",
        "ghidra_image_base": 0x10000,
        "instructions": [{"address": "009d5d14"}],
        "basic_blocks": [{"start": "009d5d14", "end": "009d5d33"}],
        # Ghidra addresses: ELF VA 0xd2f6c appears as 0xe2f6c (base 0x10000).
        "calls": [{"from": "009d5d18", "to": "000e2f6c", "type": "UNCONDITIONAL_CALL"}],
        "c": "uint DetectNeon(void)\n{\n  return 0;\n}\n",
        "error": "",
    }, indent=2) + "\n")
    return path


def make_root(tmp_path: Path) -> Path:
    """A root the workbench can run against: only ghidra_env is needed."""
    (tmp_path / "tools/pipeline").mkdir(parents=True)
    shutil.copy2(ROOT / "tools/pipeline/ghidra_env.py",
                 tmp_path / "tools/pipeline/ghidra_env.py")
    return tmp_path


def test_bare_invocation_lists_every_command(tmp_path):
    proc = run(make_root(tmp_path))
    assert proc.returncode == 0, proc.stderr
    for command in ("doctor", "index", "decompile", "status", "show", "search",
                    "build", "test", "verify"):
        assert command in proc.stdout, command
    assert "never committed" in proc.stdout


def test_doctor_json_is_machine_readable_and_fails_without_references(tmp_path):
    proc = run(make_root(tmp_path), "doctor", "--json")
    assert proc.returncode == 1, "missing reference inputs must be a FAIL, not a pass"
    report = json.loads(proc.stdout)
    checks = {c["check"]: c for c in report["checks"]}
    assert checks["reference: Premium engine ELF"]["state"] == "FAIL"
    assert "analysis/" in checks["reference: Premium engine ELF"]["fix"]
    assert checks["reference: Freemium engine ELF"]["state"] == "WARN"
    # Every non-passing check must say how to fix it; a dead end is not a report.
    for check in report["checks"]:
        if check["state"] != "PASS":
            assert check["fix"], check


def test_doctor_passes_when_the_reference_hash_matches(tmp_path):
    root = make_root(tmp_path)
    elf = root / "analysis/N.O.V.A.3_premium/lib/armeabi-v7a/libNOVA3_neon.so"
    elf.parent.mkdir(parents=True)
    elf.write_bytes(b"not a real ELF")
    free = root / "analysis/N.O.V.A.3_freemium/lib/armeabi-v7a/libGloftNAHM.so"
    free.parent.mkdir(parents=True)
    free.write_bytes(b"x")
    proc = run(root, "doctor", "--json")
    # A fabricated ELF must be rejected by hash, not accepted because it exists.
    checks = {c["check"]: c for c in json.loads(proc.stdout)["checks"]}
    assert checks["reference: Premium engine ELF"]["state"] == "FAIL"
    assert "sha256" in checks["reference: Premium engine ELF"]["detail"]
    assert checks["reference: Freemium engine ELF"]["state"] == "PASS"


def test_status_counts_exports_per_binary(tmp_path):
    root = make_root(tmp_path)
    make_index(root)
    make_export(root)
    proc = run(root, "status", "--json")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    premium = [b for b in report["binaries"] if b["sha256"] == PREMIUM_SHA][0]
    assert premium["indexed"] == 2
    assert premium["exported"] == 1
    assert premium["remaining"] == 1


def test_status_without_index_tells_the_user_what_to_run(tmp_path):
    proc = run(make_root(tmp_path), "status")
    assert proc.returncode == 0
    assert "./nova3 index" in proc.stdout


def test_show_resolves_call_targets_through_the_image_base(tmp_path):
    root = make_root(tmp_path)
    make_index(root)
    make_export(root)
    proc = run(root, "show", "0x9dad14", "--c-lines", "0")
    assert proc.returncode == 0, proc.stderr
    # Ghidra target 0xe2f6c is ELF VA 0xd2f6c; the name must resolve.
    assert "android_getCpuFamily" in proc.stdout
    assert "body hidden" in proc.stdout
    assert "DetectNeon" in proc.stdout


def test_show_without_an_export_points_at_the_export_command(tmp_path):
    root = make_root(tmp_path)
    make_index(root)
    proc = run(root, "show", "android_getCpuFamily")
    assert proc.returncode == 0, proc.stderr
    assert "./nova3 decompile --address 0x000d2f6c" in proc.stdout


def test_show_unknown_symbol_is_a_clean_failure(tmp_path):
    root = make_root(tmp_path)
    make_index(root)
    proc = run(root, "show", "0xdeadbeef")
    assert proc.returncode == 1
    assert "not in the index" in proc.stdout


def test_search_reports_matches_and_a_total(tmp_path):
    root = make_root(tmp_path)
    make_index(root)
    proc = run(root, "search", "DetectNeon")
    assert proc.returncode == 0, proc.stderr
    assert "0x009dad14" in proc.stdout
    assert "1 match(es)" in proc.stdout
    miss = run(root, "search", "NoSuchSymbolAnywhere")
    assert miss.returncode == 1
    assert "no symbol matching" in miss.stdout


def test_ghidra_env_requires_the_headless_launcher(tmp_path):
    sys.path.insert(0, str(ROOT / "tools/pipeline"))
    try:
        import ghidra_env
    finally:
        sys.path.pop(0)
    empty = tmp_path / "ghidra_99.9"
    empty.mkdir()
    assert ghidra_env.resolve_ghidra_install(empty) is None
    real = tmp_path / "ghidra_12.1.2_PUBLIC"
    (real / "support").mkdir(parents=True)
    (real / "support/analyzeHeadless").write_text("#!/bin/sh\n")
    assert ghidra_env.resolve_ghidra_install(real) == real
    (real / "Ghidra").mkdir()
    (real / "Ghidra/application.properties").write_text(
        "application.name=Ghidra\napplication.version=12.1.2\n")
    assert ghidra_env.ghidra_version(real) == "12.1.2"
