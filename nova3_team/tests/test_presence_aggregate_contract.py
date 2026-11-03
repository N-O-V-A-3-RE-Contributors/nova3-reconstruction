"""Offline contract tests for nova3_team.presence.aggregate only.

Only ``presence.aggregate`` is exercised, and only with synthetic reporter
objects whose ``snapshot`` is a recording fake or raises. No ``Reporter`` is
constructed, no provider is launched, and network, subprocess, filesystem and
clock entry points are replaced with guards that fail the test if touched.
These tests characterize aggregation (row ordering, fault isolation, exact
passthrough). They say nothing about whether any worker is online.
"""
from __future__ import annotations

import copy
import unittest
from unittest import mock

from nova3_team import presence
from nova3_team.presence import aggregate


class FakeReporter:
    """Synthetic stand-in: records every access and returns a fixed snapshot."""

    def __init__(self, row, *, name='reporter'):
        self.row = row
        self.name = name
        self.calls = 0
        self.seen = []

    def snapshot(self):
        self.calls += 1
        self.seen.append(object())  # every call returns a distinct row object
        return copy.deepcopy(self.row)  # copies like a real snapshot dict


class RaisingReporter:
    def __init__(self, error, *, name='raising'):
        self.error = error
        self.name = name
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        raise self.error


class CountingSequence:
    """Single-pass-only reporter sequence.

    Item access is counted (``items_visited``). The sequence may be read at
    most ``len(reporters)`` times in total: once the iterator has been
    exhausted, any further __getitem__ (a second loop, a materialising or
    filtering pass, a re-read) fails the test. Slicing, membership scans and
    eager materialisation via length hint are rejected too.
    """

    def __init__(self, items):
        self.items = list(items)
        self.items_visited = 0
        self.exhausted = False

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        if isinstance(index, slice):
            raise AssertionError('aggregate must not slice the reporter sequence')
        if not isinstance(index, int):
            raise AssertionError(f'non-integer index {index!r}')
        if self.exhausted:
            raise AssertionError('aggregate read reporters after the single pass')
        if index < 0 or index >= len(self.items):
            raise IndexError(index)
        self.items_visited += 1
        if self.items_visited > len(self.items):
            raise AssertionError('aggregate bound reporters more than once')
        return self.items[index]

    def __iter__(self):
        return SequenceIterator(self)

    def __contains__(self, value):
        raise AssertionError('aggregate must not run a membership scan')


class SequenceIterator:
    """Single-use iterator; a second pass or a length hint fails the test."""

    def __init__(self, sequence):
        self.sequence = sequence
        self.position = 0
        self.loops = 0

    def __iter__(self):
        self.loops += 1
        if self.loops > 1:
            raise AssertionError('aggregate must not pass over reporters twice')
        return self

    def __length_hint__(self):
        # Reached only if the reporter sequence is eagerly materialised.
        raise AssertionError('reporters were materialised instead of streamed')

    def __next__(self):
        if self.position >= len(self.sequence.items):
            self.sequence.exhausted = True
            raise StopIteration
        value = self.sequence[self.position]
        self.position += 1
        return value


# Process-wide audit backstop behind the explicit mock.patch guards below.
# It only fires while one of this module's tests is running (setUp counter), so
# it cannot disturb other test modules in a shared pytest/unittest process.
_guard_depth = 0


def _audit_hook(event, args):
    if _guard_depth and event in ('open', 'subprocess.Popen', 'os.system',
                                  'socket.connect', 'socket.__new__'):
        raise AssertionError(f'unexpected side effect during aggregate: {event}')


_audit_armed = False


class _NoEnviron:
    def __getitem__(self, key):
        raise AssertionError(f'environment read: {key!r}')

    def get(self, key, default=None):
        raise AssertionError(f'environment read: {key!r}')


