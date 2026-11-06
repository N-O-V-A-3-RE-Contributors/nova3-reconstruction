"""Offline activation handshake: only new setup attests a live process identity."""
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from nova3_team import dsh_supervisor as sup, dsh_runtime as runtime


class ActivationTests(unittest.TestCase):
    def test_capability_parses_proc_field22_with_parenthesized_command(self):
        fields = ['S'] + ['0'] * 18 + ['123456'] + ['999']
        with mock.patch.object(sup.os, 'getpid', return_value=42), \
             mock.patch.object(pathlib.Path, 'read_text', return_value='42 (a ) tricky ( name) ' + ' '.join(fields)):
            self.assertEqual(sup.runtime_capability(), {
                'version': 'acceptance-gated/1', 'pid': 42, 'process_start_ticks': '123456'})

    def test_capability_unavailable_or_malformed_fails_closed(self):
        for raw in ['malformed', '42 (x) S', '43 (x) ' + ' '.join(['0'] * 20),
                    '42 (x) ' + ' '.join(['S'] + ['0'] * 18 + ['-1'])]:
            with self.subTest(raw=raw), mock.patch.object(sup.os, 'getpid', return_value=42), \
                 mock.patch.object(pathlib.Path, 'read_text', return_value=raw):
                self.assertIsNone(sup.runtime_capability())
        with mock.patch.object(pathlib.Path, 'read_text', side_effect=OSError('no procfs')):
            self.assertIsNone(sup.runtime_capability())

    def test_setup_persists_capability_and_missing_proc_clears_old_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            paths = SimpleNamespace(state=root / 'state', logs=root / 'logs')
            worker = mock.Mock()
            worker.validate_slot_id.side_effect = lambda slot: slot
            worker.get_repo.return_value = 'owner/repo'
            worker.slot_paths.return_value = paths
            worker.state_root.return_value = root
            capability = {'version': 'acceptance-gated/1', 'pid': 42, 'process_start_ticks': '123456'}
            endpoint = runtime.DshEndpoint('/unused', root)
            with mock.patch.object(sup, '_worker', return_value=worker), \
                 mock.patch.object(sup, 'GitHub'), \
                 mock.patch.object(sup, 'build_gate'), \
                 mock.patch('nova3_team.presence.Reporter'), \
                 mock.patch.object(sup, 'endpoint_from_env', return_value=endpoint), \
                 mock.patch.object(runtime, 'write_overlay'), \
                 mock.patch.object(runtime, 'probe_endpoint', return_value={'ok': True}), \
                 mock.patch.object(runtime, 'credential_present', return_value=True), \
                 mock.patch.object(sup.os, 'getpid', return_value=42), \
                 mock.patch.object(sup, 'runtime_capability', return_value=capability) as attest:
                controller = sup.DshSupervisor(project=root)
                controller._install_signals = mock.Mock()
                controller.gh = None
                # Mock account strings must serialize like real GitHub data.
                with mock.patch.object(sup, 'GitHub') as github:
                    github.return_value.me.return_value = 'owner'
                    controller.setup()
                    stored = controller.state.load()
                    self.assertEqual(stored['orchestration'], capability)
                    self.assertEqual(stored['supervisor_pid'], 42)
                    self.assertEqual(controller.state.path, paths.state / 'supervisor.json')
                    attest.return_value = None
                    controller.setup()
                    self.assertIsNone(controller.state.load()['orchestration'])


if __name__ == '__main__':
    unittest.main()
