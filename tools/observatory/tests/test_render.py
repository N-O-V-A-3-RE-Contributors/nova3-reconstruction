import unittest
import sys
from pathlib import Path
import importlib.util

p=Path(__file__).resolve().parents[1]/'observer.py'
spec=importlib.util.spec_from_file_location('nova3_observer_render',p)
sys.modules[spec.name]=importlib.util.module_from_spec(spec)
spec.loader.exec_module(sys.modules[spec.name])
o=sys.modules[spec.name]

class FakeScreen:
    def __init__(self,width,height):
        self.width,self.height=width,height
        self.lines={}
    def getmaxyx(self):return self.height,self.width
    def erase(self): self.lines={}
    def refresh(self): pass
    def addnstr(self,y,x,s,count,style):
        assert y>=0 and y<self.height,(y,x)
        assert x>=0 and x<self.width,(y,x)
        assert count>=0,(y,x)
        self.lines[(y,x)]=s[:count]

class RenderTests(unittest.TestCase):
    def test_pages_no_exception_on_multiple_terminal_sizes(self):
        for width,height in ((80,24),(120,32),(160,46),(70,17)):
            collector=o.Collector('X/Y',Path('/tmp'),'test.service',Path('/tmp'),demo=True)
            ui=o.Observatory(collector)
            ui.stdscr=FakeScreen(width,height)
            ui.snap=collector.view()
            for tab in range(len(o.TABS)):
                ui.tab=tab
                ui.render()
                self.assertTrue(ui.stdscr.lines)
    def test_new_map_controls(self):
        collector=o.Collector('X/Y',Path('/tmp'),'test.service',Path('/tmp'),demo=True)
        ui=o.Observatory(collector)
        ui.stdscr=FakeScreen(120,35)
        ui.snap=collector.view()
        ui.handle(ord('7'))
        self.assertEqual(ui.tab,6)
        ui.handle(ord('m'))
        self.assertEqual(ui.map_mode,1)
        ui.render()
        ui.handle(ord(']'))
        ui.render()
        ui.handle(ord('8'))
        ui.render()
        ui.handle(ord('9'))
        ui.render()
        ui.handle(ord('?'))
        self.assertEqual(ui.tab,len(o.TABS)-1)

    def test_logs_scroll_without_crash(self):
        collector=o.Collector('X/Y',Path('/tmp'),'test.service',Path('/tmp'),demo=True)
        ui=o.Observatory(collector)
        ui.stdscr=FakeScreen(110,32)
        ui.snap=collector.view()
        ui.tab=4
        for mode in range(3):
            ui.log_mode=mode
            ui.render()
            ui.scroll['logs']=100000
            ui.render()

if __name__=='__main__':unittest.main()
