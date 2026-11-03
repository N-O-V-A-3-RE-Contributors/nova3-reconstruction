"""Offline inventory-metadata contracts, not symbol or correspondence evidence.

All scanned trees are synthetic and temporary. During tree_stats calls, guards
reject content reads, filesystem traversal/stat outside tmp_path, subprocesses,
and network access. No private source tree or ELF is required.
"""

import builtins
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import socket
import subprocess

import pytest


# Deliberately not traversal order; only synthetic bytes are materialized.
_FILES = (
    ("z.CPP", b"zzz"),
    ("beta/pack/nested/item", b"i" * 9),
    ("alpha/src/other/c.H", b"h" * 7),
    ("README", b"hello"),
    ("beta/pack/empty", b""),
    ("alpha/src/deep/c.H", b"h" * 10),
    ("alpha/brief.h", b"hh"),
    ("beta/shallow", b"s"),
    ("alpha/src/a.CPP", b"aaaa"),
    ("beta/pack/archive.TAR.GZ", b"g" * 8),
    ("alpha/src/deep/b.cpp", b"b" * 6),
)

# Independent explicit oracle: sorted files at each directory, then sorted
# subdirectories depth-first. This is NOT a global relative-path sort: root
# z.CPP precedes alpha/, and beta/shallow precedes beta/pack/.
_MANIFEST_RECORDS = (
    b"README\x005\n",
    b"z.CPP\x003\n",
    b"alpha/brief.h\x002\n",
    b"alpha/src/a.CPP\x004\n",
    b"alpha/src/deep/b.cpp\x006\n",
    b"alpha/src/deep/c.H\x0010\n",
    b"alpha/src/other/c.H\x007\n",
    b"beta/shallow\x001\n",
    b"beta/pack/archive.TAR.GZ\x008\n",
    b"beta/pack/empty\x000\n",
    b"beta/pack/nested/item\x009\n",
)


def _make_tree(root, entries=_FILES):
    root.mkdir()
    for relative, content in entries:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    # Directories themselves must not contribute files, bytes or manifest rows.
    (root / "unused" / "empty").mkdir(parents=True)
    return root


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    # Import only the public repository tool; never invoke its CLI/build path.
    source = (Path(__file__).resolve().parents[2]
              / "tools/source_ref/index_source_reference.py")
    spec = importlib.util.spec_from_file_location("tree_stats_contract_tool", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    original_walk = os.walk
    original_stat = os.stat
    original_scandir = os.scandir
    temporary_root = os.path.abspath(tmp_path)

    def require_temporary(path):
        absolute = os.path.abspath(os.fspath(path))
        assert os.path.commonpath((temporary_root, absolute)) == temporary_root, (
            "inventory must not access paths outside the synthetic temporary tree"
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("metadata inventory must not read content, spawn or connect")

    def walk(path, *args, **kwargs):
        require_temporary(path)
        return original_walk(path, *args, **kwargs)

    def stat(path, *args, **kwargs):
        require_temporary(path)
        return original_stat(path, *args, **kwargs)

    def scandir(path):
        require_temporary(path)
        return original_scandir(path)

    def run(root):
        require_temporary(root)
        # Scope global I/O guards to the call, not pytest's own reporting or
        # fixture setup/cleanup. Even synthetic content reads are prohibited.
        with monkeypatch.context() as guard:
            guard.setattr(os, "walk", walk)
            guard.setattr(os, "stat", stat)
            guard.setattr(os, "scandir", scandir)
            for owner, names in (
                (builtins, ("open",)),
                (io, ("open",)),
                (os, ("open", "system", "popen")),
                (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
                (socket, ("socket", "create_connection", "getaddrinfo")),
                (module, ("build", "elf_source_paths", "cross_check", "locate_symbols")),
            ):
                for name in names:
                    guard.setattr(owner, name, forbidden)
            return module.tree_stats(root)

    return run


def test_exact_inventory_aggregates_and_ordered_metadata_manifest(tmp_path, inventory):
    report = inventory(_make_tree(tmp_path / "synthetic"))

    assert report == {
        "files": 11,
        "bytes": 55,
        "manifest_sha256": hashlib.sha256(b"".join(_MANIFEST_RECORDS)).hexdigest(),
        "by_extension": {
            ".h": {"files": 3, "bytes": 19},
            "(none)": {"files": 4, "bytes": 15},
            ".cpp": {"files": 3, "bytes": 13},
            ".gz": {"files": 1, "bytes": 8},
        },
        "top_directories": [
            {"path": "alpha/src/deep", "files": 2, "bytes": 16},
            {"path": "beta/pack/nested", "files": 1, "bytes": 9},
            {"path": "beta/pack/archive.TAR.GZ", "files": 1, "bytes": 8},
            {"path": "alpha/src/other", "files": 1, "bytes": 7},
            {"path": "README", "files": 1, "bytes": 5},
            {"path": "alpha/src/a.CPP", "files": 1, "bytes": 4},
            {"path": "z.CPP", "files": 1, "bytes": 3},
            {"path": "alpha/brief.h", "files": 1, "bytes": 2},
            {"path": "beta/shallow", "files": 1, "bytes": 1},
            {"path": "beta/pack/empty", "files": 1, "bytes": 0},
        ],
    }


def test_creation_order_and_absolute_root_do_not_change_report(tmp_path, inventory):
    first = _make_tree(tmp_path / "first", _FILES)
    second = _make_tree(tmp_path / "second", reversed(_FILES))

    assert inventory(first) == inventory(second)


def test_same_length_content_replacement_does_not_change_manifest(tmp_path, inventory):
    root = _make_tree(tmp_path / "synthetic")
    before = inventory(root)
    (root / "alpha/src/a.CPP").write_bytes(b"ZZZZ")
    after = inventory(root)

    assert after["manifest_sha256"] == before["manifest_sha256"]
    assert after == before  # This report inventories metadata, not file content.


@pytest.mark.parametrize("nested_empty_directories", [False, True])
def test_empty_tree_has_empty_aggregates_and_empty_digest(
    tmp_path, inventory, nested_empty_directories
):
    root = tmp_path / "empty"
    root.mkdir()
    if nested_empty_directories:
        (root / "one" / "two" / "three").mkdir(parents=True)

    assert inventory(root) == {
        "files": 0,
        "bytes": 0,
        "manifest_sha256": hashlib.sha256(b"").hexdigest(),
        "by_extension": {},
        "top_directories": [],
    }
