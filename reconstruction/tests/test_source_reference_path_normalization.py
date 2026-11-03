"""Synthetic string mapping only; no private source tree or ELF is needed.

These tests do not establish filesystem confinement, source correspondence, or
game equivalence. Imports precede the I/O guards; normalizer calls run offline.
"""

import builtins
import io
import os
import socket
import subprocess

import pytest

from tools.source_ref.index_source_reference import normalise_source_path


@pytest.fixture
def offline_normalise(monkeypatch):
    """Reject filesystem reads, child processes, and networking during each call."""
    def forbidden(*args, **kwargs):
        pytest.fail("Path normalization must not perform I/O")

    def normalise(raw):
        # Restore before pytest reporting or teardown can need filesystem access.
        with monkeypatch.context() as guard:
            for owner, names in (
                (builtins, ("open",)),
                (io, ("open",)),
                (os, ("open", "read", "stat", "lstat", "listdir", "scandir",
                      "walk", "readlink", "system", "popen")),
                (subprocess, ("Popen", "run")),
                (socket, ("socket", "create_connection", "getaddrinfo")),
            ):
                for name in names:
                    guard.setattr(owner, name, forbidden)
            return normalise_source_path(raw)

    return normalise


@pytest.mark.parametrize("marker", ["Nova3data", "nova3data", "NOVA3DATA", "nOvA3DaTa"])
@pytest.mark.parametrize("prefix", ["", "C:\\Synthetic\\Build\\"])
def test_windows_marker_recognition_preserves_tail_case(offline_normalise, marker, prefix):
    raw = prefix + marker + r"\TrUnK\Engine\MiXeD.CpP"
    assert offline_normalise(raw) == "TrUnK/Engine/MiXeD.CpP"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("trunk/Engine/File.cpp", "trunk/Engine/File.cpp"),
        ("extern/Vendor/Header.H", "extern/Vendor/Header.H"),
        (r"trunk\Engine\File.cpp", "trunk/Engine/File.cpp"),
        (r"extern\Vendor\Header.H", "extern/Vendor/Header.H"),
        ("/synthetic/build/trunk/Engine/File.cpp", "trunk/Engine/File.cpp"),
        ("/synthetic/build/extern/Vendor/Header.H", "extern/Vendor/Header.H"),
        (r"D:\Build\trunk\Engine\File.cpp", "trunk/Engine/File.cpp"),
        (r"D:\Build\extern\Vendor\Header.H", "extern/Vendor/Header.H"),
        ("/synthetic/NOVA3DATA/ExTeRn/Vendor/Header.H", "ExTeRn/Vendor/Header.H"),
        (" \ttrunk/Engine/File.cpp\n", "trunk/Engine/File.cpp"),
    ],
)
def test_direct_roots_and_absolute_prefixes(offline_normalise, raw, expected):
    assert offline_normalise(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("trunk/./Engine//File.cpp", "trunk/Engine/File.cpp"),
        ("extern/Vendor/../Other/Header.H", "extern/Other/Header.H"),
        (r"Nova3data\trunk\Engine\..\File.cpp", "trunk/File.cpp"),
        ("/build/trunk/Engine/../File.cpp", "trunk/File.cpp"),
        # Lexical normalization need not retain a trunk/extern root.
        ("trunk/../Other/File.cpp", "Other/File.cpp"),
        ("extern/..", "."),
    ],
)
def test_dot_components_are_normalized_lexically(offline_normalise, raw, expected):
    assert offline_normalise(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "", " \t\n", "Engine/File.cpp", "/synthetic/build/Engine/File.cpp",
        "trunkish/File.cpp", "external/File.cpp", "trunk", "extern",
        "Nova3data", "Nova3database/Engine/File.cpp",
        # Only the Nova3data marker is case-insensitive, not fallback roots.
        "Trunk/Engine/File.cpp", "/synthetic/EXTERN/Vendor/Header.H",
    ],
)
def test_unrelated_or_unrecognized_paths_return_none(offline_normalise, raw):
    assert offline_normalise(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "Nova3data/..",
        "Nova3data/../Outside.cpp",
        "Nova3data/trunk/../../Outside.cpp",
        "trunk/../../Outside.cpp",
        "extern/Vendor/../../../Outside.cpp",
        r"C:\Build\Nova3data\extern\..\..\Outside.cpp",
        # The existing guard rejects every '..' prefix, not only '..' parts.
        "Nova3data/..hidden/File.cpp",
        "Nova3data/.../File.cpp",
    ],
)
def test_normalized_two_dot_prefix_returns_none(offline_normalise, raw):
    assert offline_normalise(raw) is None
