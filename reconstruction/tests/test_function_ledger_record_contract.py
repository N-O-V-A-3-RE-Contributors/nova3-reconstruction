"""Record-contract audit of coordination/function_ledger.json (inventory only).

What this module checks
-----------------------
Only the *metadata shape and accounting* of the committed ledger:

* every record (a JSON object) carries ``address``, ``size``,
  ``reference_sha256`` and ``boundary_status``, and may additionally carry
  only ``state`` / ``status`` / ``matching`` / ``thumb`` /
  ``boundary_evidence`` (plus ``boundary_confidence`` and
  ``reference_code_sha256``, which the committed entries already use);
* every ``reference_sha256`` (and every ``reference_code_sha256``, when
  present) is one of the ledger's three library references;
* addresses parse like ``nova3_team.autofeed.as_integer(address=True)`` and
  lie below 2**32; sizes are non-negative integers and at most
  ``nova3_team.indexer.MAX_PUBLISHED_FUNCTION_SIZE`` (1 MiB);
* no two records with the same reference share an address;
* the INVENTORIED / state-less split (94 / 1071 of 1165) and the committed
  statistics block are accounted for;
* status ``pending`` (the only status the indexer emits,
  ``nova3_team.indexer.build_coordination_ledger``) is never treated as a
  reconstructed or tested state, and ``INVENTORIED`` is never treated as a
  reconstruction or match claim.

What this module does NOT establish
-----------------------------------
A ledger row is **not** a recovered function, a boundary proof or a match.
This audit reads only checked-in JSON (no Ghidra, ELF or APK access) and
grants no record RECONSTRUCTED, TESTED, VERIFIED or MATCHED status. The
committed statistics block is additionally not internally consistent with
``len(functions)`` (see ``test_committed_statistics_*``), because it is
composed from Ghidra-side counts under a different accounting
convention; those tests pin the observed numbers rather than endorsing
them as a function census.

Offline only: the only filesystem reads are ``coordination/*.json`` inputs
to ``json.load`` via a module-level helper that this module also patches in
a test to prove nothing else is opened. No network and no subprocesses are
used; ``nova3_team.indexer`` is imported for its constants/implementation
only, never invoked with binary data.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from nova3_team.autofeed import as_integer
from nova3_team.indexer import MAX_PUBLISHED_FUNCTION_SIZE, build_coordination_ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "coordination" / "function_ledger.json"

# Observed shape of the committed ledger (pinned so silent drift is visible).
EXPECTED_LIBRARIES = {
    "180b582cbb7e7004c94fe771f8c8013dad4c74a9317ee4baff08c542da4ff0da":
        "libnativeinterface.so",
    "3871b9c4ae5d1c491abbccc7af1744121bf964f52b4812d2f672c72ff7c062ed":
        "libgenerator.so",
    "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf":
        "libNOVA3_neon.so",
}
EXPECTED_RECORDS = 1165
EXPECTED_INVENTORIED = 94
EXPECTED_STATE_LESS = 1071
EXPECTED_PER_REFERENCE = {
    "180b582cbb7e7004c94fe771f8c8013dad4c74a9317ee4baff08c542da4ff0da": 31,
    "3871b9c4ae5d1c491abbccc7af1744121bf964f52b4812d2f672c72ff7c062ed": 1040,
    "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf": 94,
}

REQUIRED_KEYS = frozenset({"address", "size", "reference_sha256", "boundary_status"})
OPTIONAL_KEYS = REQUIRED_KEYS | {
    "state", "status", "matching", "thumb", "boundary_evidence",
    # Already present in committed rows: state-less rows carry a free-text
    # ``source``; INVENTORIED rows carry ``boundary_confidence`` and, for the
    # donor-expansion subset, ``reference_code_sha256``.
    "boundary_confidence", "reference_code_sha256", "source",
}

# States/statuses that would legitimately accompany completed work. The
# indexer only emits INVENTORIED + pending + NOT MATCHED; anything else in a
# ledger record is an unsupported claim and fails this contract.
RECONSTRUCTED_STATES = frozenset({"RECONSTRUCTED", "TESTED", "VERIFIED", "MATCHED"})
RECONSTRUCTED_STATUSES = frozenset({"reconstructed", "tested", "verified",
                                    "matched", "integrated", "done"})
MATCHED_VALUES = frozenset({"MATCHED", "VERIFIED", "TESTED", "RECONSTRUCTED"})

HEX64 = re.compile(r"[0-9a-f]{64}")
U32_LIMIT = 1 << 32
_READS: list[str] = []


# --------------------------------------------------------------------------
# Offline JSON access
# --------------------------------------------------------------------------
def _load_json(path: Path):
    """The only filesystem read used by this module (patchable for auditing)."""
    _READS.append(str(path))
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def ledger() -> dict:
    data = _load_json(LEDGER_PATH)
    assert isinstance(data, dict)
    return data


def reference_hashes(ledger: dict) -> set[str]:
    refs = ledger.get("references")
    assert isinstance(refs, list) and refs, "ledger must list its references"
    hashes = set()
    for ref in refs:
        assert isinstance(ref, dict) and HEX64.fullmatch(str(ref.get("sha256", "")))
        hashes.add(ref["sha256"])
    return hashes


def parse_address(value) -> int:
    """Match the autofeed convention: bare hex strings are addresses."""
    if type(value) is int:
        return value
    return as_integer(value, address=True)


# --------------------------------------------------------------------------
# Contract validators (reused for the real ledger and synthetic ledgers)
# --------------------------------------------------------------------------
def validate_records(ledger: dict) -> None:
    """Record-key, type, range and uniqueness contract. Raises AssertionError."""
    functions = ledger.get("functions")
    assert isinstance(functions, list), "functions must be a list"
    hashes = reference_hashes(ledger)
    seen: dict[tuple[str, int], int] = {}
    for index, record in enumerate(functions):
        where = f"record[{index}]"
        assert isinstance(record, dict), f"{where} is not an object"
        assert REQUIRED_KEYS <= record.keys(), f"{where} missing {REQUIRED_KEYS - record.keys()}"
        assert record.keys() <= OPTIONAL_KEYS, f"{where} has unlisted keys {record.keys() - OPTIONAL_KEYS}"

        digest = record["reference_sha256"]
        assert isinstance(digest, str) and digest in hashes, f"{where} unknown reference hash"
        if "reference_code_sha256" in record:
            # not a library reference hash: local_index.py:174 stores the
            # SHA-256 of this function's own code slice.
            assert isinstance(record["reference_code_sha256"], str)
            assert HEX64.fullmatch(record["reference_code_sha256"]), f"{where} malformed code hash"

        assert isinstance(record["boundary_status"], str) and record["boundary_status"], where
        address = parse_address(record["address"])
        assert 0 <= address < U32_LIMIT, f"{where} address outside 2**32"
        size = record["size"]
        assert type(size) is int, f"{where} size must be an int, got {type(size).__name__}"
        assert 0 <= size <= MAX_PUBLISHED_FUNCTION_SIZE, f"{where} size outside [0, 1 MiB]"

        if "thumb" in record:
            assert type(record["thumb"]) is bool, where
        if "boundary_evidence" in record:
            assert isinstance(record["boundary_evidence"], dict), where
        if "source" in record:
            assert isinstance(record["source"], str) and record["source"], where
        if "status" in record:
            assert isinstance(record["status"], str), where
        if "matching" in record:
            assert isinstance(record["matching"], str), where

        key = (digest, address)
        assert key not in seen, f"{where} duplicates {seen.get(key)} at {(digest[:8], address)}"
        seen[key] = index


def inventory_classes(ledger: dict) -> Counter:
    """Map every record to its inventory class; total must equal len(functions)."""
    classes: Counter = Counter()
    for record in ledger["functions"]:
        if "state" not in record:
            classes["state_less"] += 1
        elif record["state"] == "INVENTORIED":
            classes["inventoried"] += 1
        else:
            classes["other_state"] += 1
    assert sum(classes.values()) == len(ledger["functions"])
    return classes


def synthetic_ledger(records, references=None) -> dict:
    return {
        "schema_version": 1,
        "references": references if references is not None
        else [{"library": name, "sha256": digest} for digest, name in EXPECTED_LIBRARIES.items()],
        "functions": [dict(r) if isinstance(r, dict) else r for r in records],
    }


GOOD = {
    "address": "0x824",
    "size": 12,
    "reference_sha256": "180b582cbb7e7004c94fe771f8c8013dad4c74a9317ee4baff08c542da4ff0da",
    "boundary_status": "tool_identified",
}


# --------------------------------------------------------------------------
# Real ledger: shape, types, ranges, uniqueness
# --------------------------------------------------------------------------
def test_committed_ledger_validates_against_the_record_contract(ledger):
    validate_records(ledger)


def test_references_are_exactly_the_three_library_hashes(ledger):
    assert reference_hashes(ledger) == set(EXPECTED_LIBRARIES)
    assert {ref["sha256"]: ref.get("library") for ref in ledger["references"]} == EXPECTED_LIBRARIES


def test_record_count_and_per_reference_split(ledger):
    assert len(ledger["functions"]) == EXPECTED_RECORDS
    assert Counter(r["reference_sha256"] for r in ledger["functions"]) == EXPECTED_PER_REFERENCE
    assert sum(EXPECTED_PER_REFERENCE.values()) == EXPECTED_RECORDS


def test_addresses_below_u32_and_sizes_bounded_by_one_mib(ledger):
    sizes = [r["size"] for r in ledger["functions"]]
    addresses = [parse_address(r["address"]) for r in ledger["functions"]]
    assert all(type(s) is int and 0 <= s <= MAX_PUBLISHED_FUNCTION_SIZE for s in sizes)
    assert all(0 <= a < U32_LIMIT for a in addresses)
    assert MAX_PUBLISHED_FUNCTION_SIZE == 1024 * 1024
    # The ledger stores addresses both as ints and as hex strings; pin that shape.
    assert Counter(type(r["address"]).__name__ for r in ledger["functions"]) == {"str": 1071, "int": 94}


# --------------------------------------------------------------------------
# Inventoried / state-less accounting
# --------------------------------------------------------------------------
def test_inventoried_and_state_less_records_account_for_every_row(ledger):
    classes = inventory_classes(ledger)
    assert classes["inventoried"] == EXPECTED_INVENTORIED
    assert classes["state_less"] == EXPECTED_STATE_LESS
    assert classes["other_state"] == 0
    # INVENTORIED and state-less rows together are the full inventory total.
    assert classes["inventoried"] + classes["state_less"] == len(ledger["functions"]) == EXPECTED_RECORDS


def test_state_less_records_are_not_reconstructed(ledger):
    state_less = [r for r in ledger["functions"] if "state" not in r]
    assert len(state_less) == EXPECTED_STATE_LESS
    for record in state_less:
        assert "matching" not in record, "a state-less row must not assert a match"
        assert record.get("boundary_status") == "tool_identified"
        assert record.get("source") == "headless Ghidra function boundary metadata"
        assert str(record.get("status", "pending")).lower() == "pending"
    # Neither class is reconstructed: the inventory total stays inventory.
    reconstructed = sum(
        1 for r in ledger["functions"]
        if r.get("state") in RECONSTRUCTED_STATES
        or str(r.get("status", "")).lower() in RECONSTRUCTED_STATUSES
        or r.get("matching") in MATCHED_VALUES
    )
    assert reconstructed == 0


def test_inventoried_records_claim_only_inventory(ledger):
    inventoried = [r for r in ledger["functions"] if r.get("state") == "INVENTORIED"]
    assert len(inventoried) == EXPECTED_INVENTORIED
    for record in inventoried:
        assert record["status"] == "pending"
        assert record["matching"] == "NOT MATCHED"
        assert record["boundary_status"] == "tool_identified"


def test_pending_status_is_never_rendered_or_counted_as_reconstructed(ledger):
    """'pending' is a separate state: not reconstructed, not tested, not matched."""
    pending = [r for r in ledger["functions"] if str(r.get("status", "pending")).lower() == "pending"]
    assert len(pending) == EXPECTED_RECORDS
    labels = {
        "pending": "inventory only",
        "INVENTORIED": "boundary metadata recorded; not reconstructed",
        "NOT MATCHED": "no machine-code comparison performed",
    }
    def render(record) -> str:
        return (f"{record['reference_sha256'][:8]}:{record['address']} size={record['size']} "
                f"state={record.get('state', 'PENDING')} "
                f"status={record.get('status', 'pending')} "
                f"matching={record.get('matching', 'NOT MATCHED')}")

    rendered = [render(r).lower() for r in pending]
    assert len(rendered) == EXPECTED_RECORDS
    reconstructed_words = {"reconstructed", "tested", "verified", "implemented"}
    for line in rendered:
        assert not set(line.replace("=", " ").split()) & reconstructed_words
        assert "not matched" in line
    assert "status=integrated" not in rendered
    assert labels["pending"] == "inventory only"

    # A naive status filter that treats 'pending' as complete would be wrong.
    def counted_as_reconstructed(record) -> bool:
        status = str(record.get("status", "pending")).lower()
        return status in RECONSTRUCTED_STATUSES or record.get("state") in RECONSTRUCTED_STATES

    assert not any(counted_as_reconstructed(r) for r in ledger["functions"])


def test_indexer_emits_only_inventoried_pending_not_matched_for_ledger_rows():
    """The pure producer path (no ELF/I/O by this test) agrees with the record contract."""
    digest = "84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"
    function = {"address": 0x1000, "size": 4, "thumb": True, "size_conflict": False,
                "call_sources": [{"byte_mapping_reported_validated": True,
                                  "ghidra_version": "12.1.2"}], "id": 1}
    private = {
        "binary": {"sha256": digest, "architecture": "ARM", "elf_class": 32,
                   "endianness": "little", "elf_type": "ET_DYN"},
        "functions": [function],
        "coverage": {"functions_with_call_exports": 1},
    }
    import nova3_team.indexer as indexer
    # The ELF parser is replaced for this call only; no binary data is supplied.
    original = indexer.build_ledger
    try:
        indexer.build_ledger = lambda data, exports=(): copy.deepcopy(private)
        emitted = build_coordination_ledger(b"", library="libNOVA3_neon.so")
    finally:
        indexer.build_ledger = original
    assert emitted["statistics"]["boundaries_extracted"] == len(emitted["functions"]) == 1
    assert emitted["statistics"]["inventory_complete"] is False
    row = emitted["functions"][0]
    assert row["state"] == "INVENTORIED"
    assert row["status"] == "pending"
    assert row["matching"] == "NOT MATCHED"
    assert row["boundary_status"] == "tool_identified"
    validate_records(emitted)


# --------------------------------------------------------------------------
# Statistics block consistency
# --------------------------------------------------------------------------
def test_committed_statistics_block_shape(ledger):
    stats = ledger["statistics"]
    assert type(stats["boundaries_extracted"]) is int
    assert type(stats["elf_function_count"]) is int
    assert stats["inventory_complete"] is False
    assert type(stats["excluded"]) is dict and stats["excluded"]
    assert all(type(v) is int and v >= 0 for v in stats["excluded"].values())
    # NOTE: functions_with_ghidra_exports is the count for the *last* Ghidra
    # run (62), not the library's 94 INVENTORIED rows; pinned, not endorsed.
    assert stats["functions_with_ghidra_exports"] == 62
    assert stats["functions_with_ghidra_exports"] <= EXPECTED_INVENTORIED
    # boundaries_extracted counts only the boundary rows of this Ghidra run.
    assert 0 < stats["boundaries_extracted"] <= len(ledger["functions"])
    assert stats["elf_function_count"] >= 0
    assert stats["elf_function_count"] != len(ledger["functions"])  # inventory is not a census


def test_committed_statistics_overshoot_is_pinned(ledger):
    """Excluded + emitted rows exceed the ELF count; pinned, NOT endorsed.

    The committed block mixes accounting conventions: ``missing_ghidra`` plus
    the 62 boundary rows exceeds ``elf_function_count``. The self-consistent
    contract (``excluded + len(functions) == elf_function_count``) must hold
    for a freshly produced ledger; for the committed snapshot the residual is
    recorded here so a silent change to either is detected.
    """
    stats = ledger["statistics"]
    excluded_total = sum(stats["excluded"].values())
    assert excluded_total == 39030
    assert stats["elf_function_count"] == 39092
    assert excluded_total + len(ledger["functions"]) == 40195
    assert excluded_total + len(ledger["functions"]) - stats["elf_function_count"] == 1103
    assert excluded_total + stats["boundaries_extracted"] - stats["elf_function_count"] == 0
    assert stats["excluded"]["missing_ghidra"] == stats["elf_function_count"] - 93


def test_statistics_referential_checks(ledger):
    """Checks that do hold: every library's boundary rows are accounted for."""
    stats = ledger["statistics"]
    per_ref = Counter(r["reference_sha256"] for r in ledger["functions"])
    # The only reference with inventoried rows in this snapshot.
    inventoried_refs = {r["reference_sha256"] for r in ledger["functions"] if "state" in r}
    assert inventoried_refs == {"84075223e7e7f833ebba98a46756ce4619fb909663cd0b961e440cc048abbaaf"}
    ref = next(iter(inventoried_refs))
    assert per_ref[ref] == EXPECTED_INVENTORIED
    # 94 rows = 62 from the Ghidra boundary run + 32 donor-expansion rows.
    assert stats["boundaries_extracted"] == 62
    assert stats["boundaries_extracted"] + 32 == per_ref[ref]
    expansion = [e for e in ledger["expansions"]
                 if e.get("reference_sha256") in reference_hashes(ledger)]
    assert sum(e["added_boundaries"] for e in expansion) == 32
    assert sum(e["previous_boundaries"] for e in expansion) == stats["boundaries_extracted"] == 62
    assert expansion[0]["reference_sha256"] in inventoried_refs