class AggregateContract(unittest.TestCase):
    """Offline guards plus the aggregate row contract."""

    def setUp(self):
        global _audit_armed, _guard_depth
        if not _audit_armed:
            import sys
            sys.addaudithook(_audit_hook)
            _audit_armed = True

        def release_guard():
            global _guard_depth
            _guard_depth -= 1

        _guard_depth += 1
        self.addCleanup(release_guard)
        for target, attr in (
                ('socket.socket', 'network socket creation'),
                ('socket.create_connection', 'network connection'),
                ('subprocess.run', 'subprocess execution'),
                ('subprocess.Popen', 'subprocess spawn'),
                ('os.system', 'shell execution'),
                ('builtins.open', 'file access')):
            patcher = mock.patch(target, side_effect=AssertionError(attr))
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- ordering -----------------------------------------------------------

    def test_slot_ordering_is_by_string_value(self):
        rows = [
            FakeReporter({'slot': 'b', 'n': 1}),
            FakeReporter({'slot': 'A', 'n': 2}),
            FakeReporter({'slot': 'a', 'n': 3}),
            FakeReporter({'slot': '', 'n': 4}),
            FakeReporter({'slot': 'B', 'n': 5}),
        ]
        result = aggregate(rows)
        self.assertEqual([row['slot'] for row in result], ['', 'A', 'B', 'a', 'b'])

    def test_numeric_slots_sort_as_strings_not_numbers(self):
        values = (10, 9, 100, 2, 1)
        result = aggregate([FakeReporter({'slot': value, 'tag': value})
                            for value in values])
        # str() ordering: '1' < '10' < '100' < '2' < '9', not numeric order.
        self.assertEqual([row['tag'] for row in result], [1, 10, 100, 2, 9])

    def test_mixed_type_slots_sort_by_str(self):
        values = [None, -1, 2.5, '2', 2, True, b'x', ('t',)]
        result = aggregate([FakeReporter({'slot': value, 'id': index})
                            for index, value in enumerate(values)])
        slots = [row['slot'] for row in result]
        expected = sorted(values, key=str)
        self.assertEqual(slots, expected)
        # str() keys are total and deterministic here, so this is exactly the
        # order reproduce_aggregate (the reference oracle) must return too.
        self.assertEqual(slots, [('t',), -1, '2', 2, 2.5, None, True, b'x'])
        # Booleans compare equal to ints, so check identity of each value.
        self.assertIs(slots[3], result[3]['slot'])
        self.assertIsInstance(slots[3], int)
        self.assertNotIsInstance(slots[3], bool)
        self.assertIs(slots[2], '2')

    def test_str_sorting_is_stable_for_equal_string_keys(self):
        # 2 and '2' both stringify to '2'; ties keep input order (stable sort).
        result = aggregate([FakeReporter({'slot': '2', 'id': 's'}),
                            FakeReporter({'slot': 2, 'id': 'i'})])
        self.assertEqual([row['id'] for row in result], ['s', 'i'])
        result = aggregate([FakeReporter({'slot': 2, 'id': 'i'}),
                            FakeReporter({'slot': '2', 'id': 's'})])
        self.assertEqual([row['id'] for row in result], ['i', 's'])

    def test_missing_slot_sorts_as_empty_string(self):
        reporters = [FakeReporter({'slot': 'a', 'id': 'a'}),
                     FakeReporter({'slot': 'b', 'id': 'b'}),
                     FakeReporter({'id': 'missing'})]
        result = aggregate(reporters)
        self.assertEqual([row['id'] for row in result], ['missing', 'a', 'b'])
        self.assertNotIn('slot', result[0])

    def test_none_slot_sorts_as_string_none_not_missing(self):
        result = aggregate([FakeReporter({'slot': 'a'}), FakeReporter({'slot': None})])
        self.assertEqual(result[0], {'slot': None})
        self.assertEqual(result[1], {'slot': 'a'})

    # -- fault isolation ----------------------------------------------------

    def test_exception_removes_only_that_row(self):
        before = FakeReporter({'slot': 'a', 'id': 1})
        broken = RaisingReporter(RuntimeError('snapshot failed'))
        after = FakeReporter({'slot': 'b', 'id': 2})
        result = aggregate([before, broken, after])
        self.assertEqual(result, [{'slot': 'a', 'id': 1}, {'slot': 'b', 'id': 2}])
        self.assertEqual(broken.calls, 1)
        self.assertEqual((before.calls, after.calls), (1, 1))

    def test_later_reporters_contribute_after_early_failures(self):
        bad = [RaisingReporter(ValueError('v')), RaisingReporter(KeyError('k')),
               RaisingReporter(Exception('plain'))]
        good = [FakeReporter({'slot': f'slot-{index}'}) for index in range(3)]
        result = aggregate(bad + good)
        self.assertEqual([row['slot'] for row in result],
                         ['slot-0', 'slot-1', 'slot-2'])
        self.assertEqual([reporter.calls for reporter in bad], [1, 1, 1])

    def test_all_raising_reporters_yield_empty_list(self):
        self.assertEqual(aggregate([RaisingReporter(RuntimeError('x')),
                                    RaisingReporter(RuntimeError('y'))]), [])

    def test_non_callable_snapshot_attributes_are_ignored(self):
        class Missing:
            name = 'missing'

        class NoneSnapshot:
            snapshot = None

        class StringSnapshot:
            snapshot = 'not callable'

        class FakeInt:
            def __call__(self, *args, **kwargs):
                raise AssertionError('snapshot attribute was invoked')

        class IntegerSnapshot:
            snapshot = FakeInt()

        class CallableSnapshot:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                return {'slot': 'callable'}

        callable_attr = CallableSnapshot()

        class ObjectSnapshot:
            snapshot = callable_attr

        good = FakeReporter({'slot': 'good'})
        result = aggregate([Missing(), NoneSnapshot(), StringSnapshot(),
                            IntegerSnapshot(), good, ObjectSnapshot()])
        # 'callable' < 'good'; the CallableSnapshot object is *not* called.
        self.assertEqual(result, [{'slot': 'callable'}, {'slot': 'good'}])
        self.assertEqual(good.calls, 1)
        self.assertEqual(callable_attr.calls, 1)

    def test_property_snapshot_is_evaluated_by_getattr_and_escape_propagates(self):
        # getattr() evaluates a property before the callable() check; an
        # exception raised there is not inside the try block and propagates.
        class PropertySnapshot:
            def __init__(self):
                self.reads = 0

            @property
            def snapshot(self):
                self.reads += 1
                raise AssertionError('property exploded')

        reporter = PropertySnapshot()
        with self.assertRaisesRegex(AssertionError, 'property exploded'):
            aggregate([reporter])
        self.assertEqual(reporter.reads, 1)

    def test_raising_snapshot_is_not_retried(self):
        broken = RaisingReporter(RuntimeError('once'))
        aggregate([broken, FakeReporter({'slot': 'x'})])
        self.assertEqual(broken.calls, 1)

    # -- exact passthrough --------------------------------------------------

    def test_result_contains_exactly_the_snapshot_keys_and_values(self):
        rows = [
            {'worker': 'w1', 'slot': 'slot-1', 'provider': 'codex', 'state': 'WORKING',
             'issue': 285, 'machine': None, 'extra': {'nested': [1, 2, 3]}},
            {'slot': 'slot-0', 'provider': 'claude', 'state': 'IDLE', 'issue': None},
        ]
        result = aggregate([FakeReporter(rows[0]), FakeReporter(rows[1])])
        self.assertEqual(result, [rows[1], rows[0]])
        for returned in result:
            self.assertIsInstance(returned, dict)
        self.assertEqual([set(row) for row in result], [set(rows[1]), set(rows[0])])
        self.assertEqual(result[1]['extra'], {'nested': [1, 2, 3]})
        self.assertEqual(result[1]['extra'], rows[0]['extra'])
        self.assertEqual(result[1]['worker'], 'w1')
        self.assertEqual(result[1]['machine'], None)

    def test_nested_values_are_passed_through_by_value(self):
        payload = {'slot': 's', 'log': [{'level': 'info'}]}
        result = aggregate([FakeReporter(payload)])
        self.assertEqual(result[0]['log'], payload['log'])

    def test_rows_are_the_objects_returned_by_snapshot(self):
        # FakeReporter copies its row, so identity is not the caller's dict.
        factory = FakeReporter({'slot': 'z'})
        result = aggregate([factory])
        self.assertEqual(len(factory.seen), 1)
        self.assertIsNot(result[0], factory.row)
        self.assertEqual(result[0], factory.row)

    def test_equal_multisets_produce_equal_output_regardless_of_input_order(self):
        specs = [{'slot': 'slot-2', 'state': 'WORKING'},
                 {'slot': 'slot-1', 'state': 'IDLE'},
                 {'slot': 'slot-10', 'state': 'ERROR'},
                 {'state': 'ONLINE'}]
        expected = aggregate([FakeReporter(row) for row in specs])
        for order in (specs, list(reversed(specs)),
                      [specs[2], specs[0], specs[3], specs[1]]):
            self.assertEqual(aggregate([FakeReporter(row) for row in order]), expected)
        self.assertEqual([row['slot'] for row in expected[1:]],
                         ['slot-1', 'slot-10', 'slot-2'])

    def test_duplicate_slots_keep_all_rows(self):
        result = aggregate([FakeReporter({'slot': 'same', 'id': 1}),
                            FakeReporter({'slot': 'same', 'id': 2})])
        self.assertEqual([row['id'] for row in result], [1, 2])

    def test_does_not_mutate_input_rows(self):
        rows = [{'slot': 'b', 'id': 2}, {'slot': 'a', 'id': 1}]
        result = aggregate([FakeReporter(rows[0]), FakeReporter(rows[1])])
        self.assertEqual([row['id'] for row in result], [1, 2])
        self.assertEqual(rows, [{'slot': 'b', 'id': 2}, {'slot': 'a', 'id': 1}])

    def test_tuple_input_is_accepted(self):
        result = aggregate((FakeReporter({'slot': 'b'}), FakeReporter({'slot': 'a'})))
        self.assertEqual(result, [{'slot': 'a'}, {'slot': 'b'}])

    def test_empty_inputs(self):
        self.assertEqual(aggregate([]), [])
        self.assertEqual(aggregate(()), [])

    # -- single pass --------------------------------------------------------

    def test_loop_is_single_pass_over_reporters(self):
        items = [FakeReporter({'slot': f'slot-{index}'}) for index in range(4)]
        sequence = CountingSequence(items)
        result = aggregate(sequence)
        self.assertEqual(len(result), len(items))
        self.assertEqual(sequence.items_visited, len(items))
        self.assertLessEqual(sequence.items_visited, len(sequence))
        self.assertEqual([reporter.calls for reporter in items], [1, 1, 1, 1])

    def test_single_pass_with_failures_and_non_callables(self):
        class Missing:
            pass

        items = [RaisingReporter(ValueError('x')), FakeReporter({'slot': 'b'}),
                 Missing(), RaisingReporter(OSError('y')),
                 FakeReporter({'slot': 'a'})]
        sequence = CountingSequence(items)
        result = aggregate(sequence)
        self.assertEqual([row['slot'] for row in result], ['a', 'b'])
        self.assertEqual(sequence.items_visited, len(items))
        self.assertLessEqual(sequence.items_visited, len(sequence))
        # Each snapshot is called exactly once; Missing has no snapshot at all.
        self.assertEqual([getattr(item, 'calls', None) for item in items],
                         [1, 1, None, 1, 1])

    # -- no network, subprocess, file access or Reporter --------------------

    def test_no_network_subprocess_or_reporter_construction(self):
        built = []

        def forbid(*args, **kwargs):
            built.append((args, kwargs))
            raise AssertionError('Reporter must not be constructed')

        with (mock.patch.object(presence, 'Reporter', side_effect=forbid),
              mock.patch.object(presence, 'cmd',
                                side_effect=AssertionError('gh called'))):
            result = aggregate([FakeReporter({'slot': 'b'}), FakeReporter({'slot': 'a'})])
        self.assertEqual(result, [{'slot': 'a'}, {'slot': 'b'}])
        self.assertEqual(built, [])

    def test_aggregate_does_not_touch_clock_uuid_or_environment(self):
        with (mock.patch.object(presence.time, 'time',
                                side_effect=AssertionError('clock')),
              mock.patch.object(presence.uuid, 'uuid4',
                                side_effect=AssertionError('uuid')),
              mock.patch.object(presence.os, 'environ', new=_NoEnviron())):
            result = aggregate([FakeReporter({'slot': 'x', 'issue': 285})])
        self.assertEqual(result, [{'slot': 'x', 'issue': 285}])


