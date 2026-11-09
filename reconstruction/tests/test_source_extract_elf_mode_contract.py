"""Offline contract tests for ARM/Thumb attribution in extract_elf_symbols.

``tools/source_ref/extract_elf_symbols.py`` attributes every function's mode
from the ``$a``/``$t``/``$d`` mapping symbols of the function's own section:
the last mapping symbol at a value ``<=`` the function address decides the mode
(``mode_for``, lines 171-183), and ``extract()`` later folds ``'data'`` to
``'unknown'``.  The module docstring promises that state is "never guessed from
the address".

These tests pin that boundary with synthetic ``by_section`` dicts only:

* ``mode_for`` is called with plain dicts; it imports ``bisect`` itself, so no
  ELF, capstone, or analysis/ file is involved.
* ``build_symbol_tables`` is driven by a fake ELF/symbol-table object to check
  that mapping symbols are grouped by ``st_shndx`` and sections stay
  independent.

To import the module without capstone or pyelftools, stub modules temporarily
replace the dependency entries in ``sys.modules`` within a scoped patch. The
exact prior entries (including absent entries) are restored even on failure.
Installed dependencies are deliberately not exercised by this offline test.

Scope declaration: this is a metadata/control-flow contract test.  It says
nothing about instruction windows, decoded code, or behavioural equivalence,
and it never reads the proprietary ELF.  NOT MATCHED / UNKNOWN at the
machine-code level.
"""
from __future__ import annotations

import builtins
import importlib
import importlib.util
import io
import os
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools/source_ref/extract_elf_symbols.py"
_TOOL_SOURCE = MODULE_PATH.read_text(encoding="utf-8")  # source text only

MODES = frozenset({"arm", "thumb", "data", "unknown"})

# The synthetic section used throughout: one ARM run, then Thumb, then ARM.
SECTION_TABLE = ([0x1000, 0x1008, 0x1020], ["$a", "$t", "$a"])


# --------------------------------------------------------------------------
# Offline import: temporarily stub capstone / capstone.arm / elftools.elf.elffile.
# Only the names the module touches at import
# time need to exist: capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM,
# capstone.CS_MODE_THUMB, capstone.arm.ARM_OP_IMM, and
# elftools.elf.elffile.ELFFile.
# --------------------------------------------------------------------------

def _stub_modules() -> dict[str, types.ModuleType]:
    capstone = types.ModuleType("capstone")
    capstone.CS_ARCH_ARM = 0
    capstone.CS_MODE_ARM = 0
    capstone.CS_MODE_THUMB = 1
    capstone.Cs = lambda *a, **k: None

    arm_mod = types.ModuleType("capstone.arm")
    arm_mod.ARM_OP_IMM = 2
    arm_mod.ARM_OP_REG = 1
    arm_mod.ARM_OP_MEM = 3

    elftools = types.ModuleType("elftools")
    elftools_pkg = types.ModuleType("elftools.elf")
    elffile = types.ModuleType("elftools.elf.elffile")
    elffile.ELFFile = object

    capstone.arm = arm_mod
    elftools.elf = elftools_pkg
    elftools_pkg.elffile = elffile
    return {
        "capstone": capstone,
        "capstone.arm": arm_mod,
        "elftools": elftools,
        "elftools.elf": elftools_pkg,
        "elftools.elf.elffile": elffile,
    }


@contextmanager
def _offline_imports():
    """Restore exact dependency identities in finally, without importing them."""
    stubs = _stub_modules()
    with pytest.MonkeyPatch.context() as patch:
        for name, stub in stubs.items():
            patch.setitem(sys.modules, name, stub)
        yield stubs


_MISSING = object()
_DEPENDENCY_NAMES = tuple(_stub_modules())
_COLLECTION_BEFORE = {
    name: sys.modules.get(name, _MISSING) for name in _DEPENDENCY_NAMES
}
with _offline_imports():
    SPEC = importlib.util.spec_from_file_location(
        "source_extract_elf_mode_contract", MODULE_PATH)
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
_COLLECTION_AFTER = {
    name: sys.modules.get(name, _MISSING) for name in _DEPENDENCY_NAMES
}


def test_collection_restores_dependency_module_identities():
    # Capture immediately around actual collection, not after other test imports.
    for name in _DEPENDENCY_NAMES:
        assert _COLLECTION_AFTER[name] is _COLLECTION_BEFORE[name], name