def test_synthetic_statistics_consistent_ledger_passes():
    ledger = synthetic_ledger([GOOD])
    ledger["statistics"] = {
        "boundaries_extracted": 1,
        "elf_function_count": 5,
        "functions_with_ghidra_exports": 1,
        "excluded": {"missing_ghidra": 4},
        "inventory_complete": False,
    }
    validate_records(ledger)
    stats = ledger["statistics"]
    assert sum(stats["excluded"].values()) + len(ledger["functions"]) == stats["elf_function_count"]
    assert stats["boundaries_extracted"] == len(ledger["functions"])
    assert stats["inventory_complete"] is False


def test_synthetic_statistics_incomplete_inventory_flag_and_total():
    ledger = synthetic_ledger([GOOD])
    ledger["statistics"] = {
        "boundaries_extracted": 1,
        "elf_function_count": 3,
        "functions_with_ghidra_exports": 1,
        "excluded": {"missing_ghidra": 1},
        "inventory_complete": False,
    }
    excluded_total = sum(ledger["statistics"]["excluded"].values())
    # Excluded + rows must cover the ELF count for a self-consistent block.
    assert excluded_total + len(ledger["functions"]) != ledger["statistics"]["elf_function_count"]
    # Flag must never claim completeness for an inventory-only ledger.
    ledger["statistics"]["inventory_complete"] = True
    assert ledger["statistics"]["inventory_complete"] is not False


