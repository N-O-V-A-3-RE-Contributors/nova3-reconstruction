"""Offline regression tests for ``dsh_planner._ledger_view`` only (task #282).

``_ledger_view`` (``nova3_team/dsh_planner.py``) reads
``<root>/coordination/function_ledger.json`` and summarises it for the
planner's evidence line ("ledger: N entries, M unassigned").

This module characterises that *counting contract* against temporary
synthetic ledgers only:

* ``total`` is ``len(functions)`` exactly as the file supplies it (non-dict
  records included in ``total`` but skipped by the tally and the count);
* an entry counts as unassigned when its status, lowercased by
  ``str(status).lower()``, is ``pending``, ``queued`` or the empty string
  (case-insensitive; not whitespace-stripped). Only an *absent* ``status`` key
  defaults to ``pending``; an explicit JSON ``null`` becomes ``"none"`` and is
  therefore **not** counted -- a 1165-record ledger with one explicit status
  and 1164 statusless records reads "1165 entries, 1164 unassigned". Any
  other status -- ``done``,
  ``claimed``, ``INVENTORIED``, etc. -- does not count;
* the library tally groups by ``reference_sha256`` mapped through
  ``references[]`` and reports ``"?"`` for unknown or missing hashes; a known
  hash whose reference entry has no ``library`` label is labelled ``"None"``;
* an unreadable file or a non-list ``functions`` value yields the documented
  zero view (``LedgerView(0, 0, (), False, note)``) with its reason string,
  and raises nothing. *Known gap, not part of that contract:* a JSON
  ``null`` in ``references`` raises ``TypeError`` (see
  ``LedgerViewMalformedReferencesTests``).

Scope: the tests say nothing about task assignment or worker ownership. A
status of ``INVENTORIED`` is still an unfilled ledger state, not evidence that
a worker owns or has started the record.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nova3_team import dsh_planner

UNREADABLE = "function ledger unreadable"
NO_ARRAY = "function ledger has no functions array"

# Sentinel for "the status key is absent from the record".
_MISSING = object()

# Awkward-but-legal temporary root name (space and parentheses).
_AWKWARD_NAME = "root (synthetic)"


def _write_ledger(root, payload) -> Path:
    """Write ``payload`` as ``<root>/coordination/function_ledger.json``."""
    ledger_dir = Path(root) / "coordination"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / "function_ledger.json"
    if isinstance(payload, (bytes, bytearray)):
        path.write_bytes(payload)
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _view(payload):
    """Run ``_ledger_view`` against a throwaway root holding ``payload``."""
    with tempfile.TemporaryDirectory(prefix="ledger-view-") as tmp:
        root = os.path.join(tmp, _AWKWARD_NAME)
        _write_ledger(root, payload)
        return dsh_planner._ledger_view(root)


def _record(status, sha="h"):
    if status is _MISSING:
        return {"reference_sha256": sha}
    return {"reference_sha256": sha, "status": status}


def _counts(view) -> dict:
    return dict(view.by_library)


class LedgerViewTotalTests(unittest.TestCase):
    def test_total_equals_len_functions(self):
        data = {
            "functions": (
                [{"reference_sha256": "a", "status": "done"}] * 3
                + [{"reference_sha256": "b"}] * 2
                + [{"reference_sha256": "c", "status": "claimed"}] * 4
            ),
            "references": [],
        }
        view = _view(data)
        self.assertEqual(view.total, len(data["functions"]))
        self.assertEqual(view.total, 9)

    def test_empty_functions_list_is_an_ok_zero_view(self):
        view = _view({"functions": [], "references": []})
        self.assertEqual(view.total, 0)
        self.assertEqual(view.unassigned, 0)
        self.assertEqual(view.by_library, ())
        self.assertTrue(view.checked_in)

    def test_total_is_independent_of_status_and_references(self):
        # The total counts records, not states or mappings.
        functions = [{} for _ in range(1165)]
        view = _view({"functions": functions, "references": [],
                      "other": "ignored"})
        self.assertEqual(view.total, len(functions))
        self.assertEqual(view.total, 1165)
        self.assertEqual(sum(_counts(view).values()), view.total)

    def test_non_dict_records_count_in_total_but_not_in_tally(self):
        # total is len(functions) verbatim; the loop skips non-dict records, so
        # the library tally can sum to less than total on a malformed ledger.
        view = _view({"functions": [{"reference_sha256": "a"}, "a", 7, None],
                      "references": []})
        self.assertEqual(view.total, 4)
        self.assertEqual(counts := _counts(view), {"?": 1})
        self.assertEqual(sum(counts.values()), 1)


class LedgerViewUnassignedTests(unittest.TestCase):
    def _unassigned_for(self, *statuses):
        payload = [_record(status) for status in statuses]
        view = _view({"functions": payload, "references": []})
        self.assertEqual(view.total, len(payload))
        return view.unassigned

    def test_missing_empty_pending_and_queued_are_unassigned(self):
        for status in ("pending", "PENDING", "Pending", "queued", "QUEUED",
                       "Queued", "qUeUeD", "", _MISSING):
            with self.subTest(status=repr(status)):
                self.assertEqual(self._unassigned_for(status), 1)

    def test_null_is_a_non_empty_literal_not_a_default(self):
        # `str(None).lower()` is "none", i.e. *not* empty and not one of the
        # listed words, so an explicit JSON null is NOT counted as unassigned.
        # (Pinned implementation detail of dsh_planner.py:228, not a claim
        # that null ought to mean something else.)
        self.assertEqual(self._unassigned_for(None), 0)
        self.assertEqual(self._unassigned_for(None, "pending", "queued", ""), 3)

    def test_absent_key_defaults_to_pending(self):
        # The default is applied to a *missing* key only; records without a
        # status are counted as unassigned (this is the shape of the
        # "1165 entries, 1165 unassigned" planner evidence line).
        self.assertEqual(self._unassigned_for(_MISSING, "claimed"), 1)

    def test_case_insensitive_only(self):
        # str(length-1).lower() -- surrounding whitespace is not stripped.
        self.assertEqual(self._unassigned_for("pending "), 0)
        self.assertEqual(self._unassigned_for("queued\t"), 0)

    def test_any_other_status_is_not_unassigned(self):
        for status in ("done", "DONE", "Done", "claimed", "CLAIMED",
                       "INVENTORIED", "inventoried", "in_review", "blocked",
                       "finished", "none", "null", "queued-done",
                       "pending,queued", "0", "false"):
            with self.subTest(status=repr(status)):
                self.assertEqual(self._unassigned_for(status), 0)

    def test_mixed_ledger_counts_each_record_once(self):
        data = {
            "functions": [
                {"reference_sha256": "a", "status": "pending"},
                {"reference_sha256": "a", "status": "done"},
                {"reference_sha256": "a"},                    # missing status
                {"reference_sha256": "b", "status": "Queued"},
                {"reference_sha256": "b", "status": "claimed"},
                {"reference_sha256": "b", "status": "INVENTORIED"},
                {"reference_sha256": "b", "status": None},
                {"reference_sha256": "b", "status": ""},
            ],
            "references": [],
        }
        view = _view(data)
        self.assertEqual(view.total, len(data["functions"]))
        self.assertEqual(view.total, 8)
        self.assertEqual(view.unassigned, 4)  # records 1, 3, 4 and 8

    def test_unassigned_never_exceeds_total(self):
        for statuses in ([], ["done"], ["pending", "done"],
                         ["pending"] * 5, [None, "claimed", "QUEUED"]):
            with self.subTest(statuses=statuses):
                view = _view({"functions": [{"status": s} for s in statuses],
                              "references": []})
                self.assertLessEqual(view.unassigned, view.total)

    def test_absent_status_counts_as_unassigned(self):
        # The checked-in ledger supplies no status, so this is the shape the
        # "1165 entries, 1165 unassigned" evidence line is computed from.
        functions = [{"reference_sha256": "sha-%d" % (i % 3)}
                     for i in range(94)]
        view = _view({"functions": functions, "references": []})
        self.assertEqual(view.total, 94)
        self.assertEqual(view.unassigned, 94)


class LedgerViewLibraryTallyTests(unittest.TestCase):
    def test_groups_by_mapped_reference_hash(self):
        data = {
            "functions": [
                {"reference_sha256": "sha-a"},
                {"reference_sha256": "sha-a"},
                {"reference_sha256": "sha-a", "status": "done"},
                {"reference_sha256": "sha-b"},
                {"reference_sha256": "sha-b", "status": "pending"},
            ],
            "references": [
                {"sha256": "sha-a", "library": "lib/alpha.so"},
                {"sha256": "sha-b", "library": "lib/beta.so"},
            ],
        }
        view = _view(data)
        self.assertEqual(_counts(view), {"lib/alpha.so": 3, "lib/beta.so": 2})
        self.assertEqual(view.by_library,
                         (("lib/alpha.so", 3), ("lib/beta.so", 2)))

    def test_unknown_missing_or_null_hash_reports_question_mark(self):
        data = {
            "functions": [
                {"reference_sha256": "sha-unknown"},  # absent from references[]
                {},                                   # no hash at all
                {"status": "done"},                   # no hash, real status
                {"reference_sha256": None},           # null hash
                {"reference_sha256": "sha-a"},
            ],
            "references": [{"sha256": "sha-a", "library": "lib/alpha.so"}],
        }
        view = _view(data)
        self.assertEqual(_counts(view), {"?": 4, "lib/alpha.so": 1})
        self.assertEqual(view.total, 5)

    def test_reference_without_library_label_is_labelled_none(self):
        # A *known* hash whose reference entry omits "library" does not fall
        # back to "?": str(None) == "None". "?" is reserved for hashes that do
        # not map at all. Pinned as observed at dsh_planner.py:219-226.
        data = {
            "functions": [{"reference_sha256": "sha-a"}],
            "references": [{"sha256": "sha-a"}],
        }
        self.assertEqual(_counts(_view(data)), {"None": 1})

    def test_reference_labels_are_stringified(self):
        data = {
            "functions": [{"reference_sha256": "7"}, {"reference_sha256": 8}],
            "references": [{"sha256": 7, "library": 99}],
        }
        self.assertEqual(_counts(_view(data)), {"99": 1, "?": 1})

    def test_non_dict_reference_entries_are_ignored(self):
        data = {
            "functions": [{"reference_sha256": "sha-a"}],
            "references": ["sha-a", 3, None, {"sha256": "sha-a",
                                              "library": "lib/a.so"}],
        }
        self.assertEqual(_counts(_view(data)), {"lib/a.so": 1})

    def test_tally_is_sorted_and_sums_to_total(self):
        data = {
            "functions": [
                {"reference_sha256": "sha-top"},
                {"reference_sha256": "sha-aaa"},
                {"reference_sha256": "sha-mid"},
                {"reference_sha256": "sha-mid"},
                {"reference_sha256": "sha-missing"},
            ],
            "references": [
                {"sha256": "sha-mid", "library": "lib/mid.so"},
                {"sha256": "sha-top", "library": "lib/topper.so"},
                {"sha256": "sha-aaa", "library": "lib/aaa.so"},
            ],
        }
        view = _view(data)
        labels = [name for name, _ in view.by_library]
        self.assertEqual(labels, sorted(labels))
        self.assertEqual(_counts(view),
                         {"?": 1, "lib/aaa.so": 1, "lib/mid.so": 2,
                          "lib/topper.so": 1})
        self.assertEqual(sum(_counts(view).values()), view.total)

    def test_tally_is_independent_of_unassigned_count(self):
        data = {
            "functions": [{"reference_sha256": "sha-a", "status": "done"},
                          {"reference_sha256": "sha-a", "status": "pending"}],
            "references": [{"sha256": "sha-a", "library": "lib/a.so"}],
        }
        view = _view(data)
        self.assertEqual(_counts(view), {"lib/a.so": 2})
        self.assertEqual(view.unassigned, 1)


class _ZeroViewAssertions:
    """Mixin: every fallback must return the documented view, not raise."""

    def assert_zero_view(self, view, note):
        self.assertEqual(view, dsh_planner.LedgerView(0, 0, (), False, note))
        self.assertEqual(view.total, 0)
        self.assertEqual(view.unassigned, 0)
        self.assertEqual(view.by_library, ())
        self.assertFalse(view.checked_in)
        self.assertEqual(view.note, note)


class LedgerViewUnreadableTests(_ZeroViewAssertions, unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory(prefix="ledger-missing-") as tmp:
            view = dsh_planner._ledger_view(tmp)
        self.assert_zero_view(view, UNREADABLE)

    def test_coordination_directory_without_ledger_file(self):
        with tempfile.TemporaryDirectory(prefix="ledger-missing-") as tmp:
            (Path(tmp) / "coordination").mkdir()  # present but empty
            view = dsh_planner._ledger_view(tmp)
        self.assert_zero_view(view, UNREADABLE)

    def test_invalid_json(self):
        self.assert_zero_view(_view(b"{not json"), UNREADABLE)

    def test_empty_file(self):
        self.assert_zero_view(_view(b""), UNREADABLE)

    def test_non_utf8_bytes(self):
        self.assert_zero_view(_view(b'{"functions": [\xff\xfe]}'), UNREADABLE)

    def test_directory_where_the_file_is_expected(self):
        with tempfile.TemporaryDirectory(prefix="ledger-dir-") as tmp:
            (Path(tmp) / "coordination" /
             "function_ledger.json").mkdir(parents=True)
            view = dsh_planner._ledger_view(tmp)
        self.assert_zero_view(view, UNREADABLE)

    def test_permission_error_is_swallowed(self):
        def denied(_self, *_args, **_kwargs):
            raise PermissionError(13, "Permission denied")

        with tempfile.TemporaryDirectory(prefix="ledger-perm-") as tmp:
            _write_ledger(tmp, {"functions": [{"status": "done"}]})
            with mock.patch.object(Path, "read_text", denied):
                view = dsh_planner._ledger_view(tmp)
        self.assert_zero_view(view, UNREADABLE)


class LedgerViewNoArrayTests(_ZeroViewAssertions, unittest.TestCase):
    def test_non_list_or_absent_functions(self):
        payloads = (
            {"functions": None},
            {"functions": {}},
            {"functions": "pending"},
            {"functions": 42},
            {"functions": False},
            {"references": []},
            [],
            "pending",
            42,
            None,
        )
        for payload in payloads:
            with self.subTest(payload=repr(payload)):
                self.assert_zero_view(_view(payload), NO_ARRAY)


class LedgerViewMalformedReferencesTests(unittest.TestCase):
    """``references: null`` is NOT guarded (dsh_planner.py:219).

    Known uncovered edge, recorded as a regression tripwire rather than a
    contract: the comprehension iterates ``data.get('references', [])``
    unchanged, so a JSON ``null`` there raises TypeError even when
    ``functions`` is a valid list -- a later fix should turn this test into an
    assertion that the tally falls back to all-``"?"``. The task scope forbids
    editing ``dsh_planner.py``; reported in the handoff.
    """

    def test_null_references_raise_type_error(self):
        data = {"functions": [{"reference_sha256": "sha-a"}],
                "references": None}
        with self.assertRaises(TypeError):
            _view(data)

    def test_absent_references_key_is_treated_as_empty(self):
        view = _view({"functions": [{"reference_sha256": "sha-a"}] * 3})
        self.assertEqual(_counts(view), {"?": 3})


class LedgerViewIsolationTests(unittest.TestCase):
    """The function must read only the root it is given, and only read it."""

    def test_reads_only_the_temporary_ledger(self):
        real_ledger = (Path(dsh_planner.__file__).resolve().parent.parent
                       / "coordination" / "function_ledger.json")
        reads = []
        original = Path.read_text

        def record_read(path_self, *args, **kwargs):
            reads.append(Path(os.fspath(path_self)).resolve())
            return original(path_self, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="ledger-guard-") as tmp:
            _write_ledger(tmp, {"functions": [{"status": "done"}]})
            target = (Path(tmp) / "coordination" /
                      "function_ledger.json").resolve()
            with mock.patch.object(Path, "read_text", record_read):
                view = dsh_planner._ledger_view(tmp)

        self.assertEqual(view.total, 1)
        self.assertEqual(view.unassigned, 0)
        self.assertEqual(reads, [target])
        self.assertNotIn(real_ledger.resolve(), reads)

    def test_does_not_modify_the_ledger(self):
        payload = {"functions": [{"status": "done"}], "references": []}
        with tempfile.TemporaryDirectory(prefix="ledger-ro-") as tmp:
            path = _write_ledger(tmp, payload)
            before = path.read_bytes()
            dsh_planner._ledger_view(tmp)
            self.assertEqual(path.read_bytes(), before)

if __name__ == "__main__":
    unittest.main()
