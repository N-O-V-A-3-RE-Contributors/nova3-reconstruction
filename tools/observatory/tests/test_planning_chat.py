"""Offline tests for the owner/admin Plan & Chat workspace (#140).

Every test uses temporary per-user stores, scripted providers and fake
authorization. Only an isolated Python interpreter is spawned; no network,
paid model call or real
GitHub transport is reachable from this file.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pc = _load('nova_planning_chat_tests', TOOLS / 'planning_chat.py')
o = _load('nova_observer_plan_chat_tests', TOOLS / 'observer.py')


def allowed(role='ADMIN', **extra):
    return {'allowed': role == 'ADMIN', 'role': role,
            'reason': 'fixture ' + role, 'account': 'fixture-user',
            'organization_owner': role == 'ADMIN', 'source': 'fixture'}


def proposal(**overrides):
    raw = {
        'schema_version': 1,
        'key': 'plan-chat-fixture',
        'objective': 'Exercise a reviewed planning proposal offline.',
        'title': 'Fixture planning draft',
        'body': 'Synthetic body for offline tests.',
        'allowed_paths': ['tools/observatory/planning_chat.py'],
        'prerequisites': [139],
        'evidence': ['https://github.com/fixture/repo/issues/139'],
        'acceptance': ['Draft stays non-queued'],
        'test_commands': [['python3', '-m', 'unittest']],
        'priority': 50,
        'risk': 'low',
        'privacy': 'public-metadata',
        'requires_owner_review': True,
        'source_standard': {'compiler_abi': 'unknown', 'tu_object_first': False,
                            'original_match': 'not_claimed'},
        'estimate': {'cost_micro_usd': 0, 'runtime_minutes': 1, 'confidence': 'low'},
    }
    raw.update(overrides)
    return raw


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'state'

    def build(self, *, role='ADMIN', adapters=None, user='owner', clock=None, budget=None):
        holder = {'role': role}

        def role_provider():
            return allowed(holder['role'])

        workspace = pc.ChatWorkspace(
            store_root=self.root, role_provider=role_provider, user=user,
            adapters=(adapters if adapters is not None else
                      {'hermes': pc.ScriptedProvider('First synthetic answer.')}),
            clock=clock, budget=budget)
        workspace._fixture_role = holder
        return workspace

    def test_thread_decisions_autosave_and_privacy_state(self):
        workspace = self.build()
        session = workspace.create_thread('Premium plan')
        self.assertTrue(workspace.state()['thread'])
        result = workspace.run_turn('Which target should stay primary?')
        self.assertEqual(result['status'], 'ok')
        self.assertIn('SAVED', workspace.state()['autosave'])
        decision = workspace.pin_decision('Keep Premium retail as the primary target',
                                          'debug information available')
        workspace.set_objective('Reconstruct the Premium engine')
        workspace.add_next_action('Verify DWARF coverage')
        workspace.add_question('Which compiler emitted the Premium ELF?')
        state = workspace.state()
        self.assertTrue(state['allowed'])
        self.assertEqual(state['objective'], 'Reconstruct the Premium engine')
        self.assertIn('Verify DWARF coverage', state['next_actions'])
        self.assertIn('Which compiler emitted the Premium ELF?', state['open_questions'])
        self.assertIn('PRIVATE', state['privacy'])
        self.assertEqual(len(state['transcript']), 2)
        self.assertTrue(state['transcript'][1]['untrusted'])
        self.assertEqual(workspace.decisions()[0]['entry_id'], decision['entry_id'])
        self.assertNotIn(str(self.root), json.dumps(state))
        workspace.close()

    def test_hundred_plus_turns_compacts_and_recovers_decision(self):
        budget = pc.planning_memory.Budget(max_tokens=1400, reserve_tokens=200,
                                           recent_turns=6, max_summaries=4,
                                           summary_tokens=80, max_ledger_items=8)
        workspace = self.build(budget=budget,
                               adapters={'hermes': pc.ScriptedProvider('acknowledged')})
        workspace.create_thread('Long planning run')
        workspace.pin_decision('The durable ledger outranks any transcript window',
                               'required for resumption')
        workspace.set_objective('Prove automatic memory recovery')
        for index in range(120):
            result = workspace.run_turn(f'turn {index}: synthetic planning detail')
            self.assertEqual(result['status'], 'ok')
        state = workspace.state()
        self.assertGreaterEqual(state['context']['compactions'], 1)
        self.assertGreaterEqual(state['context']['summaries'], 1)
        self.assertTrue(state['context_refreshed'])
        self.assertLessEqual(len(state['transcript']), 40)
        memory = workspace._open_memory()
        envelope = memory.context(workspace.current_session())
        accepted = [row['text'] for row in envelope['ledger']['accepted_decisions']]
        self.assertIn('The durable ledger outranks any transcript window', accepted)
        goals = [row['text'] for row in envelope['ledger']['goals']]
        self.assertIn('Prove automatic memory recovery', goals)
        self.assertGreaterEqual(len(workspace.checkpoints()), 120)
        workspace.close()

    def test_restart_resume_and_provider_switch_preserve_memory_identity(self):
        fixed = lambda: 1_800_000_000.0
        workspace = self.build(clock=fixed, adapters={
            'hermes': pc.ScriptedProvider('hermes reply'),
            'codex': pc.ScriptedProvider('codex reply'),
            'gemini': pc.ScriptedProvider('gemini reply')})
        session = workspace.create_thread('Provider neutral plan')['session_id']
        workspace.run_turn('Remember this requirement')
        workspace.pin_decision('Premium retail remains the reference target', 'primary')
        checksums = []
        for provider in ('hermes', 'codex', 'gemini'):
            workspace.set_provider(provider)
            checksums.append(workspace._open_memory().context(
                session)['checksum'])
        for provider in ('hermes', 'codex', 'gemini'):
            workspace.set_provider(provider)
            workspace.run_turn('continue under ' + provider)
        workspace.set_provider('gemini')
        self.assertEqual(len(set(checksums)), 1, 'provider hint must not change memory identity')
        active = workspace.state()['thread']
        self.assertEqual(active['provider'], 'gemini')
        workspace.close()

        resumed = self.build()
        rows = resumed.thread_rows()
        self.assertEqual([row['title'] for row in rows], ['Provider neutral plan'])
        resumed.select_thread(session)
        envelope = resumed._open_memory().context(session)
        texts = [row['text'] for row in envelope['ledger']['accepted_decisions']]
        self.assertIn('Premium retail remains the reference target', texts)
        self.assertEqual(resumed.state()['thread']['provider'], 'gemini')
        resumed.close()

    def test_contributor_denied_and_store_never_created(self):
        workspace = self.build(role='MAINTAIN')
        state = workspace.state()
        self.assertFalse(state['allowed'])
        self.assertEqual(state['mode'], 'help')
        self.assertTrue(any('owner/admin only' in line for line in state['contributor_help']))
        for action in (lambda: workspace.create_thread('x'),
                       lambda: workspace.run_turn('hello'),
                       lambda: workspace.pin_decision('nope'),
                       lambda: workspace.export_private(),
                       lambda: workspace.draft_proposal(proposal())):
            with self.assertRaises(pc.ChatError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'owner-required')
        self.assertFalse((self.root / 'users').exists(),
                         'contributor must not create owner planning memory')
        workspace.close()

    def test_role_revoked_mid_session_blocks_every_action(self):
        workspace = self.build()
        workspace.create_thread('Revocation plan')
        workspace.run_turn('first message')
        workspace._fixture_role['role'] = 'READ'
        for action in (lambda: workspace.run_turn('second message'),
                       lambda: workspace.pin_decision('should not save'),
                       lambda: workspace.checkpoints(),
                       lambda: workspace.export_private()):
            with self.assertRaises(pc.ChatError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'owner-required')
        state = workspace.state()
        self.assertEqual(state['mode'], 'help')
        self.assertEqual(state['transcript'], [])
        workspace.close()

    def test_provider_switch_never_touches_worker_or_starts_service(self):
        workspace = self.build()
        workspace.create_thread('Worker safety')
        workspace.run_turn('plan while a worker is editing')
        before = workspace.state()['thread']
        workspace.cycle_provider()
        workspace.cycle_model()
        workspace.run_turn('continue planning')
        after = workspace.state()['thread']
        self.assertNotEqual(before['provider'], after['provider'])
        self.assertFalse(hasattr(pc, 'worker'))
        self.assertFalse(hasattr(pc.ChatWorkspace, 'stop_worker'))
        workspace.close()

    def test_provider_crash_is_reported_and_retry_recovers(self):
        crashing = pc.ScriptedProvider(crash='simulated adapter crash')
        workspace = self.build(adapters={'hermes': crashing})
        workspace.create_thread('Crash plan')
        result = workspace.run_turn('will crash')
        self.assertEqual(result['status'], 'provider-error')
        self.assertIn('simulated adapter crash', result['error'])
        self.assertIn('NOT saved', workspace.state()['autosave'])
        self.assertTrue(workspace.state()['can_retry'])
        workspace.adapters['hermes'] = pc.ScriptedProvider('recovered reply')
        self.assertEqual(workspace.retry()['status'], 'ok')
        self.assertNotIn('ERROR', workspace.state()['autosave'])
        workspace.close()

    def test_unavailable_provider_shows_honest_state_without_fallback(self):
        workspace = self.build(adapters={})
        workspace.create_thread('Unsupported provider')
        result = workspace.run_turn('hello')
        self.assertEqual(result['status'], 'provider-unavailable')
        self.assertIn('NOT CONNECTED', result['error'])
        self.assertEqual(len(workspace.transcript()), 1, 'only the user turn is saved')
        self.assertEqual(workspace.state()['thread']['provider_state'], 'NOT CONNECTED')
        workspace.close()

    def test_cancellation_keeps_partial_turn_and_never_claims_success(self):
        samples = []

        class SlowProvider:
            def stream(self, prompt, context, *, emit, cancelled, provider, model):
                for letter in 'abcdefgh':
                    emit({'kind': 'delta', 'text': letter})
                    if len(samples) == 1:
                        cancelled.set()
                    if cancelled.is_set():
                        raise pc.Cancelled()
                    samples.append(letter)
                return 'unreachable'

        workspace = self.build(adapters={'hermes': SlowProvider()})
        workspace.create_thread('Cancel plan')
        result = workspace.run_turn('cancel this', on_event=lambda event: samples.append('event'))
        self.assertEqual(result['status'], 'cancelled')
        self.assertIn('cancelled', result['error'])
        self.assertIn('reply NOT saved', workspace.state()['autosave'])
        self.assertEqual(len(workspace.transcript()), 1)
        workspace.close()

    def test_corrupt_ledger_recovers_or_reports_error_honestly(self):
        user_dir = self.root / 'users' / pc._user_key('owner') / 'memory'
        user_dir.mkdir(parents=True)
        (user_dir / 'memory.sqlite3').write_bytes(b'not a sqlite database at all')
        workspace = self.build()
        try:
            workspace.create_thread('Recovered plan')
            workspace.run_turn('after corruption')
            state = workspace.state()
            self.assertEqual(state['context']['state'], 'HEALTHY')
            self.assertFalse(state['context_refreshed'])
            self.assertIn('SAVED', state['autosave'])
        except pc.ChatError as exc:
            # Refusing is acceptable only if it is explicit, never silent.
            self.assertEqual(exc.code, 'ledger')
            self.assertNotIn('Traceback', str(exc))
        workspace.close()

    def test_stale_evidence_is_reported_not_called_verified(self):
        now = {'value': 1_000_000.0}
        budget = pc.planning_memory.Budget(max_tokens=2000, reserve_tokens=200,
                                           recent_turns=4, max_summaries=2,
                                           summary_tokens=60, max_ledger_items=8)
        workspace = self.build(budget=budget, clock=lambda: now['value'])
        workspace.create_thread('Stale evidence plan')
        workspace._open_memory().remember(
            workspace.current_session(), 'fact', 'Synthetic stale observation',
            status='verified', writer='owner', writer_kind='human', importance=70,
            stale_after=5,
            evidence=[{'locator': 'fixture', 'digest': 'a' * 64}])
        self.assertEqual(workspace.state()['context']['stale'], 0)
        now['value'] += 60
        stale = workspace.state()['context']['stale']
        self.assertGreaterEqual(stale, 1)
        self.assertNotIn('Synthetic stale observation',
                         [row['text'] for row in workspace.state()['transcript']])
        workspace.close()

    def test_cross_user_isolation(self):
        first = self.build(user='alice')
        session = first.create_thread('Alice private plan')['session_id']
        first.pin_decision('Alice-only decision')
        first.close()
        second = self.build(user='bob')
        self.assertEqual(second.thread_rows(), [])
        self.assertIsNone(second.current_session())
        state = second.state()
        self.assertFalse(state['threads'])
        self.assertNotEqual((self.root / 'users' / pc._user_key('alice')),
                            (self.root / 'users' / pc._user_key('bob')))
        second.close()
        reopened = self.build(user='alice')
        reopened.select_thread(session)
        self.assertIn('Alice-only decision',
                      [row['text'] for row in reopened.decisions()])
        reopened.close()

    def test_checkpoint_inspection_and_private_export(self):
        workspace = self.build()
        workspace.create_thread('Checkpoint plan')
        workspace.run_turn('one')
        workspace.run_turn('two')
        detail = workspace.inspect_checkpoint(-1)
        row = detail['checkpoint']
        self.assertTrue(row['checkpoint_id'].startswith('ck_'))
        self.assertTrue(row['checksum'])
        self.assertIn('sealed', ' '.join(['sealed']))
        exported = workspace.export_private()
        path = Path(exported['path'])
        self.assertTrue(path.exists())
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(path.parent, workspace.exports_dir)
        with self.assertRaises(pc.ChatError) as caught:
            workspace.export_private(pc._REPO_ROOT / 'docs' / 'leak.json')
        self.assertEqual(caught.exception.code, 'private-export')
        self.assertFalse((pc._REPO_ROOT / 'docs' / 'leak.json').exists())
        workspace.close()

    def test_transcript_and_tool_events_are_redacted(self):
        secret = 'ghp_' + 'A' * 30

        class LeakyProvider:
            def stream(self, prompt, context, *, emit, cancelled, provider, model):
                emit({'kind': 'delta', 'text': 'token ' + secret})
                emit({'kind': 'tool', 'name': 'read', 'state': 'ok',
                      'summary': 'read /home/jason/secret.txt with ' + secret})
                emit({'kind': 'reasoning', 'text': 'private chain of thought'})
                return 'answer mentions ' + secret + ' and /home/jason/private'

        workspace = self.build(adapters={'hermes': LeakyProvider()})
        workspace.create_thread('Privacy plan')
        workspace.run_turn('my key is ' + secret)
        state = workspace.state()
        payload = json.dumps(state)
        self.assertNotIn(secret, payload)
        self.assertNotIn('/home/jason', payload)
        self.assertNotIn('private chain of thought', payload)
        self.assertTrue(any(event['kind'] == 'tool' for event in state['events']))
        self.assertFalse(any(event['kind'] == 'reasoning' for event in state['events']))
        workspace.close()

    def test_proposal_draft_is_local_and_publish_requires_typed_consent(self):
        workspace = self.build()
        workspace.create_thread('Proposal plan')
        draft = workspace.draft_proposal(proposal())
        self.assertEqual(draft['state'], 'LOCAL DRAFT')
        self.assertTrue(Path(draft['path']).exists())
        self.assertEqual(os.stat(draft['path']).st_mode & 0o777, 0o600)
        drafts = workspace.proposals()
        self.assertEqual(len(drafts), 1)
        self.assertIn('LOCAL DRAFT', drafts[0]['state'])
        with self.assertRaises(pc.ChatError) as wrong:
            workspace.publish_proposal(draft['fingerprint'], confirmation='yes')
        self.assertEqual(wrong.exception.code, 'confirmation')
        with self.assertRaises(pc.ChatError) as typo:
            workspace.publish_proposal(draft['fingerprint'],
                                       confirmation=workspace.publish_phrase('x' * 64))
        self.assertEqual(typo.exception.code, 'confirmation')
        workspace.close()

    def test_offline_github_publish_fails_without_creating_anything(self):
        class OfflineTransport:
            created = []

            def issue_list(self, owner, name, *, state='open'):
                raise OSError('network is offline')

            def create_issue(self, *args, **kwargs):
                self.created.append(args)
                raise OSError('should not be reached')

        workspace = self.build()
        workspace.create_thread('Offline publish plan')
        draft = workspace.draft_proposal(proposal(key='offline-publish'))
        transport = OfflineTransport()
        with self.assertRaises(pc.ChatError) as caught:
            workspace.publish_proposal(
                draft['fingerprint'],
                confirmation=workspace.publish_phrase(draft['fingerprint']),
                transport=transport)
        self.assertEqual(caught.exception.code, 'github-offline')
        self.assertEqual(transport.created, [])
        self.assertTrue(workspace.proposals()[0]['state'].startswith('LOCAL DRAFT'))
        workspace.close()

    def test_publish_uses_fresh_authorization_after_revocation(self):
        calls = []

        class RecordingTransport:
            def issue_list(self, owner, name, *, state='open'):
                calls.append('list')
                return []

            def create_issue(self, owner, name, title, body, labels):
                calls.append('create')
                return {'number': 999, 'html_url': 'https://github.com/fixture/repo/issues/999'}

            def viewer(self):
                return {'login': 'fixture-user'}

        workspace = self.build()
        workspace.create_thread('Revoked publish plan')
        draft = workspace.draft_proposal(proposal(key='revoked-publish'))
        workspace._fixture_role['role'] = 'WRITE'
        with self.assertRaises(pc.ChatError) as caught:
            workspace.publish_proposal(
                draft['fingerprint'],
                confirmation=workspace.publish_phrase(draft['fingerprint']),
                transport=RecordingTransport())
        self.assertEqual(caught.exception.code, 'owner-required')
        self.assertEqual(calls, [])
        workspace.close()

    def test_prose_is_never_turned_into_a_task(self):
        workspace = self.build()
        workspace.create_thread('No spontaneous tasks')
        workspace.run_turn('please create tasks for everything')
        with self.assertRaises(pc.ChatError) as caught:
            workspace.draft_from_reply()
        self.assertEqual(caught.exception.code, 'proposal')
        self.assertEqual(workspace.proposals(), [])
        reply = json.dumps(proposal(key='json-only-draft'))
        workspace.draft_from_reply(reply)
        self.assertEqual(len(workspace.proposals()), 1)
        workspace.close()

    def test_archive_hides_thread_but_retains_sealed_checkpoints(self):
        workspace = self.build()
        session = workspace.create_thread('Archive plan')
        workspace.run_turn('keep me')
        result = workspace.archive_thread()
        self.assertEqual(result['archived'], session['session_id'])
        self.assertEqual(workspace.thread_rows(), [])
        self.assertFalse((workspace.memory_dir / 'memory.sqlite3').stat().st_size == 0)
        reopened = self.build()
        self.assertEqual(reopened.thread_rows(), [])
        reopened.close()
        workspace.close()


class StrictSurface:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.refreshes = 0

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.cells = [[' '] * self.width for _ in range(self.height)]

    def refresh(self):
        self.refreshes += 1

    def addnstr(self, y, x, text, count, style):
        assert 0 <= y < self.height and 0 <= x < self.width, (y, x)
        assert 0 <= count <= len(text), count
        assert x + o.ui_components.cell_width(text[:count]) < self.width, (y, x, text)
        assert all(ord(c) >= 32 and c != '\x7f' for c in text)
        for c in text[:count]:
            width = o.ui_components.cell_width(c)
            if width:
                self.cells[y][x] = c
                x += width

    def line(self, y):
        return ''.join(self.cells[y]).rstrip()

    def text(self):
        return '\n'.join(self.line(y) for y in range(self.height))


class PlanScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'state'

    def build(self, width=80, height=24, role='ADMIN', adapters=None, state=None, store=None):
        access = state if state is not None else {'role': role}
        snap = o.Snapshot(repo_role=access['role'])
        collector = SimpleNamespace(
            repo='fixture/repo', service='fixture.service', demo=False,
            lock=threading.Lock(), snapshot=snap, merge_preview=None,
            perform_action=mock.Mock(), execute_merge=mock.Mock(),
            prepare_merges=mock.Mock(), force_refresh=mock.Mock(),
            state_dir=self.root if store is None else store,
            viewer_login='fixture-user',
            owner_access_provider=lambda: allowed(access['role']),
            planning_adapters=adapters if adapters is not None else {
                'hermes': pc.ScriptedProvider('Synthetic streamed answer.'),
                'codex': pc.ScriptedProvider('Codex synthetic answer.')},
        )
        ui = o.Observatory(collector)
        ui.snap, ui.stdscr, ui.unicode = snap, StrictSurface(width, height), True
        return ui

    def open_plan(self, ui):
        ui.tab = 0
        ui.handle(ord('C'))
        self.assertTrue(ui.plan_mode)
        ui.render()

    def type_text(self, ui, text):
        for character in text:
            ui.handle(ord(character))

    def wait_plan(self, ui, timeout=5):
        job = ui.plan_job
        if job is not None:
            job.join(timeout)
        ui._drain_plan_events()
        ui.render()

    def test_plan_page_renders_80x24_and_192x54_with_required_status(self):
        for width, height in ((80, 24), (192, 54)):
            ui = self.build(width, height, store=self.root / f'{width}x{height}')
            self.open_plan(ui)
            text = ui.stdscr.text()
            self.assertIn('PLAN & CHAT', text)
            self.assertIn('No planning thread yet', text)
            ui.handle(ord('t'))
            ui.handle(ord('i'))
            self.type_text(ui, 'First owner message')
            ui.handle(10)
            self.wait_plan(ui)
            text = ui.stdscr.text()
            for expected in ('PRIVACY', 'AUTOSAVE', 'CONTEXT', 'OBJECTIVE', 'hermes',
                             'untrusted'):
                self.assertIn(expected, text)
            if width == 192:
                self.assertIn('THREAD / PROVIDER', text)
                self.assertIn('TASK PROPOSALS', text)
            self.assertIn('Synthetic', text)
            ui.collector.perform_action.assert_not_called()
            ui.collector.execute_merge.assert_not_called()
            ui.planning.close()

    def test_input_survives_resize_and_cancel_discards_by_choice(self):
        ui = self.build()
        self.open_plan(ui)
        ui.handle(ord('t'))
        ui.handle(ord('t'))  # second thread, still a local draft only
        ui.handle(ord('i'))
        self.type_text(ui, 'half-written prompt')
        before = ui.plan_buffer
        for width, height in ((192, 54), (100, 30), (80, 24), (120, 40)):
            ui.stdscr = StrictSurface(width, height)
            self.assertTrue(ui.handle(o.curses.KEY_RESIZE))
            ui.render()
            self.assertEqual(ui.plan_buffer, before)
        ui.handle(27)
        self.assertIsNone(ui.plan_input)
        self.assertEqual(ui.plan_buffer, '')
        ui.planning.close()

    def test_provider_switch_focus_and_cancel_keys(self):
        ui = self.build()
        self.open_plan(ui)
        ui.handle(ord('t'))
        ui.handle(ord('p'))
        self.assertIn('no worker touched', ui.notice)
        ui.handle(ord('m'))
        self.assertIn('planning session only', ui.notice)
        ui.handle(ord('x'))
        self.assertIn('No streaming response', ui.notice)
        ui.handle(ord('['))
        ui.handle(ord(']'))
        ui.handle(ord('k'))
        self.assertIn('checkpoint', ui.notice.lower())
        ui.collector.perform_action.assert_not_called()
        ui.planning.close()

    def test_contributor_surface_reads_no_owner_memory(self):
        ui = self.build(role='WRITE')
        self.open_plan(ui)
        text = ui.stdscr.text()
        self.assertIn('OWNER / ADMIN ONLY', text)
        self.assertIn('never opened', text)
        self.assertFalse((self.root / 'users').exists())
        for key in 'tpivxekg':
            ui.handle(ord(key))
        self.assertNotIn('planning thread created', ui.notice)
        if ui.planning is not None:
            ui.planning.close()

    def test_role_revoked_between_render_and_send(self):
        access = {'role': 'ADMIN'}
        ui = self.build(state=access)
        self.open_plan(ui)
        ui.handle(ord('t'))
        self.assertIn('created', ui.notice)
        access['role'] = 'READ'
        ui.handle(ord('i'))
        self.type_text(ui, 'this must be denied')
        ui.handle(10)
        self.wait_plan(ui)
        self.assertIn('DENIED', ui.notice)
        self.assertIn('owner-required', ui.notice)
        ui.render()
        self.assertIn('OWNER / ADMIN ONLY', ui.stdscr.text())
        ui.planning.close()

    def test_proposal_keys_never_publish_without_phrase(self):
        ui = self.build()
        self.open_plan(ui)
        ui.handle(ord('t'))
        ui.handle(ord('g'))
        self.assertIn('no structured proposal JSON', ui.notice)
        self.assertIsNone(ui.plan_phrase)
        ui.handle(ord('b'))
        self.assertIn('No local draft', ui.notice)
        ui.planning.close()

    def test_stream_can_be_cancelled_from_the_keyboard(self):
        class SlowAdapter:
            def stream(self, prompt, context, *, emit, cancelled, provider, model):
                for letter in 'abcdefg':
                    emit({'kind': 'delta', 'text': letter})
                    for _ in range(50):
                        if cancelled.is_set():
                            raise pc.Cancelled()
                        threading.Event().wait(0.002)
                return 'finished too late'

        ui = self.build(adapters={'hermes': SlowAdapter()})
        self.open_plan(ui)
        ui.handle(ord('t'))
        ui.handle(ord('i'))
        self.type_text(ui, 'cancel me')
        ui.handle(10)
        for _ in range(200):
            ui.render()
            if 'STREAMING' in ui.stdscr.text():
                break
            time.sleep(0.005)
        self.assertIn('STREAMING', ui.stdscr.text())
        ui.handle(ord('x'))
        self.assertEqual(ui.notice, 'Cancel requested')
        self.wait_plan(ui)
        self.assertIn('cancelled', ui.notice.lower())
        self.assertEqual(len(ui.planning.transcript()), 1)
        ui.planning.close()

    def test_export_and_archive_confirmation(self):
        ui = self.build()
        self.open_plan(ui)
        ui.handle(ord('t'))
        ui.handle(ord('e'))
        self.assertIn('Private export written', ui.notice)
        ui.handle(ord('d'))
        self.assertEqual(ui.confirm, 'plan-archive')
        ui.handle(ord('y'))
        self.assertIn('sealed local checkpoints retained', ui.notice)
        ui.planning.close()

    def test_plan_hotkeys_never_trigger_global_worker_actions(self):
        ui = self.build()
        self.open_plan(ui)
        ui.handle(ord('t'))
        for key in 'sxrfiq':
            ui.handle(ord(key))
        ui.collector.perform_action.assert_not_called()
        self.assertIsNone(ui.confirm)


class InstalledWorkspaceTests(unittest.TestCase):
    def test_isolated_install_keeps_ledger_and_denies_unconnected_provider(self):
        import shutil
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / 'share' / 'control-center'
            backend = bundle / 'planning_backend'
            backend.mkdir(parents=True)
            shutil.copyfile(TOOLS / 'planning_chat.py', bundle / 'planning_chat.py')
            for name in ('access_policy.py', 'planning_bridge.py', 'planning_memory.py'):
                shutil.copyfile(TOOLS.parents[1] / 'nova3_team' / name, backend / name)
            script = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import planning_chat as pc
state = Path(sys.argv[2])
role = {'allowed': True, 'role': 'ADMIN', 'organization_owner': False}
workspace = pc.ChatWorkspace(store_root=state, user='fixture', role_provider=lambda: role)
workspace.create_thread('Installed ledger')
workspace.pin_decision('Preserve the installed decision', 'isolated fixture')
assert workspace.run_turn('No provider may be launched')['status'] == 'provider-unavailable'
assert workspace.state()['thread']['provider_state'] == 'NOT CONNECTED'
workspace.close()
workspace = pc.ChatWorkspace(store_root=state, user='fixture', role_provider=lambda: role)
assert workspace.decisions()[0]['text'] == 'Preserve the installed decision'
role.update(allowed=False, role='READ')
try:
    workspace.pin_decision('Denied decision')
except pc.ChatError:
    pass
else:
    raise AssertionError('revoked role wrote to ledger')
workspace.close()
print('installed ledger and authorization passed')
'''
            result = subprocess.run(
                [sys.executable, '-B', '-I', '-c', script, str(bundle), str(root / 'state')],
                cwd=root, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('installed ledger and authorization passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