class AggregateProhibitedInputs(unittest.TestCase):
    """Characterize non-dict snapshots. Callable fakes only, no I/O."""

    def test_non_dict_row_always_raises_from_sort_key(self):
        # The sort key calls row.get('slot', '') for every row, dict or not;
        # list.sort computes all keys before comparing, so even a lone row
        # raises AttributeError and nothing is returned.
        with self.assertRaises(AttributeError):
            aggregate([FakeReporter(['not', 'a', 'dict'])])
        with self.assertRaises(AttributeError):
            aggregate([FakeReporter(['not']), FakeReporter({})])
        with self.assertRaises(AttributeError):
            aggregate([FakeReporter({}), FakeReporter(['not'])])

    def test_none_row_raises_from_sort_key(self):
        # None returned by snapshot() does not raise, so it is kept until the
        # sort key runs and raises AttributeError (the exception is not
        # swallowed, and raising reporters do not hide it).
        with self.assertRaises(AttributeError):
            aggregate([FakeReporter(None)])
        with self.assertRaises(AttributeError):
            aggregate([FakeReporter({'slot': 'm'}), FakeReporter(None)])
        with self.assertRaises(AttributeError):
            aggregate([RaisingReporter(RuntimeError('x')), FakeReporter(None)])

    def test_non_iterable_reporters_raises_type_error(self):
        with self.assertRaises(TypeError):
            aggregate(None)


