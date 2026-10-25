import unittest, tempfile, pathlib
from nova3_team.guard import check

class GuardTest(unittest.TestCase):
    def test_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=pathlib.Path(tmp)
            (d/'foo.cpp').write_text('int main() {}')
            (d/'originals').mkdir();(d/'originals'/'x.apk').write_bytes(b'PK\x03\x04')
            self.assertEqual(check(d,['foo.cpp']),[])
            self.assertEqual(len(check(d,['originals/x.apk'])),1)

if __name__=='__main__':unittest.main()
