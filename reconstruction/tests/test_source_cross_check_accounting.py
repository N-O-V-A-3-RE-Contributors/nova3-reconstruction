"""Offline existence/accounting contracts using only synthetic trees and paths.

Normalization is replaced, not tested here. These checks establish no source
correspondence or game behavioural equivalence; no private references are read.
"""

import pytest

from tools.source_ref import index_source_reference as source_index


@pytest.fixture
def synthetic_cross_check(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    elf = tmp_path / "never-read.elf"

    def forbid_subprocess(*args, **kwargs):
        pytest.fail("cross_check must not execute a subprocess")

    monkeypatch.setattr(source_index.subprocess, "run", forbid_subprocess)

    def run(normalized_results):
        # Opaque raw tokens keep normalization rules outside this module's scope.
        raw_paths = [f"raw-{index}" for index in range(len(normalized_results))]
        normalization = dict(zip(raw_paths, normalized_results))
        elf_calls = []
        normalization_calls = []

        def fake_elf_source_paths(path):
            elf_calls.append(path)
            return list(raw_paths)

        def fake_normalise_source_path(raw):
            normalization_calls.append(raw)
            return normalization[raw]

        monkeypatch.setattr(source_index, "elf_source_paths", fake_elf_source_paths)
        monkeypatch.setattr(
            source_index, "normalise_source_path", fake_normalise_source_path
        )
        assert not elf.exists()
        result = source_index.cross_check(root, elf)
        assert elf_calls == [elf]
        assert normalization_calls == raw_paths
        return result

    return root, elf, run


def make_file(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


@pytest.mark.parametrize("layout", ["", "NOVA3"])
def test_files_resolve_in_either_layout_but_directories_do_not(
    synthetic_cross_check, layout
):
    root, elf, run = synthetic_cross_check
    make_file(root / layout, "trunk/present.cpp")
    make_file(root / layout, "extern/present.h")
    # Both lookup candidates exist, but neither is a file.
    for candidate_root in (root, root / "NOVA3"):
        (candidate_root / "trunk/directory.cpp").mkdir(parents=True)

    assert run(
        [
            "trunk/present.cpp",
            "extern/present.h",
            "trunk/directory.cpp",
            "extern/missing.h",
            None,
            "",
        ]
    ) == {
        "elf": str(elf),
        "path_strings": 6,
        "resolved": 4,
        "present_in_tree": 2,
        "missing_in_tree": 2,
        "present_ratio": 0.5,
        "by_extension": {".cpp": 2, ".h": 2},
        "sample_present": ["extern/present.h", "trunk/present.cpp"],
        "sample_missing": ["extern/missing.h", "trunk/directory.cpp"],
    }


def test_present_counts_occurrences_while_missing_counts_unique_paths(
    synthetic_cross_check,
):
    root, elf, run = synthetic_cross_check
    # Having a file in both layouts must not double-count an occurrence.
    make_file(root, "trunk/one.cpp")
    make_file(root / "NOVA3", "trunk/one.cpp")
    # A directory in the first location must not hide a file in the fallback.
    (root / "extern/two.h").mkdir(parents=True)
    make_file(root / "NOVA3", "extern/two.h")

    assert run(
        [
            "trunk/one.cpp",
            "extern/lost.h",
            "extern/two.h",
            "trunk/one.cpp",
            "extern/lost.h",
            "trunk/one.cpp",
            "trunk/lost.cpp",
            None,
            "",
        ]
    ) == {
        "elf": str(elf),
        "path_strings": 9,
        "resolved": 7,
        "present_in_tree": 4,
        "missing_in_tree": 2,
        "present_ratio": 4 / 7,
        # Missing occurrences contribute, even when their paths repeat.
        "by_extension": {".cpp": 4, ".h": 3},
        "sample_present": ["extern/two.h", "trunk/one.cpp"],
        "sample_missing": ["extern/lost.h", "trunk/lost.cpp"],
    }


def test_samples_are_sorted_unique_and_capped_after_deduplication(
    synthetic_cross_check,
):
    root, elf, run = synthetic_cross_check
    present = [f"trunk/present_{index:02d}.cpp" for index in range(15)]
    missing = [f"extern/missing_{index:02d}.h" for index in range(23)]
    for index, relative in enumerate(present):
        make_file(root if index % 2 else root / "NOVA3", relative)

    # Reverse ordering and adjacent duplicates expose sampling before sorting
    # or deduplication. Both unique populations exceed their respective caps.
    results = [relative for relative in reversed(present + missing) for _ in range(2)]
    assert run(results) == {
        "elf": str(elf),
        "path_strings": 76,
        "resolved": 76,
        "present_in_tree": 30,
        "missing_in_tree": 23,
        "present_ratio": 30 / 76,
        "by_extension": {".h": 46, ".cpp": 30},
        "sample_present": present[:12],
        "sample_missing": missing[:20],
    }


@pytest.mark.parametrize("normalized_results", [[], [None, "", None]])
def test_zero_resolved_has_null_ratio_and_empty_collections(
    synthetic_cross_check, normalized_results
):
    root, elf, run = synthetic_cross_check
    # Unreferenced files do not affect accounting, even in either layout.
    make_file(root, "trunk/unreferenced.cpp")
    make_file(root / "NOVA3", "extern/unreferenced.h")

    assert run(normalized_results) == {
        "elf": str(elf),
        "path_strings": len(normalized_results),
        "resolved": 0,
        "present_in_tree": 0,
        "missing_in_tree": 0,
        "present_ratio": None,
        "by_extension": {},
        "sample_present": [],
        "sample_missing": [],
    }
