import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import random
import threading
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from unittest import mock

P = Path(__file__).resolve().parents[1] / 'observer.py'
spec = importlib.util.spec_from_file_location('nova_observer', P)
import sys
sys.modules[spec.name] = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sys.modules[spec.name])
o = sys.modules[spec.name]


def issue(n, label='nova:queued', title=None, state='OPEN'):
    return {'number': n, 'title': title or f'Reconstruct libNOVA3_neon.so functions 0x00{n:06x}',
            'state': state, 'labels': [{'name':label}], 'createdAt': '2026-10-25T08:00:00Z'}


def pr(n, num, merged=False, state='OPEN'):
    return {'number':num,'title':f'#{n}: reconstruction', 'state':'MERGED' if merged else state,
            'createdAt':'2026-10-26T10:00:00Z',
            'mergedAt':'2026-10-26T12:00:00Z' if merged else None}


class Tests(unittest.TestCase):
    def test_issue_stages(self):
        for label, value in [('nova:queued','QUEUED'),('nova:claimed','CLAIMED'),('nova:review','REVIEW'),('nova:blocked','BLOCKED'),('nova:integrated','INTEGRATED'),('nova:verified','VERIFIED')]:
            self.assertEqual(o.issue_stage(issue(29,label)), value)

    def test_blocked_has_priority(self):
        item = issue(30)
        item['labels'].append({'name':'nova:blocked'})
        self.assertEqual(o.issue_stage(item),'BLOCKED')

    def test_dashboard_excluded(self):
        self.assertFalse(o.is_task(issue(16,'nova:dashboard',title='NOVA3 — live reconstruction status')))

    def test_pr_merge_overrides_stale_review(self):
        c=o.task_counts([issue(29, 'nova:review')],[pr(29,51,merged=True)])
        self.assertEqual(c['INTEGRATED'],1)
        self.assertEqual(c['REVIEW'],0)

    def test_closing_issue_not_verified(self):
        c=o.task_counts([issue(29, 'nova:queued', state='CLOSED')],[])
        self.assertEqual(c['QUEUED'],0)
        self.assertEqual(c['CLOSED*'],1)
        self.assertEqual(c['VERIFIED'],0)

    def test_merged_link_on_closing_reference(self):
        p=pr(29,51,merged=True)
        p['title']='Unrelated'
        p['closingIssuesReferences']=[{'number':29}]
        self.assertIn(29,o.merged_issue_ids([p]))

    def test_function_addresses_dedup(self):
        self.assertEqual(len(o.function_addrs('Reconstruct functions 0x00112233, 0x00112233, 0x00223344')),2)

    def test_other_hex_not_function(self):
        self.assertEqual(o.function_addrs('Bug 0x00112233'),set())

    def test_time_parser(self):
        self.assertIsNone(o.dt(None))
        self.assertIsNone(o.dt('bad timestamp'))
        self.assertEqual(o.dt('2026-10-27T08:12:35+1100').utcoffset().total_seconds(),39600)

    def test_event_parse_and_median(self):
        lines=[
            '2026-10-27T08:12:39+1100 archlinux python3[1]: [08:12:39] #29 AI session 1/3 (codex); provider launched',
            '2026-10-27T08:22:39+1100 archlinux python3[1]: [08:22:39] #29 Codex/AI session finished: exit=0; checking changed files',
            '2026-10-27T08:23:39+1100 archlinux python3[1]: [08:23:39] FAILED #29: bad',
        ]
        ev=o.parse_log_events(lines)
        self.assertEqual(ev['last_task'],29)
        self.assertEqual(ev['unfinished'],[])
        self.assertEqual(ev['sessions'][0]['seconds'],600)
        self.assertEqual(o.median_session_seconds(ev),600)
        self.assertEqual(len(ev['failures']),1)

    def test_missing_evidence_has_no_assumed_task_rate(self):
        a=o.build_estimates([issue(29),issue(30)],[],{}, {},workers=1)
        self.assertEqual(a['queue_seconds'], {})
        self.assertIsNone(a['forecast']['queue']['all_supplied_queue_seconds'])
        self.assertIn('incomplete_queue_snapshot', a['forecast']['queue']['reasons'])
        self.assertIsNone(a['throughput_seconds'])

    def test_configured_slots_do_not_create_capacity(self):
        data=[issue(29),issue(30)]
        now=datetime(2026,10,29,tzinfo=timezone.utc)
        one=o.build_estimates(data,[],{}, {},1, now)
        two=o.build_estimates(data,[],{}, {},2, now)
        self.assertEqual(one['forecast']['queue'], two['forecast']['queue'])

    def test_coverage_not_conflated_with_merging(self):
        item=issue(29,title='Reconstruct functions 0x00112233, 0x00112244')
        a=o.build_estimates([item],[pr(29,51,True)],{}, {},1)
        self.assertEqual(a['verified_ledger_functions'],None)
        self.assertEqual(a['integrated_function_addresses'],2)
        self.assertEqual(a['known_functions'],2)

    def test_ledger_reads_known_only(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'coordination';path.mkdir()
            (path/'function_ledger.json').write_text(json.dumps({'functions':[
                {'reference_sha256':'a'*64,'address':'0x1','status':'pending','boundary_status':'tool_identified'},
                {'reference_sha256':'a'*64,'address':'0x2','status':'matched','boundary_status':'verified'},
                {'reference_sha256':'a'*64,'address':'0x2','status':'matched','boundary_status':'verified'}]}))
            inv=o.load_inventory(Path(d))
            self.assertTrue(inv['available'])
            self.assertEqual(inv['count'],2)
            self.assertEqual(inv['verified'],1)

    def test_ledger_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(o.load_inventory(Path(d))['available'])

    def test_ledger_skips_unverified_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'coordination';path.mkdir()
            (path/'function_ledger.json').write_text(json.dumps({'functions':[
                {'reference_sha256':'a'*64,'address':'0x1','boundary_status':'candidate'},
                {'reference_sha256':'a'*64,'address':'0x2','boundary_status':'verified'},
            ]}))
            inv=o.load_inventory(Path(d))
            self.assertEqual(inv['count'],1)

    def test_ledger_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'coordination';path.mkdir()
            (path/'function_ledger.json').write_text('not json')
            self.assertFalse(o.load_inventory(Path(d))['available'])

    def test_health_stopped(self):
        health, reason=o.classify_health({'active':'failed'},None,{}, {})
        self.assertEqual(health,'STOPPED')

    def test_health_idle(self):
        health,_=o.classify_health({'active':'active'},None,{'QUEUED':0,'CLAIMED':0,'BLOCKED':0},{})
        self.assertEqual(health,'IDLE')

    def test_health_blocked(self):
        health,_=o.classify_health({'active':'active'},None,{'QUEUED':5,'CLAIMED':0,'BLOCKED':1},{})
        self.assertEqual(health,'RUNNING')

    def test_health_blocked_but_worker_idle(self):
        health,reason=o.classify_health({'active':'active'},None,
            {'QUEUED':0,'CLAIMED':0,'BLOCKED':3},{})
        self.assertEqual(health,'IDLE')
        self.assertIn('maintainer',reason)

    def test_health_degraded(self):
        health,_=o.classify_health({'active':'active'},'HTTP 500',{'QUEUED':5,'CLAIMED':0,'BLOCKED':0},{})
        self.assertEqual(health,'DEGRADED')

    def test_stage_count_no_data(self):
        self.assertEqual(o.task_counts([],[])['TOTAL'],0)

    def test_private_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'secret'/'snapshot.json'
            o.private_write(p,{'foo':'bar'})
            self.assertEqual(json.loads(p.read_text()),{'foo':'bar'})
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            self.assertEqual(p.parent.stat().st_mode & 0o777,0o700)

    def test_collector_keeps_network_cache(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'snapshot.json'
            o.private_write(path,{'issues':[issue(30)],'prs':[pr(30,51)],'runs':[],'github_updated':'foo','history':[],'github_errors':[]})
            coll=o.Collector('test/repo',Path(d),'fake.service',Path(d))
            self.assertEqual(coll.view().issues[0]['number'],30)

    def test_safe_open_url_only(self):
        co=o.Collector('test/repo',Path('/tmp'), 'test.service',Path('/tmp'),demo=True)
        ui=o.Observatory(co)
        ui.tab=1
        ui.active_records=[{'url':'https://github.com/test/repo/issues/1'}]
        self.assertEqual(ui.selected_url(),'https://github.com/test/repo/issues/1')

    def test_confirm_actions(self):
        c=o.Collector('test/repo',Path('/tmp'), 'test.service',Path('/tmp'),demo=True)
        ui=o.Observatory(c)
        ui.handle(ord('x'))
        self.assertEqual(ui.confirm,'stop')
        ui.handle(ord('n'))
        self.assertIsNone(ui.confirm)
        ui.handle(ord('x'))
        ui.handle(ord('y'))
        self.assertIn('Demo mode',c.view().last_action)

    def test_text_once(self):
        msg=o.text_once(o.sample_snapshot(),1)
        self.assertIn('Queue planning:',msg)
        self.assertIn('IMPORTANT:',msg)

    def test_gh_errors_do_not_wipe_sections(self):
        call=mock.Mock(side_effect=[RuntimeError('offline'),[],[]])
        with mock.patch.object(o,'gh_json',call):
            data=o.gh_collect('test/repo')
            self.assertEqual(data['issues'],[])
            self.assertEqual(len(data['errors']),1)

    def test_zero_matches_never_extrapolates_whole_game(self):
        inv={'available': True,'count':2400,'integrated':0,'verified':0}
        result=o.build_estimates([],[],inv,{},1)
        self.assertEqual(result['whole_game_seconds'], {})
        self.assertEqual(result['forecast']['whole_game']['label'], 'UNKNOWN')
        self.assertIn('zero_verified_original_matches', result['forecast']['whole_game']['reasons'])

    def test_health_quiet_long_session(self):
        now=datetime.now(timezone.utc)
        events={'last_launch': now-timedelta(minutes=51), 'unfinished':[29], 'last_event':now}
        health,_=o.classify_health({'active':'active'},None,{'QUEUED':1,'CLAIMED':1,'BLOCKED':0},events,now)
        self.assertEqual(health,'QUIET')

    def test_gh_data_labels_never_assume_verified(self):
        counts=o.task_counts([issue(29, 'nova:review')],[])
        self.assertEqual(counts['VERIFIED'],0)
        self.assertEqual(counts['REVIEW'],1)

    def test_sparkline_zero(self):
        self.assertEqual(o.simple_sparkline([0,0,0]),'▁▁▁')


class StrictSurface:
    """Composite terminal cells; reject unsafe writes instead of swallowing them."""
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


class ResponsiveTests(unittest.TestCase):
    SIZES = ((80, 24), (100, 30), (120, 40), (160, 45), (192, 54))

    def setUp(self):
        guidance = mock.patch.object(o, 'historical_setup_text', return_value='Synthetic optional toolchain guidance')
        guidance.start()
        self.addCleanup(guidance.stop)

    def make_ui(self, width=80, height=24, role='READ', demo=False):
        snap = o.Snapshot(
            issues=[dict(issue(n, title=f'Synthetic task {n}'), url=f'https://github.com/fixture/repo/issues/{n}')
                    for n in range(1, 32)],
            prs=[dict(pr(n, n+100), isDraft=False, url=f'https://github.com/fixture/repo/pull/{n+100}')
                 for n in range(1, 8)],
            runs=[{'databaseId': 1, 'workflowName': 'Synthetic CI', 'status': 'COMPLETED',
                   'conclusion': 'SUCCESS', 'displayTitle': 'Fixture check',
                   'createdAt': '2026-10-29T00:00:00Z'}],
            repo_role=role, inventory={'available': True, 'verified': 0},
            service={'active': 'active', 'pid': '123'})
        collector = SimpleNamespace(repo='fixture/repo', service='fixture.service', demo=demo,
                                    lock=threading.Lock(), snapshot=snap, merge_preview=None,
                                    perform_action=mock.Mock(), execute_merge=mock.Mock(),
                                    prepare_merges=mock.Mock(), force_refresh=mock.Mock())
        ui = o.Observatory(collector)
        ui.snap, ui.stdscr, ui.unicode = snap, StrictSurface(width, height), True
        return ui

    def test_density_uses_both_live_dimensions(self):
        policy = o.ui_components.Layout
        for size, expected in (((80, 24), 'compact'), ((100, 30), 'compact'),
                               ((120, 40), 'medium'), ((160, 45), 'spacious'),
                               ((192, 54), 'panoramic'), ((192, 24), 'compact'),
                               ((80, 54), 'compact'), ((71, 40), 'tiny'), ((192, 17), 'tiny')):
            self.assertEqual(policy(*size).density, expected)

    def test_registry_and_deterministic_navigation(self):
        self.assertEqual(len(o.TABS), len(o.TAB_KEYS))
        self.assertEqual(len(o.TABS), len(o.TAB_SHORT))
        self.assertEqual(len(set(o.TAB_KEYS)), 14)
        self.assertEqual(sorted(p for _, pages in o.TAB_GROUPS for p in pages), list(range(14)))
        ui = self.make_ui(role='ADMIN')
        for i, key in enumerate(o.TAB_KEYS):
            ui.tab = 0
            ui.handle(ord(key))
            self.assertEqual(ui.tab, i)
        ui.tab = 0
        for i in range(14):
            self.assertEqual(ui.tab, i)
            ui.handle(9)
        self.assertEqual(ui.tab, 0)
        for i in range(13, -1, -1):
            ui.handle(o.curses.KEY_BTAB)
            self.assertEqual(ui.tab, i)
        ui.tab = 0
        for group in range(1, len(o.TAB_GROUPS)+1):
            ui.handle(ord('}'))
            self.assertEqual(ui.current_group(), group % len(o.TAB_GROUPS))
        ui.handle(ord('{'))
        self.assertEqual(ui.current_group(), len(o.TAB_GROUPS)-1)

    def test_every_page_size_role_ascii_and_no_side_effects(self):
        for width, height in self.SIZES:
            for role, demo in (('ADMIN', False), ('MAINTAIN', False), ('WRITE', False),
                               ('READ', False), ('UNKNOWN', False), ('ADMIN', True)):
                ui = self.make_ui(width, height, role, demo)
                for unicode in (True, False):
                    ui.unicode = unicode
                    for tab in range(len(o.TABS)):
                        with self.subTest(size=(width, height), role=role, demo=demo, tab=tab, unicode=unicode):
                            ui.tab = tab
                            ui.render()
                            text = ui.stdscr.text()
                            self.assertIn('['+o.TAB_KEYS[tab]+':'+o.TABS[tab]+']', ui.stdscr.line(4))
                            self.assertIn('['+o.TAB_GROUPS[ui.current_group()][0]+']', ui.stdscr.line(3))
                            self.assertIn('? help', ui.stdscr.line(height-1))
                            self.assertIn('Q quit', ui.stdscr.line(height-1))
                            if tab == 9 and (role not in o.operator_ops.OWNER_ROLES or demo):
                                self.assertIn('LOCKED', text)
                            if not unicode:
                                self.assertTrue(text.isascii())
                ui.collector.perform_action.assert_not_called()
                ui.collector.execute_merge.assert_not_called()
                ui.collector.prepare_merges.assert_not_called()

    def test_randomized_resize_preserves_state_and_bounds(self):
        rng = random.Random(124)
        ui = self.make_ui(role='ADMIN')
        ui.confirm = 'discovery-now'
        for _ in range(70):
            width, height = rng.randint(1, 230), rng.randint(1, 65)
            ui.stdscr = StrictSurface(width, height)
            for tab in range(len(o.TABS)):
                ui.tab = tab
                ui.scroll['pulse'] = rng.randrange(5)
                ui.scroll['owner_page'] = rng.randrange(4)
                ui.indices['tasks'] = rng.randrange(10000)
                ui.scroll['workers'] = rng.randrange(10000)
                self.assertTrue(ui.handle(o.curses.KEY_RESIZE))
                ui.render()
                self.assertEqual(ui.confirm, 'discovery-now')
                self.assertEqual(ui.layout.width, width)
                self.assertEqual(ui.layout.height, height)
            self.assertEqual(ui.stdscr.refreshes, 14)
        ui.collector.perform_action.assert_not_called()

    def test_narrow_card_pages_and_wrapped_scroll(self):
        ui = self.make_ui(role='ADMIN')
        for tab, titles in ((0, ('WORKER TELEMETRY', 'TASK PIPELINE', 'EVIDENCE / FORECAST', 'LIVE ACTIVITY')),
                            (9, ('DISCOVERY TIMER', 'GIT BRANCH ALIGNMENT', 'BULK PR MERGE', 'OPERATOR EVENTS'))):
            ui.tab = tab
            if tab == 0:
                ui.handle(o.curses.KEY_NPAGE)  # leave the four-card summary
            for title in titles:
                ui.render()
                self.assertIn(title, ui.stdscr.text())
                ui.handle(o.curses.KEY_NPAGE)
        ui.tab = 13
        ui.render()
        before = ui.stdscr.text()
        for _ in range(15):
            ui.handle(o.curses.KEY_DOWN)
        ui.render()
        self.assertNotEqual(before, ui.stdscr.text())
        ui.scroll['help'] = 10000
        ui.render()
        self.assertLess(ui.scroll['help'], 10000)
        ui.stdscr = StrictSurface(192, 54)
        ui.render()
        self.assertGreaterEqual(ui.scroll['help'], 0)

    def test_search_cursor_selection_details_and_status_overlay(self):
        ui = self.make_ui()
        ui.tab = 1
        ui.indices['tasks'] = 10000
        ui.handle(ord('/'))
        for c in 'Synthetic task 12':
            ui.handle(ord(c))
        ui.handle(o.curses.KEY_RESIZE)
        ui.render()
        self.assertEqual([r['number'] for r in ui.active_records], [12])
        self.assertEqual(ui.indices['tasks'], 0)
        self.assertIn('_  Enter apply / Esc cancel', ui.stdscr.line(23))
        ui.handle(10)
        self.assertFalse(ui.search_edit)
        self.assertEqual(ui.selected_url(), 'https://github.com/fixture/repo/issues/12')
        ui.handle(ord('d'))
        ui.render()
        self.assertIn('SELECTED RECORD', ui.stdscr.text())
        self.assertIn('Synthetic task 12', ui.stdscr.text())
        ui.handle(ord('/'))
        ui.search[1] = 'x'*80
        ui.render()
        self.assertIn('_  Enter apply / Esc cancel', ui.stdscr.line(23))
        ui.handle(27)
        self.assertEqual(ui.search[1], 'Synthetic task 12')
        ui.show_notice('STATUS OVERLAY FIXTURE')
        ui.render()
        self.assertIn('STATUS OVERLAY FIXTURE', ui.stdscr.line(22))
        self.assertIn('Q quit', ui.stdscr.line(23))
        ui.stdscr = StrictSurface(120, 40)
        ui.handle(ord('d'))  # restore list plus detail
        ui.render()
        self.assertIn('GITHUB TASK LEDGER', ui.stdscr.text())
        self.assertIn('SELECTED RECORD', ui.stdscr.text())

    def test_filtered_pr_mark_targets_visible_selection(self):
        ui = self.make_ui(role='ADMIN')
        ui.tab = 2
        ui.search[2] = '#3:'
        ui.render()
        self.assertEqual(len(ui.active_records), 1)
        ui.handle(ord('b'))
        self.assertEqual(ui.pr_marked, {103})
        ui.search[2] = 'no match'
        ui.render()
        ui.handle(ord('b'))
        self.assertEqual(ui.pr_marked, {103})

    def test_worker_cards_scroll_and_resize_without_losing_records(self):
        ui = self.make_ui()
        now = datetime.now(timezone.utc).isoformat()
        ui.snap.worker_presence = [dict(owner=f'contributor-{i:02}', state='WORKING', updated=now,
                                        provider='synthetic', issue=i, worker=f'{i:016x}')
                                   for i in range(1, 20)]
        ui.tab = 10
        for width, height in self.SIZES:
            ui.stdscr = StrictSurface(width, height)
            seen = set()
            ui.scroll['workers'] = 0
            for _ in range(20):
                ui.render()
                text = ui.stdscr.text()
                seen.update(r['owner'] for r in ui.snap.worker_presence if r['owner'] in text)
                self.assertIn('CONTRIBUTOR HEALTH', text)
                self.assertIn('[WORKING]', text)
                ui.handle(o.curses.KEY_DOWN)
            self.assertEqual(seen, {r['owner'] for r in ui.snap.worker_presence})
            ui.scroll['workers'] = 10000
            ui.render()
            self.assertLess(ui.scroll['workers'], 20)

    def test_cards_are_disjoint_and_activity_stays_sanitized(self):
        ui = self.make_ui(role='ADMIN')
        ui.snap.activity = {'state': 'AVAILABLE', 'records': [dict(
            issue=1, round=1, timestamp_utc=datetime.now(timezone.utc).isoformat(),
            status='TESTING', event_category='test', structured_events=True,
            commands_started=7, commands_completed=6, tests_started=3, tests_completed=2,
            file_changed_count=1, last_activity_age_seconds=1, counts_complete=False,
            raw_log='PRIVATE_SENTINEL prompt=secret /private/path')]}
        for width, height in self.SIZES:
            ui.stdscr = StrictSurface(width, height)
            for tab in (0, 1, 2, 9, 10, 12):
                ui.tab = tab
                with mock.patch.object(ui, 'frame', wraps=ui.frame) as frames:
                    ui.render()
                rectangles = [o.ui_components.Rect(*c.args[:4]) for c in frames.call_args_list]
                for a in rectangles:
                    self.assertGreaterEqual(a.h, 3)
                    self.assertGreaterEqual(a.w, 6)
                    self.assertGreaterEqual(a.y, 6)
                    self.assertLessEqual(a.y+a.h, height-3)
                    self.assertLessEqual(a.x+a.w, width-1)
                for i, a in enumerate(rectangles):
                    for b in rectangles[i+1:]:
                        self.assertTrue(a.x+a.w <= b.x or b.x+b.w <= a.x or
                                        a.y+a.h <= b.y or b.y+b.h <= a.y, (a, b))
                text = ui.stdscr.text()
                self.assertNotIn('PRIVATE_SENTINEL', text)
                if tab == 0:
                    self.assertGreaterEqual(len(rectangles), 4)
                    self.assertIn('Whole-game ETA: UNKNOWN', text)
                if tab == 12:
                    self.assertIn('Commands 7 started / 6 completed', text)
                    self.assertIn('Tests 3 started / 2 completed', text)

    def test_tiny_resize_keeps_pending_confirmation_visible(self):
        # A pending confirmation still accepts Y, so a resize message must not
        # hide which privileged action is about to run.
        ui = self.make_ui(role='ADMIN')
        ui.tab = 9
        ui.handle(ord('d'))
        self.assertIsNotNone(ui.confirm)
        action = ui.confirm
        for width, height in ((71, 40), (192, 17), (30, 6)):
            ui.stdscr = StrictSurface(width, height)
            ui.handle(o.curses.KEY_RESIZE)
            ui.render()
            text = ui.stdscr.text()
            self.assertIn(action, text)
            self.assertIn('Y/N', text)
            self.assertIsNotNone(ui.confirm)
        ui.handle(ord('n'))
        self.assertIsNone(ui.confirm)
        ui.collector.perform_action.assert_not_called()

    def test_owner_keys_preserved_and_guest_actions_locked(self):
        for role, demo in (('WRITE', False), ('READ', False), ('UNKNOWN', False), ('ADMIN', True)):
            ui = self.make_ui(role=role, demo=demo)
            ui.tab = 9
            for key in 'dDnNuUpPcCiIjJfF':
                ui.handle(ord(key))
                self.assertIsNone(ui.confirm)
                self.assertIn('LOCKED', ui.notice)
            ui.collector.prepare_merges.assert_not_called()
            ui.collector.perform_action.assert_not_called()
        ui = self.make_ui(role='ADMIN')
        ui.tab = 9
        ui.handle(ord('j'))
        self.assertTrue(ui.allow_no_checks)  # original owner key, never prose scroll
        ui.handle(ord('J'))
        self.assertFalse(ui.allow_no_checks)
        ui.handle(ord('d'))
        ui.render()
        self.assertIn('Y yes / N cancel', ui.stdscr.text())
        ui.handle(o.curses.KEY_RESIZE)
        ui.collector.perform_action.assert_not_called()
        ui.handle(27)
        self.assertIsNone(ui.confirm)


if __name__=='__main__':
    unittest.main()
