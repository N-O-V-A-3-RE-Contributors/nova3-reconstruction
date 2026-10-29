"""Synthetic provider contracts only: no installed CLIs, accounts or completions."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from nova3_team import provider_runtime as runtime, worker


def available(provider, path=None):
    spec = runtime.get_provider(provider)
    return runtime.ProviderStatus(provider, runtime.RuntimeState.AVAILABLE,
                                  path or '/native/' + provider, '1.2.3',
                                  capabilities=spec.required_help)


class RegistryTests(unittest.TestCase):
    def test_registry_is_typed_ordered_immutable_and_has_vendor_guidance(self):
        self.assertEqual(runtime.provider_ids(),
                         ('codex', 'claude', 'gemini', 'kimi', 'qwen', 'opencode', 'copilot'))
        self.assertEqual(runtime.get_provider('codex').display_name, 'ChatGPT (Codex CLI)')
        self.assertNotIn('chatgpt', runtime.PROVIDERS)
        for provider, spec in runtime.PROVIDERS.items():
            self.assertIsInstance(spec, runtime.ProviderSpec)
            self.assertEqual(spec.executable, provider)
            self.assertTrue(spec.docs_url.startswith('https://'))
            self.assertTrue(spec.install_url.startswith('https://'))
            self.assertTrue(spec.login_guidance)
        with self.assertRaises(TypeError):
            runtime.PROVIDERS['other'] = runtime.PROVIDERS['codex']

    def test_existing_provider_argv_exact(self):
        self.assertEqual(runtime.build_command('codex', 'task'),
                         ['codex', 'exec', '--ephemeral', '--sandbox', 'workspace-write', 'task'])
        self.assertEqual(runtime.build_command('codex', 'task', codex_json=True),
                         ['codex', 'exec', '--ephemeral', '--sandbox', 'workspace-write', '--json', 'task'])
        self.assertEqual(runtime.build_command('claude', 'task'),
                         ['claude', '-p', 'task', '--max-turns', '12', '--allowedTools',
                          'Read', 'Edit', 'Write', 'Glob', 'Grep', '--output-format', 'text'])
        self.assertEqual(runtime.build_command('gemini', 'task'),
                         ['gemini', '-p', 'task', '--approval-mode', 'auto_edit', '--output-format', 'text'])

    def test_qwen_and_copilot_argv_exact_with_explicit_approval(self):
        self.assertEqual(runtime.build_command('qwen', 'task', status=available('qwen')),
                         ['/native/qwen', '-p', 'task', '--approval-mode', 'auto-edit', '--output-format', 'text'])
        self.assertEqual(runtime.build_command('copilot', 'task', status=available('copilot')),
                         ['/native/copilot', '-p', 'task', '--allow-tool', 'write', '--deny-tool', 'shell'])

    def test_injection_stays_one_argument_including_windows_paths(self):
        prompt = 'task; $(touch /tmp/not-created) `echo bad` & %TOKEN%\n--yolo'
        for provider in ('codex', 'claude', 'gemini', 'qwen', 'copilot'):
            path = rf'C:\Program Files\Vendor\{provider}.exe'
            command = runtime.build_command(provider, prompt, status=available(provider, path))
            self.assertEqual(command[0], path)
            self.assertEqual(command.count(prompt), 1)
            self.assertNotIn('--yolo', command)
            self.assertNotIn('--allow-all-tools', command)
            self.assertNotIn('--dangerously-skip-permissions', command)
            self.assertNotIn('--allow-all-paths', command)

    def test_new_provider_cannot_skip_readiness_and_unknown_id_not_echoed(self):
        with mock.patch.object(runtime, 'probe_provider', return_value=runtime.ProviderStatus(
                'qwen', runtime.RuntimeState.MISSING)) as probe:
            with self.assertRaises(runtime.ProviderUnavailable):
                runtime.build_command('qwen', 'task')
            probe.assert_called_once_with('qwen')
        for status in (available('codex'), runtime.ProviderStatus('qwen', runtime.RuntimeState.INCOMPATIBLE,
                                                               reason='PRIVATE-OUTPUT')):
            with self.assertRaises(runtime.ProviderUnavailable) as error:
                runtime.build_command('qwen', 'task', status=status)
            self.assertNotIn('PRIVATE-OUTPUT', str(error.exception))
        with self.assertRaises(runtime.ProviderUnavailable) as error:
            runtime.get_provider('PRIVATE-INPUT')
        self.assertNotIn('PRIVATE-INPUT', str(error.exception))

    def test_documented_unsupported_providers_never_build_or_guess(self):
        # Kimi print mode auto-approves every tool (implicit --afk); OpenCode's
        # bounded profile lives only in opencode.json permission rules. Both are
        # documented as unsupported, with the vendor URL cited in the reason.
        for provider, url in (
                ('kimi', 'moonshotai.github.io/kimi-cli'),
                ('opencode', 'opencode.ai/docs/permissions')):
            spec = runtime.get_provider(provider)
            self.assertIn(url, spec.unsupported_reason)
            self.assertIn('UNSUPPORTED', spec.unsupported_reason)
            self.assertEqual(spec.required_help, ())
            with self.assertRaises(runtime.ProviderUnavailable):
                runtime.build_command(provider, 'task', status=available(provider))

    def test_verified_candidates_require_capability_evidence_not_just_a_status(self):
        for provider in ('qwen', 'copilot'):
            spec = runtime.get_provider(provider)
            self.assertTrue(spec.required_help)
            self.assertTrue(spec.permission_profile)
            self.assertEqual(spec.unsupported_reason, '')
            unproven = runtime.ProviderStatus(provider, runtime.RuntimeState.AVAILABLE,
                                              '/native/' + provider, '1.2.3')
            with self.assertRaises(runtime.ProviderUnavailable):
                runtime.build_command(provider, 'task', status=unproven)
            partial = runtime.ProviderStatus(
                provider, runtime.RuntimeState.AVAILABLE, '/native/' + provider, '1.2.3',
                capabilities=spec.required_help[:-1])
            with self.assertRaises(runtime.ProviderUnavailable):
                runtime.build_command(provider, 'task', status=partial)
            self.assertEqual(runtime.build_command(provider, 'task',
                                                   status=available(provider))[0],
                             '/native/' + provider)

    def test_capability_token_match_is_exact_not_substring(self):
        # A toolbox flag must not authorize a differently named tool flag.
        with mock.patch.object(runtime.shutil, 'which', return_value='/native/copilot'), \
                mock.patch.object(runtime, '_probe', side_effect=[
                    runtime._Probe(0, '1.2.3'),
                    runtime._Probe(0, '--prompt --allow-toolbox write --deny-tool shell --output-format')]):
            status = runtime.probe_provider('copilot')
        self.assertEqual(status.state, runtime.RuntimeState.INCOMPATIBLE)
        self.assertIn('lacks the required', status.reason)

    def test_unprobed_new_provider_builds_only_after_successful_local_probe(self):
        spec = runtime.get_provider('qwen')
        with mock.patch.object(runtime.shutil, 'which', return_value='/native/qwen'), \
                mock.patch.object(runtime, '_probe', side_effect=[
                    runtime._Probe(0, '1.2.3'),
                    runtime._Probe(0, ' '.join(spec.required_help))]):
            command = runtime.build_command('qwen', 'task')
        self.assertEqual(command[0], '/native/qwen')

    def test_invalid_prompt_rejected_without_echo(self):
        for prompt in ('', '   ', 'SECRET\0TEXT', None, '--yolo', ' --allow-all-tools'):
            with self.assertRaises(runtime.ProviderUnavailable) as error:
                runtime.build_command('claude', prompt)
            self.assertNotIn('SECRET', str(error.exception))


class ProbeTests(unittest.TestCase):
    def test_unsupported_vendors_probe_version_only_then_stop_with_cited_reason(self):
        for provider in ('kimi', 'opencode'):
            spec = runtime.get_provider(provider)
            with mock.patch.object(runtime.shutil, 'which', return_value='/native/' + provider), \
                    mock.patch.object(runtime, '_probe',
                                      return_value=runtime._Probe(0, '9.9.9')) as probe:
                status = runtime.probe_provider(provider)
            self.assertEqual(status.state, runtime.RuntimeState.INCOMPATIBLE)
            self.assertEqual(status.version, '9.9.9')
            self.assertEqual(status.reason, spec.unsupported_reason)
            self.assertNotEqual(status.state, runtime.RuntimeState.AVAILABLE)
            probe.assert_called_once_with(['/native/' + provider, '--version'])

    def test_verified_candidates_report_documented_capabilities(self):
        for provider in ('qwen', 'copilot'):
            spec = runtime.get_provider(provider)
            with mock.patch.object(runtime.shutil, 'which', return_value='/native/' + provider), \
                    mock.patch.object(runtime, '_probe', side_effect=[
                        runtime._Probe(0, 'vendor 2.0.0'),
                        runtime._Probe(0, ' '.join(spec.required_help) + ' --json')]):
                status = runtime.probe_provider(provider)
            self.assertEqual(status.state, runtime.RuntimeState.AVAILABLE)
            self.assertEqual(set(spec.required_help) - set(status.capabilities), set())
            self.assertIn('--json', status.capabilities)

    def test_missing_binary_is_actionable_and_no_process_runs(self):
        with mock.patch.object(runtime.shutil, 'which', return_value=None), \
                mock.patch.object(runtime, '_probe') as probe:
            for provider in runtime.provider_ids():
                result = runtime.probe_provider(provider)
                self.assertEqual(result.state, runtime.RuntimeState.MISSING)
                self.assertIn('Install locally:', result.reason)
            probe.assert_not_called()

    def test_capability_gate_not_an_invented_minimum_version(self):
        for provider in ('codex', 'claude', 'gemini', 'qwen', 'copilot'):
            spec = runtime.get_provider(provider)
            help_text = ' '.join(spec.required_help) + ' --json'
            with mock.patch.object(runtime.shutil, 'which', return_value='/native/' + provider), \
                    mock.patch.object(runtime, '_probe', side_effect=[
                        runtime._Probe(0, 'vendor 1.2.3\n'), runtime._Probe(0, help_text)]) as probe:
                result = runtime.probe_provider(provider)
                self.assertTrue(result.available)
                self.assertEqual(result.version, '1.2.3')
                self.assertEqual(probe.call_args_list, [
                    mock.call(['/native/' + provider, '--version']),
                    mock.call(['/native/' + provider, *spec.help_args])])

    def test_unsupported_help_version_failed_probe_and_secrets_never_exported(self):
        cases = (
            [runtime._Probe(0, 'unversioned PRIVATE-OUTPUT')],
            [runtime._Probe(1, 'PRIVATE-OUTPUT')],
            [runtime._Probe(None, failure='Local CLI probe timed out.')],
            [runtime._Probe(0, '1.2.3'), runtime._Probe(0, '--prompt --approval-mode auto_edit --output-format')],
            [runtime._Probe(0, '1.2.3'), runtime._Probe(1, 'PRIVATE-OUTPUT')],
            [runtime._Probe(0, '1.2.3'), runtime._Probe(0, '--prompt --approval-mode auto-editing --output-format')],
        )
        for results in cases:
            with mock.patch.object(runtime.shutil, 'which', return_value='/native/qwen'), \
                    mock.patch.object(runtime, '_probe', side_effect=results):
                status = runtime.probe_provider('qwen')
                self.assertFalse(status.available)
                self.assertNotIn('PRIVATE-OUTPUT', repr(status))

    def test_windows_native_exe_and_batch_wrapper(self):
        for path in (r'C:\Program Files\Qwen\qwen.CMD', r'C:\bin\qwen.bat'):
            with mock.patch.object(runtime.shutil, 'which', return_value=path), \
                    mock.patch.object(runtime, '_probe') as probe:
                self.assertEqual(runtime.probe_provider('qwen').state, runtime.RuntimeState.INCOMPATIBLE)
                probe.assert_not_called()
        path = r'C:\Program Files\Qwen\qwen.exe'
        with mock.patch.object(runtime.shutil, 'which', return_value=path), \
                mock.patch.object(runtime, '_probe', side_effect=[runtime._Probe(0, '1.2.3'),
                    runtime._Probe(0, ' '.join(runtime.PROVIDERS['qwen'].required_help))]) as probe:
            self.assertTrue(runtime.probe_provider('qwen').available)
            self.assertEqual(probe.call_args_list[0], mock.call([path, '--version']))
        self.assertEqual(runtime.executable_stem(r'C:\Program Files\codex.EXE'), 'codex')

    def test_real_probe_is_bounded_closed_stdin_and_output_limited(self):
        # Only local Python emitters: no vendor binaries, network or model calls.
        result = runtime._probe([sys.executable, '-c',
                                 'import sys; print(repr(sys.stdin.read()))'])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.output.strip(), "''")
        with mock.patch.object(runtime, 'PROBE_TIMEOUT', 0.1):
            result = runtime._probe([sys.executable, '-c', 'import time; time.sleep(10)'])
        self.assertIn('timed out', result.failure)
        with mock.patch.object(runtime, 'MAX_PROBE_BYTES', 128):
            result = runtime._probe([sys.executable, '-c', 'print("x" * 1024)'])
        self.assertIn('limit', result.failure)
        result = runtime._probe(['/nonexistent/SECRET-EXECUTABLE'])
        self.assertEqual(result.failure, 'Local CLI probe could not execute.')


class AuthenticationTests(unittest.TestCase):
    def test_no_key_no_auth_claim_no_login_no_credential_file_reads(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(runtime.shutil, 'which', side_effect=lambda name: '/native/' + name), \
                mock.patch.object(runtime, '_probe') as probe, \
                mock.patch.object(Path, 'read_text', side_effect=AssertionError('credential read')):
            for provider in ('kimi', 'qwen', 'opencode', 'copilot', 'claude', 'gemini'):
                evidence = runtime.check_auth(provider)
                self.assertEqual(evidence.state, runtime.AuthState.UNKNOWN)
                self.assertIn('official', evidence.reason)
            probe.assert_not_called()

    def test_missing_auth_cli_status_failure_and_distinct_github_identity(self):
        with mock.patch.object(runtime.shutil, 'which', return_value=None):
            self.assertEqual(runtime.check_auth('codex').state, runtime.AuthState.UNAVAILABLE)
            self.assertEqual(runtime.check_github_identity().state, runtime.AuthState.UNAVAILABLE)
        with mock.patch.object(runtime.shutil, 'which', side_effect=lambda name: '/native/' + name), \
                mock.patch.object(runtime, '_probe', return_value=runtime._Probe(0, 'SECRET')) as probe:
            self.assertEqual(runtime.check_auth('codex').state, runtime.AuthState.AUTHENTICATED)
            evidence = runtime.check_github_identity()
            self.assertEqual(evidence.state, runtime.AuthState.AUTHENTICATED)
            self.assertNotIn('SECRET', repr(evidence))
            self.assertEqual(probe.call_args_list, [mock.call(['/native/codex', 'login', 'status']),
                mock.call(['/native/gh', 'auth', 'status', '--hostname', 'github.com'])])
        with mock.patch.object(runtime.shutil, 'which', return_value='/native/codex'), \
                mock.patch.object(runtime, '_probe', return_value=runtime._Probe(1, 'SECRET')):
            self.assertEqual(runtime.check_auth('codex').state, runtime.AuthState.UNKNOWN)

    def test_diagnostic_redaction_never_logs_environment_values(self):
        with mock.patch.dict(os.environ, {'QWEN_API_KEY': 'private-key-value',
                                          'GH_TOKEN': 'private-github-value'}):
            diagnostic = runtime.redact_diagnostic(
                'private-key-value private-github-value ghp_exampleToken sk-exampleKey Bearer otherToken')
            self.assertNotIn('private-key-value', diagnostic)
            self.assertNotIn('private-github-value', diagnostic)
            self.assertNotIn('exampleToken', diagnostic)
            self.assertNotIn('exampleKey', diagnostic)
            self.assertNotIn('otherToken', diagnostic)


class WorkerRegistryTests(unittest.TestCase):
    def test_run_checks_provider_before_github_or_claim(self):
        for provider in runtime.provider_ids():
            result = runtime.ProviderStatus(provider, runtime.RuntimeState.MISSING,
                                            reason='Optional CLI unavailable; install locally.')
            with mock.patch.object(sys, 'argv', ['worker', 'run', '--provider', provider]), \
                    mock.patch.object(runtime, 'probe_provider', return_value=result) as probe, \
                    mock.patch.object(worker, 'get_repo') as github, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
                worker.main()
            self.assertEqual(exit.exception.code, 2)
            probe.assert_called_once_with(provider)
            github.assert_not_called()

    def test_validated_verified_candidates_flow_through_worker_command_for(self):
        for provider, expected in (
                ('qwen', ['/native/qwen', '-p', 'task', '--approval-mode', 'auto-edit',
                          '--output-format', 'text']),
                ('copilot', ['/native/copilot', '-p', 'task', '--allow-tool', 'write',
                             '--deny-tool', 'shell'])):
            status = runtime.ProviderStatus(provider, runtime.RuntimeState.AVAILABLE,
                                            '/native/' + provider, '1.2.3',
                                            capabilities=runtime.get_provider(provider).required_help)
            self.assertEqual(worker.command_for(provider, 'task', status=status), expected)

    def test_worker_unknown_provider_fails_before_github_contact(self):
        with mock.patch.object(sys, 'argv', ['worker', 'run', '--provider', 'PRIVATE-VENDOR']), \
                mock.patch.object(runtime, 'probe_provider') as probe, \
                mock.patch.object(worker, 'get_repo') as github, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
            worker.main()
        self.assertEqual(exit.exception.code, 2)
        probe.assert_not_called()
        github.assert_not_called()

    def test_default_provider_remains_codex(self):
        with mock.patch.object(sys, 'argv', ['worker', 'run']), \
                mock.patch.object(runtime, 'probe_provider', return_value=runtime.ProviderStatus(
                    'codex', runtime.RuntimeState.MISSING, reason='Absent')) as probe, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            worker.main()
        probe.assert_called_once_with('codex')

    def test_validated_codex_uses_native_path_and_preflight_json_evidence(self):
        status = runtime.ProviderStatus('codex', runtime.RuntimeState.AVAILABLE,
                                       '/native/codex', '1.2.3', capabilities=('--json',))
        with mock.patch.object(worker, '_codex_json_supported') as probe:
            command = worker.command_for('codex', 'task', status=status)
        self.assertEqual(command, ['/native/codex', 'exec', '--ephemeral', '--sandbox',
                                   'workspace-write', '--json', 'task'])
        probe.assert_not_called()

    def test_provider_listing_offline_no_github_or_login(self):
        with mock.patch.object(sys, 'argv', ['worker', 'providers']), \
                mock.patch.object(runtime.shutil, 'which', return_value=None), \
                mock.patch.object(runtime, '_probe') as probe, \
                mock.patch.object(worker, 'get_repo') as github, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            worker.main()
        listing = json.loads(output.getvalue())
        self.assertEqual([p['id'] for p in listing['providers']], list(runtime.provider_ids()))
        self.assertEqual(listing['github_identity']['state'], 'unknown')
        probe.assert_not_called()
        github.assert_not_called()

    def test_worker_prompt_and_source_guard_remain_in_force(self):
        issue = {'number': 127, 'title': 'Synthetic test', 'body': 'Allowed paths: source.cpp'}
        prompt = worker.session_prompt(issue, 1, 'No prior attempt', ['source.cpp'], None)
        for expected in ('Never patch an APK', 'ALLOWED EDIT PATHS (strict): source.cpp',
                         'Do not start nested AI sessions', 'NOT MATCHED'):
            self.assertIn(expected, prompt)
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(worker, 'changed_paths', return_value=['source.cpp']), \
                mock.patch.object(worker, 'guard_check', return_value=[('source.cpp', 'binary magic')]):
            with self.assertRaisesRegex(RuntimeError, 'Forbidden files'):
                worker.validate_scope(temp, ['source.cpp'])


if __name__ == '__main__':
    unittest.main()
