"""Synthetic lexical contracts for normalise_source_path only.

These outputs do not establish filesystem confinement, source correspondence,
source existence, or reconstruction equivalence. No private references are used.
"""

import pytest

from tools.source_ref.index_source_reference import normalise_source_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"  C:\Build\Nova3data\trunk\Engine\MiXeD.CPP  ", "trunk/Engine/MiXeD.CPP"),
        (r"extern\Vendor\Header.H", "extern/Vendor/Header.H"),
        ("\ttrunk/Engine/File.cpp\n", "trunk/Engine/File.cpp"),
        ("Nova3data/trunk/Engine/File.cpp", "trunk/Engine/File.cpp"),
        ("nOvA3DaTa/TrUnK/Engine/MiXeD.CPP", "TrUnK/Engine/MiXeD.CPP"),
        ("/build/NOVA3DATA/ExTeRn/Vendor/Header.H", "ExTeRn/Vendor/Header.H"),
        ("trunk/Engine/File.cpp", "trunk/Engine/File.cpp"),
        ("extern/Vendor/Header.H", "extern/Vendor/Header.H"),
        ("/synthetic/build/trunk/Engine/File.cpp", "trunk/Engine/File.cpp"),
        ("/synthetic/build/extern/Vendor/Header.H", "extern/Vendor/Header.H"),
        (r"D:\Build\trunk\Engine\File.cpp", "trunk/Engine/File.cpp"),
        (r"D:\Build\extern\Vendor\Header.H", "extern/Vendor/Header.H"),
    ],
)
def test_recognized_prefixes_preserve_remaining_spelling(raw, expected):
    assert normalise_source_path(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("trunk/./Engine//File.cpp", "trunk/Engine/File.cpp"),
        ("extern/Vendor/../Other/Header.H", "extern/Other/Header.H"),
        (r"Nova3data\trunk\Engine\..\File.cpp", "trunk/File.cpp"),
        ("trunk/Engine/", "trunk/Engine"),
        # Recognition precedes normalization; the result need not retain a root.
        ("trunk/../Elsewhere/File.cpp", "Elsewhere/File.cpp"),
        ("extern/..", "."),
        ("Nova3data/", "."),
        ("Nova3data/Other/MiXeD.txt", "Other/MiXeD.txt"),
        # A marker followed by an extra slash can even yield an absolute path.
        ("Nova3data//Other/File.cpp", "/Other/File.cpp"),
    ],
)
def test_dot_components_and_marker_tails_are_only_lexical(raw, expected):
    assert normalise_source_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " \t\n",
        "Engine/File.cpp",
        "/synthetic/build/Engine/File.cpp",
        "trunkish/File.cpp",
        "external/File.cpp",
        "trunk",
        "extern",
        "Nova3data",
        "Nova3database/trunkish/File.cpp",
    ],
)
def test_unrecognized_paths_are_rejected(raw):
    assert normalise_source_path(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "Trunk/Engine/File.cpp",
        "EXTERN/Vendor/Header.H",
        "/synthetic/build/TRUNK/Engine/File.cpp",
        "/synthetic/build/Extern/Vendor/Header.H",
        r"C:\Build\TrUnK\Engine\File.cpp",
        r"C:\Build\ExTeRn\Vendor\Header.H",
    ],
)
def test_trunk_and_extern_fallback_is_case_sensitive(raw):
    # Unlike Nova3data marker recognition, fallback matching does not case-fold.
    assert normalise_source_path(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "Nova3data/..",
        "Nova3data/../Outside.cpp",
        "Nova3data/trunk/../../Outside.cpp",
        "trunk/../../Outside.cpp",
        "extern/Vendor/../../../Outside.cpp",
        # The guard rejects any '..' prefix, not just parent-directory parts.
        "Nova3data/..hidden/File.cpp",
        "Nova3data/.../File.cpp",
    ],
)
def test_normalized_results_beginning_with_two_dots_are_rejected(raw):
    assert normalise_source_path(raw) is None