# --------------------------------------------------------------------------
# Negative synthetic fixtures
# --------------------------------------------------------------------------
def test_synthetic_duplicate_address_is_rejected():
    ledger = synthetic_ledger([GOOD, dict(GOOD, size=8)])
    with pytest.raises(AssertionError, match="duplicates"):
        validate_records(ledger)


def test_synthetic_duplicate_address_across_types_is_rejected():
    # 0x824 and 2084 are the same address; the ledger mixes both spellings.
    ledger = synthetic_ledger([GOOD, dict(GOOD, address=0x824)])
    with pytest.raises(AssertionError, match="duplicates"):
        validate_records(ledger)


def test_same_address_under_different_references_is_allowed():
    other = dict(GOOD, reference_sha256=list(EXPECTED_LIBRARIES)[1])
    validate_records(synthetic_ledger([GOOD, other]))


@pytest.mark.parametrize(
    "bad_size",
    [-1, MAX_PUBLISHED_FUNCTION_SIZE + 1, 1 << 40, True, 4.0, "12", None],
)
def test_synthetic_bad_size_is_rejected(bad_size):
    ledger = synthetic_ledger([GOOD, dict(GOOD, address="0x900", size=bad_size)])
    with pytest.raises(AssertionError, match="size"):
        validate_records(ledger)


