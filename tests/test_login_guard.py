from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from login_guard import LoginGuard, source_ip


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.guard = LoginGuard(clock=lambda: self.now)

    def fail(self, user='alice', ip='192.0.2.1'):
        token, retry = self.guard.admit(user, ip)
        self.assertIsNotNone(token)
        self.assertEqual(retry, 0)
        return self.guard.finish(token, False)

    def test_user_limit_across_sources_and_cooldown_expiry(self):
        for n in range(7):
            self.assertEqual(self.fail(ip=f'192.0.2.{n+1}'), 0)
        self.assertEqual(self.fail(ip='192.0.2.8'), 300)
        self.assertEqual(self.guard.admit('alice', '192.0.2.99'), (None, 300))
        self.now = 300
        self.assertIsNotNone(self.guard.admit('alice', '192.0.2.99')[0])

    def test_ip_limit_across_usernames(self):
        for n in range(29):
            self.assertEqual(self.fail(user=f'user{n}'), 0)
        self.assertEqual(self.fail(user='user29'), 600)
        self.assertEqual(self.guard.admit('new_user', '192.0.2.1'), (None, 600))

    def test_parallel_reservations_cannot_exceed_user_limit(self):
        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(lambda n: self.guard.admit('alice', f'192.0.2.{n+1}'), range(32)))
        tokens = [token for token, retry in results if token is not None]
        self.assertEqual(len(tokens), 8)
        for token in tokens:
            self.guard.finish(token, False)
        self.assertEqual(self.guard.admit('alice', '198.51.100.1'), (None, 300))
        self.assertEqual(self.guard._active, 0)

    def test_global_hashing_concurrency_bound(self):
        tokens = [self.guard.admit(f'user{n}', f'192.0.2.{n+1}')[0] for n in range(16)]
        self.assertTrue(all(tokens))
        self.assertEqual(self.guard.admit('extra', '198.51.100.1'), (None, 1))
        self.guard.finish(tokens[0], True)
        self.assertIsNotNone(self.guard.admit('extra', '198.51.100.1')[0])

    def test_success_resets_user_but_not_ip_failure_budget(self):
        for _ in range(7):
            self.fail()
        token, _ = self.guard.admit('alice', '192.0.2.1')
        self.guard.finish(token, True)
        self.assertEqual(self.guard._states[('user', 'alice')].failures, 0)
        self.assertEqual(self.guard._states[('ip', '192.0.2.1')].failures, 7)

    def test_window_reset(self):
        for _ in range(7):
            self.fail()
        self.now = 900
        self.assertEqual(self.fail(), 0)

    def test_capacity_fails_closed_without_evicting_limits(self):
        self.guard = LoginGuard(max_entries=4, user_limit=1, clock=lambda: self.now)
        self.fail('alice', '192.0.2.1')
        self.fail('bob', '192.0.2.2')
        for n in range(100):
            self.assertEqual(self.guard.admit(f'new{n}', '198.51.100.1'), (None, 60))
        self.assertEqual(len(self.guard._states), 4)
        self.assertEqual(self.guard.admit('alice', '192.0.2.1'), (None, 300))
        self.now = 1801
        self.assertIsNotNone(self.guard.admit('new', '198.51.100.1')[0])
        self.assertEqual(len(self.guard._states), 2)

    def test_active_records_survive_cleanup(self):
        token, _ = self.guard.admit('alice', '192.0.2.1')
        self.now = 1801
        self.guard.admit('bob', '192.0.2.2')
        self.guard.finish(token, False)
        self.assertIn(('user', 'alice'), self.guard._states)

    def test_trusted_source_header(self):
        h = Message(); h['X-Real-IP'] = '198.51.100.7'
        self.assertEqual(source_ip('127.0.0.1', h), '198.51.100.7')
        self.assertEqual(source_ip('203.0.113.8', h), '203.0.113.8')
        h['X-Real-IP'] = '198.51.100.8'
        self.assertEqual(source_ip('127.0.0.1', h), '127.0.0.1')
        h = Message(); h['X-Real-IP'] = '198.51.100.7, 203.0.113.1'
        self.assertEqual(source_ip('127.0.0.1', h), '127.0.0.1')
        h = Message(); h['X-Real-IP'] = '::ffff:198.51.100.7'
        self.assertEqual(source_ip('::ffff:127.0.0.1', h), '198.51.100.7')


if __name__ == '__main__':
    unittest.main()
