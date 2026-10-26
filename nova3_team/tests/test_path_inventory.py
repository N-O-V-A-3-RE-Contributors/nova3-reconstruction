import subprocess
import tempfile
import unittest
from pathlib import Path

from nova3_team.path_inventory import changed_files, parse_porcelain, UnsafeChange


class InventoryTests(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git(self.root, 'init', '-q')
        self.git(self.root, 'config', 'user.name', 'Test')
        self.git(self.root, 'config', 'user.email', 'test@example.org')
        (self.root/'baseline.txt').write_text('hello')
        self.git(self.root, 'add', '.')
        self.git(self.root, 'commit', '-qm', 'baseline')

    def test_nested_untracked_files_are_not_directory_names(self):
        p=self.root/'reconstruction/recovered/libNOVA3_neon'
        p.mkdir(parents=True)
        (p/'block_002ad444.cpp').write_text('int x;')
        self.assertEqual(changed_files(self.root), ['reconstruction/recovered/libNOVA3_neon/block_002ad444.cpp'])

    def test_old_checkout_untracked_python_cache_does_not_block_source(self):
        cache=self.root/'nova3_team/__pycache__'
        cache.mkdir(parents=True)
        (cache/'worker.cpython-313.pyc').write_bytes(b'fake cache')
        target=self.root/'reconstruction/recovered/libNOVA3_neon/block_002ad444.cpp'
        target.parent.mkdir(parents=True)
        target.write_text('int x;')
        self.assertEqual(changed_files(self.root),
                         ['reconstruction/recovered/libNOVA3_neon/block_002ad444.cpp'])

    def test_staged_and_unstaged(self):
        (self.root/'baseline.txt').write_text('modified')
        (self.root/'new.json').write_text('{}')
        self.git(self.root, 'add', 'new.json')
        self.assertEqual(changed_files(self.root), ['baseline.txt','new.json'])

    def test_status_supports_spaces(self):
        (self.root/'a b.txt').write_text('contents')
        self.assertEqual(changed_files(self.root), ['a b.txt'])

    def test_reject_delete(self):
        (self.root/'baseline.txt').unlink()
        with self.assertRaises(UnsafeChange): changed_files(self.root)

    def test_reject_symlink(self):
        (self.root/'linked.cpp').symlink_to('/etc/hosts')
        with self.assertRaises(UnsafeChange): changed_files(self.root)

    def test_parse_rename(self):
        self.assertEqual(parse_porcelain(b'R  new.cpp\0old.cpp\0'), [('R ','new.cpp'),('R ','old.cpp')])

    def test_reject_truncation(self):
        with self.assertRaises(UnsafeChange): parse_porcelain(b'?? dir/file')


if __name__=='__main__':unittest.main()
