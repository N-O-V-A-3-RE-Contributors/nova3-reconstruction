"""Hermetic tests for the unattended decompilation driver.

These never touch the private reference ELF: they only exercise the driver's
argument/verification/status contract, so they run on a clean checkout with no
`analysis/`, no `analysis_db/` and no Ghidra installation.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tools/pipeline/auto_decompile.py"
SELF = Path(__file__).resolve()


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DRIVER), *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_missing_reference_is_rejected():
    proc = run("--binary", "analysis/does-not-exist.so", "--status")
    assert proc.returncode == 2
    assert "missing reference" in proc.stderr


def test_hash_mismatch_aborts_before_any_work():
    proc = run("--binary", str(SELF), "--expected-sha256", "0" * 64, "--status")
    assert proc.returncode == 2
    assert "expected" in proc.stderr


def test_status_reports_a_complete_coverage_triple():
    proc = run("--binary", str(SELF), "--status")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert set(report) == {"binary", "sha256", "indexed", "exported", "remaining"}
    assert report["remaining"] == max(report["indexed"] - report["exported"], 0)
    assert len(report["sha256"]) == 64


def test_batch_arguments_are_wired_to_the_exporter():
    """The driver must drive the exporter in symbol order, not by its default
    two-address selection; that defect silently produced zero progress."""
    source = DRIVER.read_text()
    assert '"--all-symbols"' in source
    assert '"--project-dir"' in source
    assert "tools/ghidra/export_batch.py" in source
