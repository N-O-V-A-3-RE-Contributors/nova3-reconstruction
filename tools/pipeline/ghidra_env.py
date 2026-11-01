#!/usr/bin/env python3
"""Locate a Ghidra installation and a JDK without hardcoding one host's paths.

`tools/ghidra/export_batch.py` used to call `pyghidra.start(install_dir='/opt/ghidra')`
and set `JAVA_HOME=/usr/lib/jvm/java-21-openjdk` unconditionally. That works on
the maintainer's Arch laptop and nowhere else, so the documented
"start it and walk away" command failed on any other checkout. This module is
the single place that answers "where is Ghidra?" so the workbench, the exporter
and `doctor` cannot disagree.

Search order (first hit wins):

1. explicit argument (`--ghidra-home`);
2. `NOVA3_GHIDRA_HOME`, then the conventional `GHIDRA_INSTALL_DIR`;
3. well-known system locations (`/opt/ghidra`, `/usr/share/ghidra`,
   `/usr/local/ghidra`);
4. versioned directories under `~/`, `~/.local/opt/`, `/opt/`
   (`ghidra_11.3_PUBLIC`, `ghidra-12.1`, ...), newest version first.

A directory only counts when it contains `support/analyzeHeadless`, which is
what PyGhidra actually needs; merely existing is not enough.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def _is_ghidra(home: Path) -> bool:
    return (home / "support/analyzeHeadless").is_file()


def _version_key(path: Path) -> tuple:
    numbers = re.findall(r"\d+", path.name)
    return tuple(int(n) for n in numbers) or (0,)


def _candidates(explicit: str | os.PathLike | None) -> list[Path]:
    out: list[Path] = []
    for value in (explicit, os.environ.get("NOVA3_GHIDRA_HOME"),
                  os.environ.get("GHIDRA_INSTALL_DIR")):
        if value:
            out.append(Path(value).expanduser())
    out += [Path("/opt/ghidra"), Path("/usr/share/ghidra"), Path("/usr/local/ghidra")]
    home = Path.home()
    for base in (home, home / ".local/opt", Path("/opt"), home / "Applications"):
        if not base.is_dir():
            continue
        try:
            found = [p for p in base.iterdir() if p.is_dir() and p.name.lower().startswith("ghidra")]
        except OSError:
            continue
        out += sorted(found, key=_version_key, reverse=True)
    return out


def resolve_ghidra_install(explicit: str | os.PathLike | None = None) -> Path | None:
    """Return the first usable Ghidra install directory, or None.

    An explicit argument is authoritative: when it does not contain
    `support/analyzeHeadless` this returns None instead of silently falling back
    to a different installation, so a typo in `--ghidra-home` is a visible error
    rather than a run against the wrong Ghidra.
    """
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if _is_ghidra(candidate) else None
    seen: set[str] = set()
    for candidate in _candidates(None):
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if _is_ghidra(candidate):
            return candidate
    return None


def ghidra_version(home: Path | None) -> str | None:
    """Application version from Ghidra's own properties file, when readable."""
    if home is None:
        return None
    props = home / "Ghidra/application.properties"
    try:
        for line in props.read_text(errors="replace").splitlines():
            if line.startswith("application.version="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


def resolve_java_home(explicit: str | os.PathLike | None = None) -> Path | None:
    """Return a JDK home directory (Ghidra 11+/12 needs 17 or newer)."""
    for value in (explicit, os.environ.get("JAVA_HOME")):
        if value and (Path(value) / "bin/java").is_file():
            return Path(value)
    roots = [Path("/usr/lib/jvm"), Path("/usr/java"), Path("/opt/java")]
    best: tuple[tuple, Path] | None = None
    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = [p for p in root.iterdir() if (p / "bin/java").is_file()]
        except OSError:
            continue
        for entry in entries:
            key = _version_key(entry)
            if best is None or key > best[0]:
                best = (key, entry)
    return best[1] if best else None


if __name__ == "__main__":
    home = resolve_ghidra_install()
    java = resolve_java_home()
    print("ghidra_install_dir=%s" % home)
    print("ghidra_version=%s" % ghidra_version(home))
    print("java_home=%s" % java)