@pytest.mark.parametrize("preexisting", [False, True])
@pytest.mark.parametrize("fail_import", [False, True])
def test_offline_import_scope_restores_modules(monkeypatch, preexisting, fail_import):
    prior = {}
    for name in _DEPENDENCY_NAMES:
        if preexisting:
            prior[name] = types.ModuleType(name)
            monkeypatch.setitem(sys.modules, name, prior[name])
        else:
            monkeypatch.delitem(sys.modules, name, raising=False)

    class ImportFailure(Exception):
        pass

    def exercise_scope():
        with _offline_imports() as stubs:
            for name, stub in stubs.items():
                assert sys.modules[name] is stub
                if preexisting:
                    assert stub is not prior[name]
            if fail_import:
                raise ImportFailure

    if fail_import:
        with pytest.raises(ImportFailure):
            exercise_scope()
    else:
        exercise_scope()
    for name in _DEPENDENCY_NAMES:
        if preexisting:
            assert sys.modules[name] is prior[name], name
        else:
            assert name not in sys.modules, name


@pytest.fixture(autouse=True)
def _forbid_external_effects(monkeypatch):
    """No subprocesses and no file opens during any test in this module."""
    def forbidden_process(*args, **kwargs):
        raise AssertionError("mode attribution must not spawn a subprocess")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden_process)
    for name in ("system", "popen", "spawnv", "spawnve", "execv", "execve"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden_process)

    def forbidden_open(file, *args, **kwargs):
        raise AssertionError(f"mode attribution must not open files: {file!r}")

    monkeypatch.setattr(builtins, "open", forbidden_open)
    monkeypatch.setattr(io, "open", forbidden_open)
    yield


# --------------------------------------------------------------------------
# mode_for contract
# --------------------------------------------------------------------------

def test_mode_for_is_importable_without_analysis_inputs():
    assert callable(module.mode_for)
    assert module.__name__ == "source_extract_elf_mode_contract"
    # The synthetic table lives in the test, not in a loaded ELF.
    assert not hasattr(module, "image")


@pytest.mark.parametrize(
    "addr, expected",
    [
        (0x1000, "arm"),     # exactly at the first $a
        (0x1004, "arm"),     # inside the same $a run
        (0x1008, "thumb"),   # exactly at $t
        (0x101C, "thumb"),   # last address before the next $a
        (0x1020, "arm"),     # exactly at the second $a
        (0x0FFF, "unknown"),  # no mapping symbol at or before the address
    ],
)
def test_mode_at_and_next_to_mapping_symbols(addr, expected):
    table = {"section": SECTION_TABLE}
    assert module.mode_for(table, addr, "section") == expected


def test_values_after_last_mapping_symbol_stay_in_last_run():
    table = {"section": SECTION_TABLE}
    # Above the last mapping symbol the last mode persists; no guessing.
    assert module.mode_for(table, 0x1024, "section") == "arm"
    assert module.mode_for(table, 0xFFFF_FFFE, "section") == "arm"


def test_every_returned_value_is_in_extract_mode_set():
    table = {
        "s": SECTION_TABLE,
        "d_section": ([0x2000], ["$d"]),
        "empty": ([], []),
        "a_only": ([0x3000], ["$a"]),
        "t_first": ([0x4000], ["$t"]),
    }
    probes = [
        ("s", a) for a in (0x0FFF, 0x1000, 0x1004, 0x1008, 0x101C, 0x1020, 0x2000)
    ] + [
        ("d_section", 0x2000), ("empty", 0x1000), ("a_only", 0x2FFF),
        ("a_only", 0x3000), ("t_first", 0x4000), ("absent", 0x1000),
        (7, 0x1000), (None, 0x1000),
    ]
    seen = set()
    for shndx, addr in probes:
        value = module.mode_for(table, addr, shndx)
        assert type(value) is str
        assert value in MODES, (shndx, hex(addr), value)
        seen.add(value)
    # extract() only special-cases 'data'; the set it can see is exactly MODES.
    assert seen <= MODES
    assert {"arm", "thumb", "data", "unknown"} <= seen


