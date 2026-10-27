"""Regression for Ghidra ProjectLocator rejecting hidden path components."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from nova3_team import discovery


class GhidraProjectDirectoryTests(unittest.TestCase):
    def fake_ghidra(self, root: Path) -> Path:
        executable = root / 'analyzeHeadless'
        executable.write_text('''#!/usr/bin/env python3
import json,pathlib,sys
args=sys.argv[1:]
project_root=pathlib.Path(args[0])
if any(piece.startswith('.') for piece in project_root.parts):
 print("ERROR Abort due to Headless analyzer error: Path element starting with '.' is not permitted")
 sys.exit(1)
assert project_root.is_dir(), project_root
assert args[1].startswith('scan_')
assert '-postScript' in args
index=args.index('-postScript')
output=pathlib.Path(args[index+2])
output.write_text(json.dumps({'normalization':'image-base-relative','functions':[{'address':'0x1000','size':8}]}))
pathlib.Path(args[args.index('-log')+1]).write_text('success')
pathlib.Path(args[args.index('-scriptlog')+1]).write_text('success')
''')
        executable.chmod(0o755)
        return executable

    def test_workspace_does_not_live_beneath_hidden_private_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / '.hidden' / '.cache'
            executable = self.fake_ghidra(root)
            elf = root / 'libNOVA3_neon.so'
            elf.write_bytes(b'\x7fELF' + b'0'*32)
            script = root / 'Nova3FunctionLedger.java'
            script.write_text('// test')
            output = root / 'export.json'
            with patch.dict(os.environ, {'NOVA3_DISCOVERY_CACHE':str(cache)}):
                discovery.run_ghidra(executable,elf,script=script,
                                     digest='a'*64,out=output,timeout=15)
                parsed=discovery.parse_export(output,digest='a'*64,library=elf.name)
                self.assertEqual(len(parsed['functions']),1)
                self.assertFalse(list(cache.glob('nova3-ghidra-*')))

    def test_hidden_environment_tempdir_falls_back_to_safe_path(self):
        with tempfile.TemporaryDirectory() as td:
            hidden=Path(td)/'.hidden'/'tmp';hidden.mkdir(parents=True)
            with patch('nova3_team.discovery.tempfile.gettempdir',return_value=str(hidden)):
                safe=discovery._ghidra_workspace_parent()
                self.assertFalse(any(part.startswith('.') for part in safe.parts))
                self.assertNotEqual(safe,hidden)

    def test_old_failure_markers_are_invalidated(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            script=root/'Nova3FunctionLedger.java';script.write_text('test')
            headless=root/'analyzeHeadless';headless.write_text('test')
            with patch.dict(os.environ, {'NOVA3_DISCOVERY_CACHE':str(root/'private')}):
                marker=discovery._failure_path('a'*64,script,headless)
                old=__import__('hashlib').sha256(('a'*64+discovery.sha256_file(script)+str(headless)+str(headless.stat().st_mtime_ns)).encode()).hexdigest()[:20]
                self.assertNotEqual(marker.name,old+'.json')

if __name__=='__main__':
    unittest.main()
