"""Synthetic Codex JSONL only: no provider session, secrets or game fixtures."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from nova3_team import activity_feed as feed


def event(kind, item_type=None, **fields):
    value = {'type': kind}
    if item_type:
        value['item'] = {'id': 'item_1', 'type': item_type, **fields}
    else:
        value.update(fields)
    return json.dumps(value).encode() + b'\n'


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'round-1.log'
        self.log.touch()
        self.activity = feed.ActivityFeed(self.log, structured=True)

    def append(self, data):
        with self.log.open('ab') as output:
            output.write(data)

    def summary(self, **kwargs):
        self.assertTrue(self.activity.update(force=True, **kwargs))
        return json.loads(self.activity.path.read_text())

    def test_representative_lifecycle_command_and_file_change(self):
        self.append(event('thread.started', thread_id='private-id'))
        self.assertEqual(self.summary()['status'], 'RUNNING')
        self.append(event('item.started', 'command_execution', command='python -m unittest -v'))
        value = self.summary()
        self.assertEqual(value['status'], 'TESTING')
        self.assertEqual(value['event_category'], 'test')
        self.assertEqual(value['commands_started'], 1)
        self.assertEqual(value['tests_started'], 1)
        self.append(event('item.completed', 'command_execution', command='python -m unittest -v',
                          exit_code=1, status='completed', aggregated_output='fixture failed'))
        value = self.summary()
        self.assertEqual(value['status'], 'RUNNING')
        self.assertEqual(value['commands_completed'], 1)
        self.assertEqual(value['tests_completed'], 1)  # completed is not passed
        self.append(event('item.started', 'file_change', changes=[{'path': 'private.txt'}]))
        self.assertEqual(self.summary()['status'], 'EDITING')
        self.append(event('item.completed', 'file_change', status='completed'))
        self.assertIsNone(self.summary()['file_changed_count'])
        self.append(event('turn.completed', usage={'input_tokens': 999}))
        value = self.summary(returncode=0, file_changed_count=2)
        self.assertEqual(value['status'], 'FINISHED')
        self.assertEqual(value['file_changed_count'], 2)
        self.assertTrue(value['counts_complete'])
        self.assertTrue(value['timestamp_utc'].endswith('Z'))

    def test_partial_lines_and_escaped_multiline_text(self):
        data = event('item.started', 'command_execution', command='echo harmless',
                     aggregated_output='one\ntwo\nthree')
        self.append(data[:45])
        self.assertEqual(self.summary()['status'], 'UNKNOWN')
        self.append(data[45:-1])
        self.assertEqual(self.summary()['commands_started'], 0)
        self.append(b'\n')
        value = self.summary()
        self.assertEqual(value['commands_started'], 1)
        self.assertEqual(value['status'], 'RUNNING')
        # Incremental reads never recount old events.
        self.assertEqual(self.summary()['commands_started'], 1)

    def test_throttle_and_quiet_use_observation_time(self):
        self.append(event('turn.started'))
        with mock.patch.object(feed.time, 'monotonic', return_value=10):
            self.assertTrue(self.activity.update())
        self.append(event('item.started', 'command_execution', command='ctest'))
        with mock.patch.object(feed.time, 'monotonic', return_value=15):
            self.assertFalse(self.activity.update())
        with mock.patch.object(feed.time, 'monotonic', return_value=20):
            self.assertTrue(self.activity.update())
        with mock.patch.object(feed.time, 'monotonic', return_value=80):
            value = self.summary()
        self.assertEqual(value['status'], 'QUIET')
        self.assertEqual(value['event_category'], 'test')
        self.assertEqual(value['last_activity_age_seconds'], 60)

    def test_no_event_text_leaks_from_any_field(self):
        secret = 'SYNTHETIC_BEARER_SECRET_/private/example/game.bin'
        cases = [
            event('thread.started', thread_id=secret),
            event('item.started', 'command_execution', id=secret,
                  command='echo ' + secret, reasoning=secret),
            event('item.completed', 'command_execution', id=secret,
                  command='echo ' + secret, aggregated_output=secret, stderr=secret),
            event('item.completed', 'file_change', changes=[{'path': secret, 'diff': secret}]),
            event('item.completed', 'agent_message', text=secret),
            event('item.completed', 'reasoning', text=secret),
            event('turn.failed', error={'message': secret}),
            event('error', message=secret),
            event(secret, text=secret),
            secret.encode() + b'\n',
        ]
        expected_keys = {'schema_version', 'timestamp_utc', 'status', 'event_category',
                         'last_activity_age_seconds', 'commands_started', 'commands_completed',
                         'tests_started', 'tests_completed', 'file_changed_count',
                         'counts_complete', 'structured_events'}
        for data in cases:
            self.append(data)
            value = self.summary()
            text = self.activity.path.read_text()
            self.assertNotIn(secret, text)
            self.assertNotIn('/private/', text)
            self.assertNotIn(str(self.log.parent), text)
            self.assertEqual(set(value), expected_keys)

    def test_reasoning_alone_is_not_activity(self):
        self.append(event('item.completed', 'reasoning', text='hidden'))
        value = self.summary()
        self.assertEqual(value['status'], 'UNKNOWN')
        self.assertIsNone(value['last_activity_age_seconds'])

    def test_malformed_unknown_and_truncated_input_fail_safe(self):
        for data in (b'null\n[]\n', b'\xff\n', b'{bad json}\n',
                     b'{\n"type":"turn.started"\n}\n',
                     event('item.started', 'future_type', text='ignored'),
                     event('item.started', 'command_execution', id=['invalid']),
                     b'[' * 2000 + b'\n'):
            with self.subTest(data=data[:40]):
                self.append(data)
                value = self.summary()
                self.assertEqual(value['status'], 'UNKNOWN')
                self.assertFalse(value['counts_complete'])
        self.append(event('turn.started'))
        self.assertEqual(self.summary()['status'], 'RUNNING')
        self.append(b'{"type":')
        self.assertEqual(self.summary(returncode=1)['status'], 'FAILED')
        self.assertFalse(self.summary(returncode=0)['counts_complete'])

    def test_missing_opaque_and_unavailable_logs(self):
        self.log.unlink()
        self.assertEqual(self.summary()['status'], 'UNKNOWN')
        self.assertFalse(self.summary()['counts_complete'])
        self.log.write_text('unstructured provider output\n')
        self.activity = feed.ActivityFeed(self.log, structured=False)
        value = self.summary()
        self.assertEqual(value['status'], 'UNKNOWN')
        self.assertFalse(value['structured_events'])
        self.assertEqual(self.activity.offset, 0)
        with mock.patch.object(feed.os, 'open', side_effect=PermissionError):
            self.assertFalse(self.activity.update(force=True))

    def test_file_replacement_and_truncation_do_not_reuse_active_items(self):
        self.append(event('item.started', 'command_execution', command='pytest'))
        self.summary()
        self.log.write_bytes(b'bad\n')
        value = self.summary()
        self.assertEqual(value['status'], 'UNKNOWN')
        replacement = self.log.with_suffix('.new')
        replacement.write_bytes(event('turn.started'))
        replacement.replace(self.log)
        value = self.summary()
        self.assertEqual(value['status'], 'RUNNING')
        self.assertFalse(value['counts_complete'])

    def test_large_log_and_oversized_line_are_bounded(self):
        self.append(b'x' * (feed.MAX_READ_BYTES * 3) + b'\n' + event('turn.started'))
        value = self.summary()
        self.assertEqual(value['status'], 'RUNNING')
        self.assertFalse(value['counts_complete'])
        self.assertLessEqual(len(self.activity.pending), feed.MAX_LINE_BYTES)
        self.append(b'x' * (feed.MAX_LINE_BYTES + 1))
        self.assertEqual(self.summary()['status'], 'UNKNOWN')
        self.append(b'\n' + event('turn.started'))
        self.assertEqual(self.summary()['status'], 'RUNNING')

    def test_active_items_are_bounded(self):
        with mock.patch.object(feed, 'MAX_ACTIVE_ITEMS', 2):
            for i in range(3):
                self.append(event('item.started', 'command_execution', id=str(i), command='echo hi'))
            value = self.summary()
        self.assertEqual(value['status'], 'UNKNOWN')
        self.assertFalse(value['counts_complete'])
        self.assertLessEqual(len(self.activity.active), 2)

    def test_atomic_private_updates_and_cleanup_on_replace_failure(self):
        self.append(event('turn.started'))
        self.summary()
        previous = self.activity.path.read_bytes()
        real_replace = os.replace

        def check_replace(source, destination):
            self.assertEqual(self.activity.path.read_bytes(), previous)
            json.loads(Path(source).read_text())
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(Path(source).stat().st_mode), 0o600)
            real_replace(source, destination)

        with mock.patch.object(feed.os, 'replace', side_effect=check_replace):
            self.summary(returncode=0)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(self.activity.path.stat().st_mode), 0o600)
        previous = self.activity.path.read_bytes()
        with mock.patch.object(feed.os, 'replace', side_effect=OSError):
            self.assertFalse(self.activity.update(force=True))
        self.assertEqual(self.activity.path.read_bytes(), previous)
        self.assertEqual(list(self.log.parent.glob('.activity-*')), [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX nonblocking regular-file guard')
    def test_fifo_and_symlink_are_not_parsed(self):
        self.log.unlink()
        os.mkfifo(self.log)
        self.assertEqual(self.summary()['status'], 'UNKNOWN')
        self.log.unlink()
        target = self.log.with_suffix('.target')
        target.write_bytes(event('turn.started'))
        self.log.symlink_to(target)
        self.assertEqual(self.summary()['status'], 'UNKNOWN')

    def test_test_detection_does_not_guess_from_text(self):
        for command in ('ctest --output-on-failure', 'python3 -m unittest -v',
                        'pytest tests', "/bin/bash -lc 'python -m pytest tests'"):
            self.assertTrue(feed._is_test(command), command)
        for command in ('echo pytest', 'cat pytest', 'python script.py pytest',
                        'echo hi && pytest', 'pytest; echo hi', 'pytest $(cat secret)',
                        '"unterminated', None, 'a' * 9000):
            self.assertFalse(feed._is_test(command), command)


if __name__ == '__main__':
    unittest.main()