@pytest.mark.parametrize(
    "shndx, by_section, addr",
    [
        ("absent", {"section": SECTION_TABLE}, 0x1000),
        ("empty", {"empty": ([], [])}, 0x1000),
        ("empty_at_zero", {"empty": ([], [])}, 0),
        (3, {3: ([], [])}, 0x1000),
        (3, {3: ([], [])}, 0),
    ],
)
def test_missing_or_empty_section_tables_are_unknown(shndx, by_section, addr):
    assert module.mode_for(by_section, addr, shndx) == "unknown"


def test_empty_pair_absent_key_and_empty_mapped_table_agree():
    table = {"section": SECTION_TABLE, 5: ([], [])}
    values = {
        module.mode_for(table, 0x1000, "missing"),
        module.mode_for(table, 0x1000, 5),
        module.mode_for({}, 0x1000, 3),
        module.mode_for({"e": ([], [])}, 0x1000, "e"),
    }
    assert values == {"unknown"}


def test_data_mapping_symbol_yields_data():
    table = {"ro": ([0x5000], ["$d"])}
    assert module.mode_for(table, 0x5000, "ro") == "data"
    assert module.mode_for(table, 0x5400, "ro") == "data"
    assert module.mode_for(table, 0x4FFC, "ro") == "unknown"


def test_mapping_symbols_switch_cumulatively():
    table = {"s": ([0x100, 0x200, 0x300, 0x400], ["$a", "$t", "$d", "$t"])}
    assert module.mode_for(table, 0x100, "s") == "arm"
    assert module.mode_for(table, 0x1FF, "s") == "arm"
    assert module.mode_for(table, 0x200, "s") == "thumb"
    assert module.mode_for(table, 0x2FF, "s") == "thumb"
    assert module.mode_for(table, 0x300, "s") == "data"
    assert module.mode_for(table, 0x3FF, "s") == "data"
    assert module.mode_for(table, 0x400, "s") == "thumb"


def test_mode_for_does_not_mutate_the_synthetic_tables():
    addrs, names = list(SECTION_TABLE[0]), list(SECTION_TABLE[1])
    table = {"section": ([0x1000, 0x1008, 0x1020], ["$a", "$t", "$a"])}
    for addr in (0x0FFF, 0x1000, 0x1008, 0x101C, 0x1020):
        module.mode_for(table, addr, "section")
    assert table["section"] == (addrs, names)


# --------------------------------------------------------------------------
# build_symbol_tables mapping collection (lines 133-142)
# --------------------------------------------------------------------------

class _FakeSymbol:
    """Minimal stand-in for elftools' Symbol: name plus the keys the tool reads."""

    def __init__(self, name, value=0, shndx=0, type_="STT_NOTYPE", bind="STB_LOCAL",
                 size=0):
        self.name = name
        self._value = value
        self._shndx = shndx
        self._type = type_
        self._bind = bind
        self._size = size

    def __getitem__(self, key):
        if key == "st_info":
            return {"type": self._type, "bind": self._bind}
        if key == "st_value":
            return self._value
        if key == "st_shndx":
            return self._shndx
        if key == "st_size":
            return self._size
        raise KeyError(key)


class _FakeSymtab:
    def __init__(self, symbols):
        self._symbols = symbols

    def iter_symbols(self):
        return iter(self._symbols)


class _FakeElf:
    def __init__(self, symtab):
        self._symtab = symtab

    def get_section_by_name(self, name):
        return self._symtab if name == ".symtab" else None


class _FakeImage:
    def __init__(self, symbols):
        self.elf = _FakeElf(_FakeSymtab(symbols))


def _build(symbols):
    return module.build_symbol_tables(_FakeImage(symbols))


