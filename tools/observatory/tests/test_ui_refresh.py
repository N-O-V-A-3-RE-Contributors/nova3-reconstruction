"""Synthetic visual contracts: geometry and rendered cells, not text labels alone."""
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_render import FakeScreen, o

u = o.ui_components
SIZES = ((40, 12), (72, 18), (80, 24), (100, 30), (140, 38), (180, 48), (220, 55))
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)


def issue(number, stage):
    return {'number': number, 'title': 'Reconstruct synthetic task', 'state': 'OPEN',
            'labels': [{'name': 'nova:' + stage}]}


def record(age=0, number=1, round_number=1):
    return {'issue': number, 'round': round_number,
            'timestamp_utc': (NOW-timedelta(seconds=age)).isoformat(),
            'status': 'TESTING', 'event_category': 'test', 'structured_events': True,
            'commands_started': 7, 'commands_completed': 6,
            'tests_started': 3, 'tests_completed': 2, 'file_changed_count': 4,
            'last_activity_age_seconds': 3, 'counts_complete': False}


class CellScreen(FakeScreen):
    """Reject out-of-bounds writes and composite overwrites like curses does."""
    def erase(self):
        super().erase()
        self.cells = [[' '] * self.width for _ in range(self.height)]

    def addnstr(self, y, x, text, count, style):
        super().addnstr(y, x, text, count, style)
        self.assert_valid(y, x, text[:count])
        for c in text[:count]:
            width = u.cell_width(c)
            if width:
                self.cells[y][x] = c
                for i in range(1, width):
                    self.cells[y][x+i] = ' '
                x += width

    def assert_valid(self, y, x, text):
        assert x + u.cell_width(text) < self.width, (y, x, text)
        assert '\x1b' not in text

    def text(self):
        return '\n'.join(''.join(row).rstrip() for row in self.cells)

    def bkgd(self, *args):
        pass


def dashboard(width=140, height=38, records=()):
    collector = SimpleNamespace(repo='fixture/synthetic', service='fixture.service',
                                demo=False, action=Mock())
    view = o.Observatory(collector)
    view.stdscr = CellScreen(width, height)
    view.unicode = True  # deterministic canonical fixture, independent of TERM
    view.snap = o.Snapshot(
        issues=[issue(1, 'claimed'), issue(2, 'blocked'), issue(3, 'queued'), issue(4, 'other')],
        github_updated=NOW.isoformat(), inventory={'available': True, 'verified': 0},
        service={'active': 'active', 'sub': 'running', 'pid': '123', 'restarts': '0'},
        activity={'state': 'AVAILABLE' if records else 'MISSING', 'records': list(records)})
    return view


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


