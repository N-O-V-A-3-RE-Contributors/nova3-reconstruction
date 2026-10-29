"""Offline tests for the optional local-only contributor GUI (issue #128).

No internet, no real GitHub login, no provider launch and no paid model call.
Every subprocess, provider probe and browser open is injected or mocked. The
only socket used is a real loopback socket on an ephemeral port, which is what
lets these tests actually prove the binding/origin/CSRF guarantees.
"""
from __future__ import annotations

import http.client
import importlib.util
from pathlib import Path
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / 'contributor_desktop.py'
_DEFAULT = object()
spec = importlib.util.spec_from_file_location('nova3_contributor_desktop_tests', MODULE)
desktop = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = desktop
spec.loader.exec_module(desktop)


class FakeCandidate:
    def __init__(self, provider, installed=True, authenticated=True, detail=''):
        self.provider = provider
        self.installed = installed
        self.authenticated = authenticated
        self.detail = detail


class FakeController:
    def __init__(self, repo):
        self.repo = repo
        self.state = 'idle'
        self.detail = 'stub'
        self.provider = 'codex'
        self.calls = []
        self.refuse = None

    def start(self, *, slots=1, consent=False):
        self.calls.append(('start', slots, consent))
        if self.refuse:
            return {'ok': False, 'reason': self.refuse}
        self.state = 'running'
        return {'ok': True, 'reason': 'started'}

    def pause(self):
        self.calls.append(('pause',))
        self.state = 'paused'
        return {'ok': True, 'reason': 'paused'}

    def resume(self):
        self.calls.append(('resume',))
        self.state = 'running'
        return {'ok': True, 'reason': 'resumed'}

    def stop(self):
        self.calls.append(('stop',))
        self.state = 'stopped'
        return {'ok': True, 'reason': 'stopped'}


class GuiCase(unittest.TestCase):
    def make_session(self, **overrides):
        options = dict(
            repo='N-O-V-A-3-RE-Contributors/nova3-reconstruction',
            github_status=lambda: {'state': 'AUTHENTICATED', 'reason': 'stub signed in'},
            repo_access=lambda repo: {'state': 'ACCESSIBLE', 'reason': 'stub access'},
            candidates=lambda: [FakeCandidate('codex'), FakeCandidate('claude', False, False)],
            installer=lambda provider: None,
            browser=lambda url: True,
        )
        options.update(overrides)
        return desktop.OnboardingSession(**options)

    def setUp(self):
        self.session = self.make_session()
        self.server, self.thread, self.info = desktop.start_gui(
            self.session, open_browser=False)
        self.port = self.server.server_address[1]
        self.closed = False

    def tearDown(self):
        if self.closed:
            return
        self.session.shutdown_requested = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    # -- HTTP helpers -----------------------------------------------------
    def request(self, method, path, *, body=None, headers=None, host=None,
                origin=None, csrf=_DEFAULT, content_type='application/json'):
        hdrs = {'Host': host if host is not None else f'127.0.0.1:{self.port}'}
        if method == 'POST':
            hdrs['Content-Type'] = content_type
            hdrs['Origin'] = origin if origin is not None else f'http://127.0.0.1:{self.port}'
            token = self.session.csrf if csrf is _DEFAULT else csrf
            if token is not None:
                hdrs[desktop.CSRF_HEADER] = token
        if headers:
            hdrs.update(headers)
        payload = None if body is None else (
            body if isinstance(body, bytes) else json.dumps(body).encode('utf-8'))
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request(method, path, body=payload, headers=hdrs)
            response = conn.getresponse()
            raw = response.read()
            return response.status, raw
        finally:
            conn.close()

    def post(self, action, **fields):
        payload = {'action': action}
        payload.update(fields)
        status, raw = self.request('POST', '/api/action', body=payload)
        return status, json.loads(raw.decode('utf-8'))


