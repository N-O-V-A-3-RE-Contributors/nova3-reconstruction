"""Host resource guards shared by the installer and the worker loop.

The maintainer laptop is an Intel Celeron N5100 (4 cores, ~8 GiB RAM). A model
worker plus a compiler plus a Ghidra import will not fit side by side, so the
loop checks memory pressure and disk before starting each pass and refuses to
pile passes on top of a machine that is already exhausted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

MiB = 1024 * 1024


@dataclass(frozen=True)
class Memory:
    total: int
    available: int

    @property
    def available_mib(self) -> int:
        return self.available // MiB


@dataclass(frozen=True)
class Disk:
    total: int
    free: int

    @property
    def free_gib(self) -> float:
        return round(self.free / (1024 ** 3), 1)


def memory() -> Memory | None:
    """Best-effort available memory: /proc/meminfo, then sysconf, then sysctl."""
    try:
        with open("/proc/meminfo", encoding="ascii", errors="replace") as handle:
            info = {}
            for line in handle:
                key, _, rest = line.partition(":")
                info[key.strip()] = int(rest.split()[0]) * 1024 if rest.split() else 0
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable") or info.get("MemFree", 0)
        if total:
            return Memory(total, available)
    except OSError:
        pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page = os.sysconf("SC_PAGE_SIZE")
        total = pages * page
        return Memory(total, total // 2)
    except (ValueError, OSError, AttributeError):
        pass
    if os.name == "nt":
        try:
            cp = subprocess.run(["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/format:list"],
                                capture_output=True, text=True, timeout=15, check=False)
            values = dict(
                line.split("=", 1) for line in cp.stdout.splitlines() if "=" in line)
            return Memory(int(values.get("TotalVisibleMemorySize", 0)) * 1024,
                          int(values.get("FreePhysicalMemory", 0)) * 1024)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
    return None


def disk(path: Path) -> Disk | None:
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return None
    return Disk(usage.total, usage.free)


def cpu_count() -> int:
    return os.cpu_count() or 1


def pressure(mem: Memory | None, minimum_available_mib: int) -> str | None:
    """Return a human-readable refusal reason, or None when there is room."""
    if mem is None:
        return None
    if mem.available_mib < minimum_available_mib:
        return (f"only {mem.available_mib} MiB RAM available "
                f"(need {minimum_available_mib} MiB); wait for other programs to finish")
    return None


def disk_pressure(space: Disk | None, minimum_free_gib: float) -> str | None:
    if space is None:
        return None
    if space.free_gib < minimum_free_gib:
        return (f"only {space.free_gib} GiB free on disk "
                f"(need {minimum_free_gib} GiB)")
    return None


def describe() -> str:
    mem = memory()
    space = disk(Path.cwd())
    parts = [f"cpus={cpu_count()}"]
    if mem:
        parts.append(f"ram={mem.total // MiB} MiB total/{mem.available_mib} MiB available")
    if space:
        parts.append(f"disk={space.free_gib} GiB free")
    return " ".join(parts)