class VisualContracts(unittest.TestCase):
    def setUp(self):
        frozen = patch.object(o, 'datetime', FrozenDateTime)
        frozen.start()
        self.addCleanup(frozen.stop)

    def render(self, view, tab):
        view.tab = tab
        with patch.object(view, 'frame', wraps=view.frame) as frames:
            view.render()
        return view.stdscr.text(), [u.Rect(*call.args[:4]) for call in frames.call_args_list]

    def assert_disjoint(self, frames, width, height):
        for r in frames:
            self.assertGreaterEqual(r.h, 3)
            self.assertGreaterEqual(r.w, 6)
            self.assertGreaterEqual(r.y, 6)
            self.assertGreaterEqual(r.x, 1)
            self.assertLessEqual(r.x+r.w, width-1)
            self.assertLessEqual(r.y+r.h, height-3)
        for i, a in enumerate(frames):
            for b in frames[i+1:]:
                self.assertTrue(a.x+a.w <= b.x or b.x+b.w <= a.x or
                                a.y+a.h <= b.y or b.y+b.h <= a.y, (a, b))

    def test_all_tabs_every_breakpoint_and_ascii(self):
        self.assertEqual(len(o.TABS), 15)
        self.assertEqual(len(o.TAB_KEYS), len(o.TABS))
        self.assertEqual(len(set(o.TAB_KEYS)), 15)
        for width, height in SIZES:
            for unicode in (True, False):
                view = dashboard(width, height)
                view.unicode = unicode
                for tab, label in enumerate(o.TABS):
                    if tab == 9:
                        continue  # owner-only page is hidden from contributors
                    with self.subTest(size=(width, height), tab=label, unicode=unicode):
                        text, _ = self.render(view, tab)
                        self.assertIn('CONTROL CENTER', text)
                        if width >= 72:
                            self.assertIn('[' + o.TAB_KEYS[tab] + ':' + label + ']', text)
                        else:
                            self.assertIn('Resize', text)
                            self.assertIn('Whole-game ETA: UNKNOWN', text)
                        if not unicode:
                            self.assertTrue(text.isascii())
                view.collector.action.assert_not_called()

    def test_pulse_is_four_real_tiles_and_bars_have_task_denominator(self):
        for width, height in SIZES[1:]:
            with self.subTest(size=(width, height)):
                view = dashboard(width, height)
                text, frames = self.render(view, 0)
                self.assertGreaterEqual(len(frames), 4)  # rejects single-box regression
                self.assert_disjoint(frames, width, height)
                self.assertEqual(frames[0].y, frames[1].y)
                self.assertEqual(frames[2].y, frames[3].y)
                self.assertGreater(frames[1].x, width//2-2)
                self.assertGreater(frames[2].y, frames[0].y)
                for title in ('WORKER TELEMETRY', 'TASK PIPELINE', 'EVIDENCE / FORECAST', 'LIVE ACTIVITY'):
                    self.assertIn(title, text)
                self.assertIn('[RUNNING]', text)
                self.assertIn('[MISSING]', text)
                self.assertIn('Whole-game ETA: UNKNOWN', text)
                self.assertNotIn('32 days', text)
                self.assertNotIn('%', text)
                if width >= 100:
                    self.assertIn('Denominator: 4 sampled tasks', text)
                    self.assertIn('█', text)
                    self.assertIn('░', text)
                    self.assertIn('Other / verified / unclassified: 1', text)
                if height >= 38:
                    self.assertIn('READINESS / SOURCE EVIDENCE', text)
                    self.assertIn('Original source matches: 0', text)

    def test_forecast_distinct_gate_queue_setup_and_scrolling_reasons(self):
        for width, height in SIZES[1:]:
            view = dashboard(width, height)
            view.snap.issues += [issue(n, 'blocked') for n in range(10, 40)]
            text, frames = self.render(view, 5)
            self.assertGreaterEqual(len(frames), 3)
            self.assert_disjoint(frames, width, height)
            self.assertIn('EVIDENCE GATE', text)
            self.assertIn('QUEUE / STAGE WORK', text)
            self.assertIn('SETUP / SOURCE', text)
            self.assertIn('Whole-game ETA: UNKNOWN', text)
            self.assertIn('Samples: 0', text)
            self.assertIn('Original matches: 0', text)
            if height >= 30:
                self.assertIn('REASONS / BLOCKERS', text)
                self.assertIn('incomplete_queue_snapshot', text)
                before = text
                view.scroll['forecast'] = 4
                after, _ = self.render(view, 5)
                self.assertNotEqual(before, after)
                self.assertIn('Whole-game ETA: UNKNOWN', after)
                view.scroll['forecast'] = 10000
                text, _ = self.render(view, 5)
                self.assertIn('Whole-game ETA: UNKNOWN', text)

    def test_chart_requires_measured_stage_evidence(self):
        f = o.forecast_model
        report = f.forecast(f.Snapshot(
            now=NOW.timestamp(), observed_at=NOW.timestamp(), scope='synthetic', queue_complete=True,
            tasks=(f.Task('1', 'small', stage='active', stage_started_at=NOW.timestamp()-10),),
            workers=(f.Worker('local', '1', NOW.timestamp()),),
            history=tuple(f.Attempt(str(i), 'old'+str(i), 'small', 'source', 'execution',
                                    NOW.timestamp()-1000-i*1000, NOW.timestamp()-800-i*1000)
                          for i in range(10))))
        self.assertIsNotNone(report['queue']['all_supplied_queue_seconds'])
        view = dashboard()
        est = o.build_estimates([], [], {}, {}, snapshot=view.snap)
        est['forecast'] = report
        with patch.object(o, 'build_estimates', return_value=est):
            text, _ = self.render(view, 5)
        self.assertIn('Measured stage work (seconds', text)
        self.assertIn('Samples: 10', text)
        self.assertIn('█', text)
        self.assertIn('p20', text)
        self.assertIn('p80', text)
        self.assertIn('Whole-game ETA: UNKNOWN', text)
        self.assertNotIn('No qualifying stage range.', text)
        text, _ = self.render(view, 5)
        self.assertNotIn('█', text)
        self.assertIn('[NO CHART]', text)

    def test_activity_missing_live_stale_and_past_task(self):
        for width, height in SIZES[1:]:
            for records, sub in (([], 'feed missing'), ([record()], 'structured feed live'),
                                 ([record(age=91)], 'structured-event quiet'), ([record(age=-1)], 'structured feed live')):
                view = dashboard(width, height, records)
                text, frames = self.render(view, 12)
                self.assertGreaterEqual(len(frames), 2)
                self.assert_disjoint(frames, width, height)
                self.assertIn('RUNNING', text)
                if width >= 100:
                    self.assertIn(sub, text.lower())
                if width >= 100:
                    self.assertGreaterEqual(len(frames), 3)
                    self.assertIn('INDEPENDENT SIGNALS / PRIVACY', text)
                if records and height >= 24:
                    self.assertIn('Commands 7 started / 6 completed', text)
                    self.assertIn('Tests 3 started / 2 completed', text)
                    self.assertIn('Task: CLAIMED', text)
                if not records:
                    self.assertIn('UNKNOWN', text)
        view = dashboard(records=[record()])
        view.snap.issues[0]['labels'] = [{'name': 'nova:integrated'}]
        text, _ = self.render(view, 12)
        self.assertIn('PAST TASK', text)
        self.assertNotIn('AI observed', text)

    def test_activity_scrolls_rounds_and_never_renders_untrusted_extra_fields(self):
        records = [record(number=i, round_number=i) for i in range(1, 7)]
        for r in records:
            r['raw_log'] = 'PRIVATE_SENTINEL /private/path prompt=secret command args=secret'
        view = dashboard(records=records)
        view.snap.logs = ['PRIVATE_SENTINEL raw AI log']
        before, frames = self.render(view, 12)
        self.assertGreaterEqual(len(frames), 4)
        view.scroll['activity'] = 3
        after, _ = self.render(view, 12)
        self.assertNotEqual(before, after)
        self.assertNotIn('PRIVATE_SENTINEL', before + after)
        self.assertNotIn('prompt=secret', before + after)
        for tab in (0, 5):
            text, _ = self.render(view, tab)
            self.assertNotIn('PRIVATE_SENTINEL', text)

    def test_idle_is_not_service_error_and_owner_controls_stay_locked(self):
        view = dashboard()
        view.snap.issues = []
        text, _ = self.render(view, 0)
        self.assertIn('systemd active / running | PID 123 | RUNNING', text)
        self.assertIn('Quiet output is not a stopped', text)
        self.assertNotIn('[ERROR]', text)
        self.assertIn('PID 123', text)
        view.handle(ord('0'))
        self.assertIn('ADMINISTRATION LOCKED', view.notice)
        self.assertEqual(view.tab, 0)
        for key in ('s', 'x', 'r', 'd', 'n', 'u'):
            view.handle(ord(key))
        view.collector.action.assert_not_called()

    def test_navigation_keys_match_rendered_tab_registry(self):
        view = dashboard()
        view.snap.repo_role = 'ADMIN'
        for key, label in zip(o.TAB_KEYS, o.TABS):
            view.tab = 0
            view.handle(ord(key.lower()))
            self.assertEqual(o.TABS[view.tab], label)
        view.tab = 0
        for _ in range(15):
            view.handle(9)
        self.assertEqual(view.tab, 0)


class Primitives(unittest.TestCase):
    def test_grid_no_overlap_and_fills_usable_area(self):
        for width, height in SIZES[1:]:
            area = u.Rect(6, 1, height-9, width-2)
            rects = u.grid(area)
            self.assertEqual(rects[-1].x+rects[-1].w, area.x+area.w)
            self.assertEqual(rects[-1].y+rects[-1].h, area.y+area.h)
            self.assertEqual(len(rects), 4)

    def test_cell_width_clipping_and_control_characters(self):
        for width in range(30):
            for value in ('abc', 'wide: 界界界', 'e\u0301 cafe\u0301', '\x1b[31m\r\n\tprivate'):
                for unicode in (True, False):
                    text = u.truncate(value, width, unicode)
                    self.assertLessEqual(u.cell_width(text), width)
                    self.assertNotIn('\x1b', text)
                    if not unicode:
                        self.assertTrue(text.isascii())
        view = dashboard(40, 12)
        view.stdscr.erase()
        for x in (-20, -1, 0, 39, 40, 100):
            for y in (-1, 0, 11, 12):
                view.put(y, x, '界'*100)

    def test_palette_mono_eight_limited_and_256(self):
        for colors, pairs in ((0, 0), (8, 4), (8, 64), (256, 256)):
            with patch.object(u.curses, 'has_colors', return_value=bool(colors)), \
                 patch.object(u.curses, 'start_color'), \
                 patch.object(u.curses, 'use_default_colors'), \
                 patch.object(u.curses, 'COLORS', colors, create=True), \
                 patch.object(u.curses, 'COLOR_PAIRS', pairs, create=True), \
                 patch.object(u.curses, 'init_pair') as init, \
                 patch.object(u.curses, 'color_pair', side_effect=lambda index: index << 8):
                palette = u.palette()
                for call in init.call_args_list:
                    index, fg, bg = call.args
                    self.assertLess(index, pairs)
                    self.assertLess(fg, colors)
                    self.assertTrue(bg == -1 or 0 <= bg < colors)
                self.assertEqual(set(palette), {'normal', 'muted', 'cyan', 'green', 'yellow', 'red', 'magenta', 'blue', 'bold'})
                view = dashboard()
                view.C = palette
                for tab in range(len(o.TABS)):
                    view.tab = tab
                    view.render()
        with patch.object(u.curses, 'has_colors', side_effect=o.curses.error):
            self.assertEqual(u.palette()['normal'], 0)


if __name__ == '__main__':
    unittest.main()