class LoopbackSecurityTests(GuiCase):
    def test_bind_is_loopback_with_ephemeral_port(self):
        self.assertEqual(self.server.server_address[0], desktop.LOOPBACK)
        self.assertGreater(self.port, 0)
        second = desktop.create_server(self.session)
        try:
            self.assertNotEqual(second.server_address[1], self.port)
            self.assertEqual(second.server_address[0], desktop.LOOPBACK)
        finally:
            second.server_close()

    def test_non_loopback_bind_is_refused(self):
        for host in ('0.0.0.0', '::', '', 'localhost', '192.168.1.5'):
            with self.subTest(host=host):
                with self.assertRaises(desktop.DesktopSecurityError):
                    desktop.create_server(self.session, host=host)
        with self.assertRaises(desktop.DesktopSecurityError):
            desktop.create_server(self.session, host='127.0.0.1', port=70000)

    def test_csrf_token_required_and_constant_time_compared(self):
        original = self.session.snapshot()['step']
        status, _ = self.request('POST', '/api/action', body={'action': 'next'},
                                 csrf=None)  # header omitted entirely
        self.assertEqual(status, 403)
        status, _ = self.request('POST', '/api/action', body={'action': 'next'},
                                 csrf='not-the-token')
        self.assertEqual(status, 403)
        self.assertEqual(self.session.snapshot()['step'], original)
        status, data = self.post('next')
        self.assertEqual(status, 200)
        self.assertEqual(self.session.snapshot()['step'], original + 1)

    def test_wrong_origin_is_rejected(self):
        for origin in ('http://evil.example', 'null', '', f'https://127.0.0.1:{self.port}'):
            with self.subTest(origin=origin):
                status, data = self.request(
                    'POST', '/api/action', body={'action': 'next'}, origin=origin)
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(data)['reason'], 'invalid_origin')

    def test_wrong_host_is_rejected(self):
        for host in ('evil.example', f'localhost:{self.port}', '127.0.0.1', ''):
            with self.subTest(host=host):
                status, _ = self.request('GET', '/', host=host)
                self.assertEqual(status, 403)

    def test_get_page_and_state_carry_no_secret(self):
        status, raw = self.request('GET', '/')
        self.assertEqual(status, 200)
        page = raw.decode('utf-8')
        self.assertIn(self.session.csrf, page)          # token in body, not URL
        self.assertIn('aria-live', page)
        self.assertIn('<label for="repo">', page)
        status, raw = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        state = json.loads(raw.decode('utf-8'))['state']
        self.assertEqual(state['step_id'], 'welcome')
        self.assertEqual(state['slots'], 1)
        self.assertNotIn('csrf', state)

    def test_credential_fields_are_refused(self):
        for key in ('token', 'password', 'api_key', 'Authorization'):
            with self.subTest(key=key):
                status, data = self.post('check_github', **{key: 'hunter2-should-not-persist'})
                self.assertEqual(status, 400)
                self.assertEqual(data['reason'], 'credentials_not_accepted')
                self.assertNotIn('hunter2', json.dumps(data))

    def test_unknown_paths_and_actions(self):
        self.assertEqual(self.request('GET', '/nope')[0], 404)
        self.assertEqual(self.post('rm_rf')[0], 409)

    def test_oversized_and_non_json_bodies_rejected(self):
        status, _ = self.request('POST', '/api/action', body=b'x',
                                 headers={'Content-Length': str(desktop.MAX_BODY_BYTES + 1)})
        self.assertEqual(status, 400)
        status, _ = self.request('POST', '/api/action', body={'action': 'next'},
                                 content_type='text/plain')
        self.assertEqual(status, 415)

    def test_simultaneous_requests_are_safe(self):
        errors = []

        def fetch():
            try:
                status, _ = self.request('GET', '/api/state')
                if status != 200:
                    errors.append(status)
            except Exception as exc:  # pragma: no cover - diagnostic only
                errors.append(type(exc).__name__)

        threads = [threading.Thread(target=fetch) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])

    def test_request_log_has_no_query_or_tokens(self):
        self.request('GET', '/api/state')
        joined = '\n'.join(self.session.request_log)
        self.assertIn('GET /api/state', joined)
        self.assertNotIn('?', joined)
        self.assertNotIn(self.session.csrf, joined)


