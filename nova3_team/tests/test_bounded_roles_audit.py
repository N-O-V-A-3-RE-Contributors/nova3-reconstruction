"""Independent offline controller/role wiring audit; no provider calls."""
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from nova3_team import model_agents as agents, task_agents, delivery_backend as backend
from nova3_team import dsh_runtime as runtime, dsh_model_router as routing


class BoundedRolesAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.base = runtime.DshEndpoint('unused-binary', self.root)

    def test_route_overlay_preserves_role_guard_and_step_bound(self):
        endpoint = agents.role_endpoint(self.base, role='reviewer', cwd=self.root,
                                       folder=self.root / 'private', max_steps=12)
        role = json.loads(endpoint.role_patch.read_text())
        route = routing.Route(provider='configured', model='configured-model', key_ref='configured-key')
        selected = routing.write_route(endpoint, route, self.root / 'routes')
        overlay = json.loads(selected.patch.read_text())
        self.assertEqual(overlay[-len(role):], role)
        self.assertEqual(selected.max_steps, 12)
        self.assertEqual(selected.role_patch, endpoint.role_patch)
        self.assertIn(str(selected.patch), selected.command(session_id=None))
        self.assertEqual(selected.patch.stat().st_mode & 0o777, 0o600)
        self.assertTrue(any('insert' in patch for patch in overlay))

    def test_role_read_scope_tracks_git_files_and_fails_closed(self):
        with mock.patch.object(agents.subprocess, 'run', return_value=SimpleNamespace(
                stdout='reconstruction/source.cpp\0docs/public.md\0')) as git:
            endpoint = agents.role_endpoint(self.base, role='reviewer', cwd=self.root,
                folder=self.root / 'tracked')
        config = json.loads(endpoint.role_patch.read_text())[-1]['insert'][0]['config']
        self.assertEqual(config['allowedReads'], ['reconstruction/source.cpp', 'docs/public.md'])
        self.assertEqual(git.call_args.args[0], ['git', 'ls-files', '-z'])
        with mock.patch.object(agents.subprocess, 'run', side_effect=OSError('offline git')):
            endpoint = agents.role_endpoint(self.base, role='reviewer', cwd=self.root,
                folder=self.root / 'unavailable', allowed=['untracked.cpp'])
            config = json.loads(endpoint.role_patch.read_text())[-1]['insert'][0]['config']
            self.assertEqual(config['allowedReads'], [])
            endpoint = agents.role_endpoint(self.base, role='repairer', cwd=self.root,
                folder=self.root / 'repair', allowed=['new.cpp'])
            config = json.loads(endpoint.role_patch.read_text())[-1]['insert'][0]['config']
            self.assertEqual(config['allowedReads'], ['new.cpp'])

    def test_role_identity_is_runtime_not_model_claim(self):
        outcome = runtime.SessionOutcome(status=runtime.COMPLETED, exit_code=0,
            session_id='real-runtime-id', turn_reason=None, error_code=None,
            error_message='', steps=1, tool_calls=0, elapsed_s=0,
            events=({'type': 'final', 'text': '{"session_id":"forged","role":"reviewer"}'},))
        with mock.patch.object(routing.Router, 'run', return_value=outcome):
            answer = agents.run_agent(role='repairer', prompt='bounded task', cwd=self.root,
                folder=self.root / 'role', task='source', allowed=['source.cpp'], base=self.base)
        self.assertEqual(answer['execution']['session_id'], 'real-runtime-id')
        self.assertEqual(answer['execution']['role'], 'repairer')
        self.assertFalse(answer['execution']['read_only'])

    def test_collaborators_are_bounded_cached_advisory_and_primary_runs_once(self):
        primary = mock.Mock(return_value='primary-result')
        child = mock.Mock(side_effect=lambda **kw: {'execution': {'session_id': kw['role']},
                                                   'text': 'suggestion only'})
        prompt = 'ALLOWED EDIT PATHS (strict): reconstruction/source.cpp, reconstruction/tests/test.cpp\nImplement real source.'
        with mock.patch.object(task_agents.subprocess, 'run', return_value=SimpleNamespace(stdout='head')):
            for _ in range(2):
                self.assertEqual(task_agents.run_with_collaborators(prompt, cwd=self.root,
                    log_path=self.root / 'log', timeout_s=600, run_primary=primary,
                    base_endpoint=self.base, task={'title': 'Recover matrix'},
                    ledger_append=mock.Mock(), cancel=lambda: False, child_runner=child), 'primary-result')
        self.assertEqual(child.call_count, 2)
        self.assertEqual(primary.call_count, 2)
        for call in child.call_args_list:
            self.assertLessEqual(call.kwargs['timeout_s'], 120)
            self.assertLessEqual(call.kwargs['max_steps'], 10)
            self.assertIn(call.kwargs['role'], ('analysis', 'test_design'))
        self.assertIn('"verified": false', primary.call_args.args[0])
        self.assertLessEqual(primary.call_args.args[1], 600)

    def test_repair_refuses_remote_head_race_before_add_commit_push(self):
        cwd = self.root / 'checkout'; cwd.mkdir()
        (cwd / 'source.cpp').write_text('source')
        head = 'a' * 40
        context = {'repo': 'owner/repo', 'pr': {'number': 1, 'head': {
            'sha': head, 'ref': 'repair', 'repo': {'full_name': 'owner/repo'}}},
            'task': {'title': 'source task', 'allowed_paths': ['source.cpp']}, 'diff': 'changed source'}
        runner = mock.Mock(return_value={'text': '{"blocked":false}',
                                        'execution': {'role': 'repairer', 'session_id': 'r'}})
        engine = backend.RuntimeBackend(self.root, self.root / 'private',
            api=lambda path: {'head': {'sha': 'b' * 40}}, agent_runner=runner)
        git = mock.Mock(side_effect=lambda root, *args, **kw:
                        'source.cpp' if args[:2] == ('diff', '--name-only') else '')
        with mock.patch.object(engine, '_checkout', return_value=cwd), \
             mock.patch.object(backend, '_git', git), \
             mock.patch('nova3_team.worker.guard_check', return_value=[]):
            with self.assertRaisesRegex(backend.BackendError, 'changed before'):
                engine.repairer(context, [])
        self.assertFalse(any(call.args[1] in ('add', 'commit', 'push') for call in git.call_args_list))

    def test_successful_repair_enforces_ancestry_and_exact_remote_lease(self):
        cwd = self.root / 'checkout'; cwd.mkdir()
        (cwd / 'source.cpp').write_text('source')
        head = 'a' * 40
        published = 'b' * 40
        context = {'repo': 'owner/repo', 'pr': {'number': 1, 'head': {
            'sha': head, 'ref': 'repair', 'repo': {'full_name': 'owner/repo'}}},
            'task': {'title': 'source task', 'allowed_paths': ['source.cpp']}, 'diff': 'source'}
        runner = mock.Mock(return_value={'text': json.dumps({'blocked': False,
            'published_head': 'c' * 40}),
            'execution': {'role': 'repairer', 'session_id': 'r'}})
        engine = backend.RuntimeBackend(self.root, self.root / 'private',
            api=lambda path: {'head': {'sha': head}}, agent_runner=runner)
        def git_answer(root, *args, **kwargs):
            if args[:2] == ('diff', '--name-only'):
                return 'source.cpp'
            if args == ('rev-parse', 'HEAD'):
                return published
            return ''
        git = mock.Mock(side_effect=git_answer)
        with mock.patch.object(engine, '_checkout', return_value=cwd), \
             mock.patch.object(backend, '_git', git), \
             mock.patch('nova3_team.worker.guard_check', return_value=[]):
            result = engine.repairer(context, [])
        self.assertTrue(result['repaired'])
        self.assertEqual(result['published_head'], published)
        self.assertNotEqual(result['published_head'], json.loads(runner.return_value['text'])['published_head'])
        calls = [call.args[1:] for call in git.call_args_list]
        ancestry = ('merge-base', '--is-ancestor', head, published)
        push = ('push', f'--force-with-lease=refs/heads/repair:{head}',
                'https://github.com/owner/repo.git', f'{published}:refs/heads/repair')
        self.assertIn(ancestry, calls)
        self.assertIn(push, calls)
        self.assertLess(calls.index(ancestry), calls.index(push))
        self.assertFalse(any('--force' in args or '-f' in args for args in calls))
        # A failed ancestry gate must leave the repair local, never push.
        git.reset_mock()
        def fail_ancestry(root, *args, **kwargs):
            if args[0] == 'merge-base':
                raise backend.BackendError('not descendant')
            return git_answer(root, *args, **kwargs)
        git.side_effect = fail_ancestry
        with mock.patch.object(engine, '_checkout', return_value=cwd), \
             mock.patch.object(backend, '_git', git), \
             mock.patch('nova3_team.worker.guard_check', return_value=[]):
            with self.assertRaises(backend.BackendError):
                engine.repairer(context, [])
        self.assertFalse(any(call.args[1] == 'push' for call in git.call_args_list))

    def test_invalid_or_unchanged_repair_commit_cannot_publish_model_claimed_sha(self):
        cwd = self.root / 'checkout'; cwd.mkdir()
        (cwd / 'source.cpp').write_text('source')
        head = 'a' * 40
        context = {'repo': 'owner/repo', 'pr': {'number': 1, 'head': {
            'sha': head, 'ref': 'repair', 'repo': {'full_name': 'owner/repo'}}},
            'task': {'title': 'source task', 'allowed_paths': ['source.cpp']}, 'diff': 'source'}
        runner = mock.Mock(return_value={'text': json.dumps({'blocked': False,
            'published_head': 'c' * 40}),
            'execution': {'role': 'repairer', 'session_id': 'r'}})
        engine = backend.RuntimeBackend(self.root, self.root / 'private',
            api=lambda path: {'head': {'sha': head}}, agent_runner=runner)
        for commit in ('', 'not-a-sha', 'b' * 39, 'B' * 40, head):
            with self.subTest(commit=commit):
                def git_answer(root, *args, **kwargs):
                    if args[:2] == ('diff', '--name-only'):
                        return 'source.cpp'
                    return commit if args == ('rev-parse', 'HEAD') else ''
                git = mock.Mock(side_effect=git_answer)
                with mock.patch.object(engine, '_checkout', return_value=cwd), \
                     mock.patch.object(backend, '_git', git), \
                     mock.patch('nova3_team.worker.guard_check', return_value=[]):
                    with self.assertRaisesRegex(backend.BackendError, 'commit identity invalid'):
                        engine.repairer(context, [])
                self.assertFalse(any(call.args[1] in ('merge-base', 'push')
                                     for call in git.call_args_list))

    def test_autopilot_delivery_lock_prevents_overlapping_model_sessions(self):
        import fcntl
        from nova3_team import autopilot
        folder = self.root / 'delivery-owner'; folder.mkdir()
        with (folder / 'delivery.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(autopilot.autoadmit, '_api') as api, \
                 mock.patch.object(backend, 'RuntimeBackend') as factory:
                result = autopilot.merge_validated('owner/repo', folder)
        api.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(result['refused'][0]['why'], 'delivery_busy')

    def test_autopilot_stops_after_one_model_involving_pr(self):
        from nova3_team import autopilot, delivery
        folder = self.root / 'delivery-owner'; folder.mkdir()
        instance = mock.Mock(sessions_started=1)
        with mock.patch.object(autopilot.autoadmit, '_api', return_value=[{'number': 1}, {'number': 2}]), \
             mock.patch.object(backend, 'RuntimeBackend', return_value=instance), \
             mock.patch.object(delivery, 'process_pr', return_value={
                 'state': 'waiting_ci', 'reason': 'pending', 'head': 'a'*40}) as process:
            result = autopilot.merge_validated('owner/repo', folder)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(result['delivery'][0]['state'], 'waiting_ci')
        self.assertEqual(json.loads((folder / 'delivery-cursor.json').read_text())['last_pr'], 1)

    def test_reviewer_keeps_runtime_metadata_and_returns_invalid_text_for_gate(self):
        folder = self.root / 'private'
        cwd = folder / 'checkouts' / 'review'; cwd.mkdir(parents=True)
        execution = {'session_id': 'trusted-runtime', 'role': 'reviewer', 'read_only': True, 'independent': True}
        engine = backend.RuntimeBackend(self.root, folder, api=mock.Mock(),
            agent_runner=mock.Mock(return_value={'text': 'malformed', 'execution': execution}))
        context = {'repo': 'owner/repo', 'pr': {'number': 1, 'head': {'sha': 'a'*40}},
                   'task': {'title': 'source task'}}
        with mock.patch.object(engine, '_checkout', return_value=cwd), \
             mock.patch.object(backend, '_git', return_value='') as git:
            answer = engine.reviewer(context)
        self.assertEqual(answer, {'review': 'malformed', 'execution': execution})
        self.assertTrue(any(call.args[1:3] == ('worktree', 'remove') for call in git.call_args_list))


if __name__ == '__main__':
    unittest.main()