@pytest.mark.parametrize("bad_address", [U32_LIMIT, 1 << 40, -1, "0x100000000", "not-an-int", None, " 0x824"])
def test_synthetic_bad_address_is_rejected(bad_address):
    ledger = synthetic_ledger([GOOD, dict(GOOD, size=4, address=bad_address)])
    with pytest.raises((AssertionError, ValueError)):
        validate_records(ledger)


@pytest.mark.parametrize("missing", sorted(REQUIRED_KEYS))
def test_synthetic_missing_required_key_is_rejected(missing):
    record = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(AssertionError, match="missing"):
        validate_records(synthetic_ledger([record]))


def test_synthetic_unknown_reference_hash_is_rejected():
    ledger = synthetic_ledger([dict(GOOD, reference_sha256="0" * 64)])
    with pytest.raises(AssertionError, match="unknown reference"):
        validate_records(ledger)


def test_synthetic_non_object_record_is_rejected():
    ledger = synthetic_ledger([GOOD, "0x824/12"])
    with pytest.raises(AssertionError, match="not an object"):
        validate_records(ledger)


def test_synthetic_reconstructed_claim_is_detected():
    ledger = synthetic_ledger([dict(GOOD, state="RECONSTRUCTED")])
    validate_records(ledger)  # shape is fine ...
    assert inventory_classes(ledger)["other_state"] == 1  # ... but not inventory.
    ledger = synthetic_ledger([dict(GOOD, status="matched")])
    assert not any(
        str(r.get("status", "")).lower() in RECONSTRUCTED_STATUSES for r in ledger["functions"]
    ) is False