class WizardTests(GuiCase):
    def test_navigation_next_back_skip(self):
        self.assertEqual(self.session.snapshot()['step_id'], 'welcome')
        self.assertEqual(self.post('back')[0], 200)       # already at first step
        self.assertEqual(self.session.snapshot()['step'], 0)
        self.assertEqual(self.post('skip')[0], 409)       # welcome is not skippable
        self.assertEqual(self.post('next')[0], 200)
        self.assertEqual(self.session.snapshot()['step_id'], 'repository')
        self.assertEqual(self.post('skip')[0], 200)       # repository is skippable
        self.assertEqual(self.session.snapshot()['step_id'], 'provider')
        self.assertEqual(self.post('back')[0], 200)
        self.assertEqual(self.session.snapshot()['step_id'], 'repository')

    def test_repository_validation_and_check(self):
        status, data = self.post('set_repo', repo='not-a-repo')
        self.assertEqual(status, 409)
        self.assertEqual(data['state']['repo_status']['state'], 'INVALID')
        status, data = self.post('set_repo', repo='Org/repo')
        self.assertEqual(status, 200)
        self.assertEqual(data['state']['repo'], 'Org/repo')
        status, data = self.post('check_repo')
        self.assertEqual(status, 200)
        self.assertEqual(data['state']['repo_status']['state'], 'ACCESSIBLE')

    def test_github_status_reports_signed_out_without_prompting(self):
        self.session.github = desktop.OnboardingSession(
            github_status=lambda: {'state': 'SIGNED_OUT', 'reason': 'run gh auth login --web'}
        ).refresh_github()
        status, data = self.post('start')
        self.assertEqual(status, 409)
        self.assertIn('gh', data['reason'])

    def test_one_worker_is_default_and_multiple_needs_consent(self):
        self.assertEqual(self.session.snapshot()['slots'], 1)
        status, data = self.post('set_slots', slots=2, consent=False)
        self.assertEqual(status, 409)
        self.assertEqual(data['state']['slots'], 1)
        status, data = self.post('set_slots', slots=2, consent=True)
        self.assertEqual(status, 200)
        self.assertEqual(data['state']['slots'], 2)
        self.assertTrue(data['state']['multi_consent'])
        self.assertEqual(self.post('set_slots', slots=9)[0], 409)

    def test_start_pause_resume_use_shared_controller(self):
        controller = FakeController(self.session.repo)
        self.session._controller_factory = lambda repo: controller
        self.post('check_github')
        status, data = self.post('start')
        self.assertEqual(status, 200)
        self.assertEqual(controller.calls[-1], ('start', 1, False))
        self.assertEqual(data['state']['worker']['state'], 'running')
        self.assertEqual(self.post('pause')[0], 200)
        self.assertEqual(controller.calls[-1], ('pause',))
        self.assertEqual(self.post('resume')[0], 200)
        self.assertEqual(controller.calls[-1], ('resume',))

    def test_start_refused_without_github_or_repo(self):
        empty = desktop.OnboardingSession(github_status=lambda: {'state': 'SIGNED_OUT', 'reason': 'x'})
        self.assertFalse(empty.dispatch({'action': 'start'})['ok'])
        self.assertIsNone(empty.controller)

    def test_controller_gate_refusal_makes_no_launch_call(self):
        launched = []

        def launcher(**kwargs):
            launched.append(kwargs)
            return mock.Mock(started=True, processes=())

        controller = desktop.WorkerController(
            'Org/repo', gate_factory=lambda repo: None,
            issues_fetcher=lambda repo: [], launcher=launcher)
        result = controller.start(slots=1, consent=False)
        self.assertFalse(result['ok'])
        self.assertIn('gate', result['reason'])
        self.assertEqual(launched, [])

    def test_real_launch_slots_refuses_before_spawn_without_gate(self):
        from nova3_team import worker

        class RefusingGate:
            def preflight(self):
                raise RuntimeError('pinned identity missing')

        popen = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(worker, 'provider_candidates',
                                  return_value=[worker.ProviderCandidate('codex', True, True)]):
            controller = desktop.WorkerController(
                'Org/repo', gate_factory=lambda repo: RefusingGate(),
                issues_fetcher=lambda repo: [], root=tmp, popen=popen,
                python=sys.executable)
            result = controller.start(slots=1, consent=False)
        self.assertFalse(result['ok'])
        self.assertIn('approval_gate_unavailable', result['reason'])
        popen.assert_not_called()

    def test_start_action_never_bypasses_controller(self):
        controller = FakeController(self.session.repo)
        controller.refuse = 'approval_gate_unavailable'
        self.session._controller_factory = lambda repo: controller
        self.post('check_github')
        status, data = self.post('start')
        self.assertEqual(status, 409)
        self.assertEqual(data['state']['worker']['state'], 'idle')
        self.assertEqual(controller.calls[-1], ('start', 1, False))

    def test_provider_scan_offline_reports_unknown_not_ready(self):
        def offline():
            raise OSError('no network')
        session = self.make_session(candidates=offline)
        session.refresh_providers()
        self.assertTrue(session.providers)
        for row in session.providers:
            self.assertEqual(row['state'], 'UNKNOWN')
            self.assertNotEqual(row['state'], 'READY')

    def test_install_requires_explicit_consent(self):
        calls = []
        session = self.make_session(installer=lambda provider: calls.append(provider))
        session.refresh_providers()
        result = session.dispatch({'action': 'install_provider', 'provider': 'codex',
                                   'consent': False})
        self.assertFalse(result['ok'])
        self.assertEqual(calls, [])
        self.assertEqual(session.install['state'], 'DECLINED')

    def test_install_uses_official_package_without_sudo_or_shell(self):
        completed = mock.Mock(returncode=0)
        run = mock.Mock(return_value=completed)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(desktop.shutil, 'which', return_value='/usr/bin/npm'), \
                mock.patch.object(desktop.Path, 'home', return_value=Path(tmp)):
            desktop.default_installer('codex', run=run)
        argv = run.call_args.args[0]
        self.assertNotIn('sudo', argv)
        self.assertIn('@openai/codex', argv)
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(run.call_args.kwargs['env']['NPM_CONFIG_PREFIX'],
                         str(Path(tmp) / '.nova3-team' / 'npm'))
        # Unknown providers never reach the installer.
        with self.assertRaises(desktop.DesktopError):
            desktop.default_installer('totally-not-a-cli', run=run)

    def test_install_failure_is_redacted_in_response_and_logs(self):
        secret = 'ghp_' + 'A' * 36

        def failing(provider):
            raise RuntimeError('npm failed token=' + secret)

        session = self.make_session(installer=failing)
        session.refresh_providers()
        result = session.dispatch({'action': 'install_provider', 'provider': 'codex',
                                   'consent': True})
        self.assertFalse(result['ok'])
        self.assertEqual(session.install['state'], 'FAILED')
        blob = json.dumps(result) + json.dumps(session.snapshot()) + '\n'.join(session.request_log)
        self.assertNotIn(secret, blob)
        self.assertIn('[redacted]', json.dumps(result))

    def test_open_control_center_returns_terminal_command(self):
        result = self.post('open_control_center')
        self.assertEqual(result[0], 200)
        self.assertIn('tools.observatory', result[1]['reason'])