def reproduce_aggregate(reporters):
    """Independent reference for presence.aggregate (presence.py:199-210).

    Written from the documented contract, not by importing aggregate: take
    callable snapshot() rows, drop rows whose snapshot raises, sort by
    str(row.get('slot', '')). The fake snapshots return copies of plain dicts.
    """
    rows = []
    for reporter in reporters:
        method = getattr(reporter, 'snapshot', None)
        if not callable(method):
            continue
        try:
            row = method()
        except Exception:
            continue
        rows.append(row)
    rows.sort(key=lambda row: str(row.get('slot', '')))
    return rows


class AggregateReferenceEquivalence(unittest.TestCase):
    """Differential check of aggregate against a plain reference."""

    CASES = (
        [],
        [{'slot': 'b'}, {'slot': 'a'}],
        [{'slot': 10}, {'slot': 9}, {'slot': 100}],
        [{'slot': None}, {}, {'slot': ''}, {'slot': 0}],
        [{'slot': '2'}, {'slot': 2}, {'slot': True}, {'slot': 1.0}],
        [{'slot': 'z', 'issue': 285}, {'slot': 'z', 'issue': None}, {'issue': 1}],
    )

    def test_matches_reference_on_dict_rows(self):
        for case in self.CASES:
            with self.subTest(case=case):
                reporters = [FakeReporter(row) for row in case]
                self.assertEqual(aggregate(reporters), reproduce_aggregate(reporters))

    def test_matches_reference_with_failures_and_non_callables(self):
        class Missing:
            pass

        reporters = [RaisingReporter(KeyError('k')), FakeReporter({'slot': 'm'}),
                     Missing(), FakeReporter({'slot': 'a'}),
                     RaisingReporter(OSError('o'))]
        self.assertEqual(aggregate(reporters), reproduce_aggregate(reporters))
        self.assertEqual(aggregate(reporters), [{'slot': 'a'}, {'slot': 'm'}])


if __name__ == '__main__':
    unittest.main()
