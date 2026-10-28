import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
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

    def test_eta_assumptions(self):
        a=o.build_estimates([issue(29),issue(30)],[],{}, {},workers=1)
        self.assertEqual(a['queue_seconds']['fast'],2*35*60)
        self.assertEqual(a['queue_seconds']['likely'],2*90*60)
        self.assertEqual(a['queue_seconds']['slow'],2*210*60)
        self.assertIsNone(a['throughput_seconds'])

    def test_eta_parallelism(self):
        data=[issue(29),issue(30)]
        one=o.build_estimates(data,[],{}, {},1)
        two=o.build_estimates(data,[],{}, {},2)
        self.assertEqual(one['queue_seconds']['likely'],two['queue_seconds']['likely']*2)

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

    def test_hypothetical_eta_bounds(self):
        inv={'available': True,'count':2400,'integrated':0,'verified':0}
        result=o.build_estimates([],[],inv,{},1)
        self.assertLess(result['whole_game_seconds']['fast'], result['whole_game_seconds']['likely'])
        self.assertLess(result['whole_game_seconds']['likely'], result['whole_game_seconds']['slow'])
        self.assertEqual(result['hypothetical_inventory_coverage']['likely'], .30)

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


if __name__=='__main__':
    unittest.main()