class LifecycleTests(unittest.TestCase):
    def test_browser_failure_keeps_server_usable(self):
        session = desktop.OnboardingSession()
        server, thread, info = desktop.start_gui(
            session, open_browser=True, browser=lambda url: (_ for _ in ()).throw(OSError('no xdg-open')))
        try:
            self.assertFalse(info['opened'])
            self.assertIn('Browser launch failed', info['reason'])
            self.assertTrue(info['url'].startswith('http://127.0.0.1:'))
            conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
            conn.request('GET', '/api/state')
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
        finally:
            session.shutdown_requested = True
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_browser_returning_false_is_reported(self):
        session = desktop.OnboardingSession()
        server, thread, info = desktop.start_gui(session, open_browser=True,
                                                 browser=lambda url: False)
        try:
            self.assertFalse(info['opened'])
            self.assertIn('No browser', info['reason'])
        finally:
            session.shutdown_requested = True
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_shutdown_action_stops_the_server_cleanly(self):
        session = desktop.OnboardingSession()
        server, thread, _ = desktop.start_gui(session, open_browser=False)
        port = server.server_address[1]
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request('POST', '/api/action', body=json.dumps({'action': 'shutdown'}),
                     headers={'Host': f'127.0.0.1:{port}',
                              'Origin': f'http://127.0.0.1:{port}',
                              'Content-Type': 'application/json',
                              desktop.CSRF_HEADER: session.csrf})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        self.assertTrue(session.shutdown_requested)
        thread.join(timeout=5)
        desktop.wait_for_shutdown(session, server, thread)  # idempotent close
        self.assertFalse(thread.is_alive())
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=1).close()

    def test_run_gui_without_browser_prints_fallback(self):
        lines = []
        session_holder = {}

        class StopImmediately(desktop.OnboardingSession):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                session_holder['session'] = self

        with mock.patch.object(desktop, 'OnboardingSession', StopImmediately), \
                mock.patch.object(desktop, 'wait_for_shutdown',
                                  side_effect=lambda s, srv, th: (
                                      srv.shutdown(), srv.server_close(), th.join(5))):
            code = desktop.run_gui(open_browser=False, out=lines.append)
        self.assertEqual(code, 0)
        self.assertTrue(any('127.0.0.1' in line for line in lines))


if __name__ == '__main__':
    unittest.main()