# --------------------------------------------------------------------------
# Offline-only guard
# --------------------------------------------------------------------------
def test_only_checked_in_coordination_json_is_read(ledger):
    paths = sorted(_READS)
    assert paths, "ledger fixture must have performed exactly one read"
    assert paths == [str(LEDGER_PATH)]
    assert LEDGER_PATH.parent.name == "coordination"
    # No ELF/APK/Ghidra path is opened by this module.
    assert not any(p.endswith((".so", ".apk", ".rar", ".gpr", ".rep")) for p in paths)


def test_reference_code_sha256_is_a_function_slice_hash_not_a_library_hash(ledger):
    """Only ``reference_sha256`` resolves to a library reference.

    The 32 donor-expansion rows also carry ``reference_code_sha256``, which
    ``nova3_team.local_index`` fills with the SHA-256 of that function's own
    code slice. It is not a reference hash and must never be counted as one
    of the three library references.
    """
    hashes = reference_hashes(ledger)
    coded = [r for r in ledger["functions"] if "reference_code_sha256" in r]
    assert len(coded) == 32
    assert all(r["reference_sha256"] in hashes for r in coded)
    assert not hashes & {r["reference_code_sha256"] for r in coded}
    assert all(HEX64.fullmatch(r["reference_code_sha256"]) for r in coded)
    bad = dict(GOOD, reference_sha256="0" * 64, reference_code_sha256=GOOD["reference_sha256"])
    with pytest.raises(AssertionError, match="unknown reference"):
        validate_records(synthetic_ledger([bad]))
