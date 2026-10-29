"""Synthetic provider contracts only: no installed CLIs, accounts or completions."""
import contextlib
import dataclasses
import io
import json
import os
from pathlib import Path
import stat
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

    def test_kimi_and_opencode_argv_exact_with_bounded_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = runtime.prepare_aux_files('kimi', temp)
            self.assertEqual(set(profile), {'kimi-agent.yaml'})
            command = runtime.build_command('kimi', 'task', status=available('kimi'),
                                            aux_dir=temp)
            text = Path(profile['kimi-agent.yaml']).read_text(encoding='utf-8')
        self.assertEqual(command, ['/native/kimi', '--print', '--agent-file',
                                   profile['kimi-agent.yaml'], '--output-format',
                                   'text', '-p', 'task'])
        self.assertIn('kimi_cli.tools.file:ReadFile', text)
        self.assertIn('kimi_cli.tools.file:WriteFile', text)
        self.assertNotIn('Shell', text)
        self.assertNotIn('SearchWeb', text)

        self.assertEqual(runtime.build_command('opencode', 'task', status=available('opencode')),
                         ['/native/opencode', 'run', '--auto', '--agent',
                          'nova3-bounded', 'task'])
        environment = runtime.build_environment('opencode')
        self.assertEqual(set(environment), {'OPENCODE_CONFIG_CONTENT'})
        config = json.loads(environment['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(config['permission']['*'], 'deny')
        self.assertEqual(config['permission']['edit'], 'allow')
        self.assertEqual(config['permission']['read'], 'allow')
        self.assertNotIn('bash', config['permission'])
        self.assertNotIn('webfetch', config['permission'])
        self.assertNotIn('question', config['permission'])
        agent_rules = config['agent']['nova3-bounded']['permission']
        self.assertEqual(agent_rules['*'], 'deny')
        self.assertEqual(agent_rules['edit'], 'allow')
        self.assertEqual(runtime.build_environment('kimi'), {})

    def test_agent_profile_is_private_and_never_optional(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'provider-aux'
            profile = runtime.prepare_aux_files('kimi', folder)
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(Path(profile['kimi-agent.yaml']).stat().st_mode), 0o600)
            with self.assertRaises(runtime.ProviderUnavailable) as error:
                runtime.build_command('kimi', 'PRIVATE-TASK', status=available('kimi'))
            self.assertNotIn('PRIVATE-TASK', str(error.exception))
            with self.assertRaises(runtime.ProviderUnavailable):
                runtime.build_command('kimi', 'task', status=available('kimi'),
                                      aux_dir=Path(temp) / 'absent')
        self.assertEqual(runtime.prepare_aux_files('qwen', Path(temp) / 'unused'), {})
        self.assertFalse((Path(temp) / 'unused').exists())


    def test_injection_stays_one_argument_including_windows_paths(self):
        prompt = 'task; $(touch /tmp/not-created) `echo bad` & %TOKEN%\n--yolo'
        with tempfile.TemporaryDirectory() as temp:
            runtime.prepare_aux_files('kimi', temp)
            for provider in ('codex', 'claude', 'gemini', 'kimi', 'qwen', 'opencode', 'copilot'):
                path = rf'C:\Program Files\Vendor\{provider}.exe'
                command = runtime.build_command(provider, prompt, status=available(provider, path),
                                                aux_dir=temp)
                self.assertEqual(command[0], path)
                self.assertEqual(command.count(prompt), 1)
                self.assertNotIn('--yolo', command)
                self.assertNotIn('--allow-all-tools', command)
                self.assertNotIn('--dangerously-skip-permissions', command)
                self.assertNotIn('--allow-all-paths', command)
                self.assertNotIn('--afk', command)
                self.assertNotIn('--allow-all', command)
                environment = runtime.build_environment(provider)
                self.assertNotIn(prompt, repr(environment))
                self.assertFalse(any('TOKEN' in k for k in environment))

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

    def test_unsupported_specs_still_fail_closed_without_a_read_only_substitute(self):
        # Registry keeps the escape hatch for a vendor whose official docs show
        # no bounded noninteractive profile. A synthetic spec proves the path.
        synthetic = dataclasses.replace(
            runtime.get_provider('copilot'), id='future', executable='future',
            required_help=(), aux_files=(), environment=(),
            unsupported_reason='UNSUPPORTED: official docs show no bounded profile. '
                               'https://example.invalid/docs')
        with mock.patch.object(runtime, 'PROVIDERS', {'future': synthetic}):
            with self.assertRaises(runtime.ProviderUnavailable) as error:
                runtime.build_command('future', 'PRIVATE-TASK', status=runtime.ProviderStatus(
                    'future', runtime.RuntimeState.AVAILABLE, '/native/future', '1.2.3'))
            for leaked in ('PRIVATE-TASK', '/native/future', 'https://example.invalid'):
                self.assertNotIn(leaked, str(error.exception))
            with mock.patch.object(runtime.shutil, 'which', return_value='/native/future'), \
                    mock.patch.object(runtime, '_probe', return_value=runtime._Probe(0, '1.2.3')) as probe:
                status = runtime.probe_provider('future')
            self.assertEqual(status.state, runtime.RuntimeState.INCOMPATIBLE)
            self.assertEqual(status.reason, synthetic.unsupported_reason)
            probe.assert_called_once_with(['/native/future', '--version'])

    def test_new_adapters_require_capability_evidence_not_just_a_status(self):
        for provider in ('kimi', 'qwen', 'opencode', 'copilot'):
            spec = runtime.get_provider(provider)
            self.assertTrue(spec.required_help)
            self.assertTrue(spec.permission_profile)
            self.assertEqual(spec.unsupported_reason, '')
            unproven = runtime.ProviderStatus(provider, runtime.RuntimeState.AVAILABLE,
                                              '/native/' + provider, '1.2.3')
            with tempfile.TemporaryDirectory() as temp:
                runtime.prepare_aux_files(provider, temp)
                with self.assertRaises(runtime.ProviderUnavailable):
                    runtime.build_command(provider, 'task', status=unproven, aux_dir=temp)
                partial = runtime.ProviderStatus(
                    provider, runtime.RuntimeState.AVAILABLE, '/native/' + provider, '1.2.3',
                    capabilities=spec.required_help[:-1])
                with self.assertRaises(runtime.ProviderUnavailable):
                    runtime.build_command(provider, 'task', status=partial, aux_dir=temp)
                self.assertEqual(runtime.build_command(provider, 'task',
                                                       status=available(provider),
                                                       aux_dir=temp)[0],
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

    def test_legacy_providers_keep_frozen_argv_despite_help_drift(self):
        # claude 2.1.126 no longer advertises --max-turns in `claude --help`.
        # #127 freezes the legacy argv, so an advisory probe must not rewrite or
        # block it; only new adapters are gated on help tokens.
        drifted = runtime.ProviderStatus(
            'claude', runtime.RuntimeState.INCOMPATIBLE, '/native/claude', '2.1.126',
            reason='help lacks --max-turns', capabilities=('--print', '--allowedTools'))
        self.assertEqual(
            runtime.build_command('claude', 'task', status=drifted),
            ['/native/claude', '-p', 'task', '--max-turns', '12', '--allowedTools',
             'Read', 'Edit', 'Write', 'Glob', 'Grep', '--output-format', 'text'])
        self.assertTrue(runtime.is_legacy('claude'))
        self.assertFalse(runtime.is_legacy('kimi'))

    def test_new_adapters_still_fail_closed_on_incompatible_probe(self):
        incompatible = runtime.ProviderStatus(
            'kimi', runtime.RuntimeState.INCOMPATIBLE, '/native/kimi', '0.1',
            reason='help lacks --agent-file', capabilities=('--print',))
        with tempfile.TemporaryDirectory() as temp:
            runtime.prepare_aux_files('kimi', temp)
            with self.assertRaises(runtime.ProviderUnavailable):
                runtime.build_command('kimi', 'task', status=incompatible, aux_dir=temp)

    def test_status_for_another_provider_is_rejected(self):
        wrong = available('qwen')
        with self.assertRaises(runtime.ProviderUnavailable):
            runtime.build_command('copilot', 'task', status=wrong)

    def test_command_for_falls_back_to_codex_json_probe(self):
        drifted = runtime.ProviderStatus(
            'codex', runtime.RuntimeState.INCOMPATIBLE, '/native/codex', '0.163.0',
            reason='help drift', capabilities=())
        with mock.patch.object(worker, '_codex_json_supported', return_value=True):
            command = worker.command_for('codex', 'task', status=drifted)
        self.assertIn('--json', command)


class ProbeTests(unittest.TestCase):
    def test_new_adapters_report_documented_capabilities(self):
        for provider in ('kimi', 'qwen', 'opencode', 'copilot'):
            spec = runtime.get_provider(provider)
            with mock.patch.object(runtime.shutil, 'which', return_value='/native/' + provider), \
                    mock.patch.object(runtime, '_probe', side_effect=[
                        runtime._Probe(0, 'vendor 2.0.0'),
                        runtime._Probe(0, ' '.join(spec.required_help) + ' --json')]):
                status = runtime.probe_provider(provider)
            self.assertEqual(status.state, runtime.RuntimeState.AVAILABLE)
            self.assertEqual(set(spec.required_help) - set(status.capabilities), set())
            self.assertIn('--json', status.capabilities)
            self.assertNotIn('vendor 2.0.0', status.reason)

    def test_missing_binary_is_actionable_and_no_process_runs(self):
        with mock.patch.object(runtime.shutil, 'which', return_value=None), \
                mock.patch.object(runtime, '_probe') as probe:
            for provider in runtime.provider_ids():
                result = runtime.probe_provider(provider)
                self.assertEqual(result.state, runtime.RuntimeState.MISSING)
                self.assertIn('Install locally:', result.reason)
            probe.assert_not_called()

    def test_capability_gate_not_an_invented_minimum_version(self):
        for provider in runtime.provider_ids():
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

    def test_validated_new_adapters_flow_through_worker_command_for(self):
        for provider, expected in (
                ('qwen', ['/native/qwen', '-p', 'task', '--approval-mode', 'auto-edit',
                          '--output-format', 'text']),
                ('opencode', ['/native/opencode', 'run', '--auto', '--agent',
                              'nova3-bounded', 'task']),
                ('copilot', ['/native/copilot', '-p', 'task', '--allow-tool', 'write',
                             '--deny-tool', 'shell'])):
            status = runtime.ProviderStatus(provider, runtime.RuntimeState.AVAILABLE,
                                            '/native/' + provider, '1.2.3',
                                            capabilities=runtime.get_provider(provider).required_help)
            self.assertEqual(worker.command_for(provider, 'task', status=status), expected)
        status = runtime.ProviderStatus('kimi', runtime.RuntimeState.AVAILABLE,
                                        '/native/kimi', '1.2.3',
                                        capabilities=runtime.get_provider('kimi').required_help)
        with tempfile.TemporaryDirectory() as temp:
            profile = worker.provider_runtime.prepare_aux_files('kimi', temp)
            self.assertEqual(worker.command_for('kimi', 'task', status=status, aux_dir=temp),
                             ['/native/kimi', '--print', '--agent-file',
                              profile['kimi-agent.yaml'], '--output-format', 'text', '-p', 'task'])

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

    def test_run_provider_passes_only_declared_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'round.log'
            env = {'PATH': os.environ.get('PATH', ''), 'NOVA3_TEST_MARKER': 'alpha'}
            completed = worker.run_provider(
                [sys.executable, '-c',
                 'import os;print(os.environ.get("NOVA3_TEST_MARKER"));'
                 'print(os.environ.get("OPENCODE_CONFIG_CONTENT", "absent"))'],
                temp, log, 30, env=env)
            self.assertEqual(completed.returncode, 0)
            self.assertIn('alpha', log.read_text(encoding='utf-8'))
            self.assertIn('absent', log.read_text(encoding='utf-8'))

    def test_opencode_inline_config_never_inherits_secret_environment(self):
        with mock.patch.dict(os.environ, {'OPENCODE_API_KEY': 'private-key-value',
                                          'GH_TOKEN': 'private-github-value'}):
            environment = runtime.build_environment('opencode')
        self.assertEqual(set(environment), {'OPENCODE_CONFIG_CONTENT'})
        for secret in ('private-key-value', 'private-github-value', 'GH_TOKEN',
                       'API_KEY'):
            self.assertNotIn(secret, repr(environment))
        self.assertNotIn('prompt', environment['OPENCODE_CONFIG_CONTENT'].lower())

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
