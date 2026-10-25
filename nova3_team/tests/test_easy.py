import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('nova3_easy',ROOT/'nova3_easy.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class EasyTests(unittest.TestCase):
    def test_known_provider_names(self):
        self.assertEqual(set(mod.PACKAGES), {'codex','claude','gemini'})
    def test_config_is_not_in_repo(self):
        self.assertNotIn('nova3_re',str(mod.config_path()))
    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            old=os.environ.get('XDG_CONFIG_HOME')
            os.environ['XDG_CONFIG_HOME']=d
            try:
                mod.save_config({'provider':'codex'})
                self.assertEqual(mod.load_config()['provider'],'codex')
            finally:
                if old is None:os.environ.pop('XDG_CONFIG_HOME',None)
                else:os.environ['XDG_CONFIG_HOME']=old

if __name__=='__main__':unittest.main()