def test_build_symbol_tables_groups_mapping_symbols_by_shndx():
    symbols = [
        _FakeSymbol("$t", value=0x2008, shndx=5),
        _FakeSymbol("$a", value=0x2000, shndx=5),
        _FakeSymbol("$a", value=0x1000, shndx=3),
        _FakeSymbol("$t", value=0x1008, shndx=3),
        _FakeSymbol("$a", value=0x1020, shndx=3),
        _FakeSymbol("$d", value=0x3000, shndx=7),
        _FakeSymbol("unit.cpp", type_="STT_FILE"),  # must precede local symbols
        _FakeSymbol("func_thumb", value=0x1009, shndx=3, type_="STT_FUNC", size=8),
        _FakeSymbol("other.cpp", type_="STT_FILE"),  # file hint is local-only
        _FakeSymbol("obj", value=0x3000, shndx=7, type_="STT_OBJECT", size=4),
    ]
    funcs, by_addr, data_by_addr, by_section, file_hints = _build(symbols)

    assert [sym.name for _, sym in funcs] == ["func_thumb"]
    assert by_addr == {0x1009: "func_thumb"}
    assert data_by_addr == {0x3000: "obj"}
    assert by_section == {
        3: ([0x1000, 0x1008, 0x1020], ["$a", "$t", "$a"]),
        5: ([0x2000, 0x2008], ["$a", "$t"]),
        7: ([0x3000], ["$d"]),
    }
    assert file_hints == {7: "unit.cpp"}
    assert 9 not in file_hints  # STT_OBJECT never receives a file hint

    # Independent sections: each section's own $t decides only its own lookups.
    assert module.mode_for(by_section, 0x1008, 3) == "thumb"
    assert module.mode_for(by_section, 0x1008, 5) == "unknown"
    assert module.mode_for(by_section, 0x2004, 5) == "arm"
    assert module.mode_for(by_section, 0x2004, 3) == "arm"  # outside sec 3's run
    assert module.mode_for(by_section, 0x2008, 5) == "thumb"
    assert module.mode_for(by_section, 0x2008, 3) == "arm"
    assert module.mode_for(by_section, 0x1000, 5) == "unknown"
    # In section 3, 0x2000 is past the last ($a) mapping symbol, so its last run
    # persists; section 5's $t at the same-ish value never reaches section 3.
    assert module.mode_for(by_section, 0x2000, 3) == "arm"
    assert module.mode_for(by_section, 0x3000, 7) == "data"
    # The same address in a section with no $d of its own never sees 'data':
    # it keeps that section's own last run (3 ends in $a, 5 ends in $t).
    assert module.mode_for(by_section, 0x3000, 3) == "arm"
    assert module.mode_for(by_section, 0x3000, 5) == "thumb"


def test_build_symbol_tables_sorts_each_section_and_ignores_stray_names():
    symbols = [
        _FakeSymbol("$t", value=0x1010, shndx=2),
        _FakeSymbol("$a", value=0x1000, shndx=2),
        _FakeSymbol("$d", value=0x0F00, shndx=2),
        _FakeSymbol("$x", value=0x0F80, shndx=2),  # not a known mapping symbol
        _FakeSymbol("$a.1", value=0x0F90, shndx=2),  # suffixed, not exactly $a
        _FakeSymbol("$t", value=0x0020, shndx=9),
        _FakeSymbol("$a", value=0x0000, shndx=9),
    ]
    by_section = _build(symbols)[3]
    assert by_section == {
        2: ([0x0F00, 0x1000, 0x1010], ["$d", "$a", "$t"]),
        9: ([0x0000, 0x0020], ["$a", "$t"]),
    }
    assert module.mode_for(by_section, 0x0F40, 2) == "data"
    assert module.mode_for(by_section, 0x0F80, 2) == "data"
    assert module.mode_for(by_section, 0x0FFF, 2) == "data"
    assert module.mode_for(by_section, 0x1000, 2) == "arm"
    assert module.mode_for(by_section, 0x1010, 2) == "thumb"
    assert module.mode_for(by_section, 0x0000, 9) == "arm"


def test_build_symbol_tables_without_mapping_symbols_gives_unknown():
    symbols = [
        _FakeSymbol("f", value=0x1000, shndx=1, type_="STT_FUNC", size=4),
    ]
    funcs, _, _, by_section, _ = _build(symbols)
    assert len(funcs) == 1
    assert by_section == {}
    assert module.mode_for(by_section, 0x1000, 1) == "unknown"


def test_module_never_calls_extract_or_subprocesses():
    # Read the tool source once at import-collection time, before the fixture
    # that forbids file opens is active (this is source text, never the ELF).
    source = _TOOL_SOURCE
    assert "def extract(" in source  # the extractor exists...
    # ... but only main() may call it; no test path in this file runs it.
    calls = [line.strip() for line in source.splitlines()
             if "extract(" in line and "def extract(" not in line]
    assert calls == ["rows, summary = extract(args.elf)"], calls
