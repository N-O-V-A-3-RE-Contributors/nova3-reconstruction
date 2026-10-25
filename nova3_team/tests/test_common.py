import unittest
from datetime import timedelta
from nova3_team.common import lease_put, lease_get, lease_active, now, iso
from nova3_team.worker import allowed_from_issue

class CommonTest(unittest.TestCase):
    def test_round_trip(self):
        l={'owner':'test','token':'abc','expires_at':iso(now()+timedelta(minutes=30))}
        body=lease_put('Hello',l)
        self.assertEqual(lease_get(body),l)
        self.assertTrue(lease_active(lease_get(body)))
        self.assertEqual(lease_put(body,None),'Hello')
    def test_expired(self):
        l={'owner':'test','token':'abc','expires_at':iso(now()-timedelta(minutes=1))}
        self.assertFalse(lease_active(l))
    def test_allowed(self):
        self.assertEqual(allowed_from_issue('Priority: 90\nAllowed paths: foo/bar.cpp, foo/test.cpp\n'),['foo/bar.cpp','foo/test.cpp'])

if __name__=='__main__':unittest.main()
