from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from contextlib import closing
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import secrets
import sys
import threading
import time
import unittest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import log_web
from login_guard import LoginGuard


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.original_db = log_web.DB
        self.original_guard = log_web.LOGIN_GUARD
        log_web.DB = Path(self.temp.name) / 'users.db'
        self.now = 0
        log_web.LOGIN_GUARD = LoginGuard(user_limit=2, ip_limit=3, clock=lambda: self.now)
        self.password = secrets.token_urlsafe(24)
        salt, digest = log_web.hash_password(self.password)
        db = log_web.connect()
        db.execute('INSERT INTO users(username,password_salt,password_hash,role,created_at,password_set) VALUES(?,?,?,?,?,1)',
                   ('test_admin', salt, digest, 'admin', int(time.time())))
        self.invite = secrets.token_hex(10).upper()
        db.execute('INSERT INTO users(username,role,created_at,password_set,setup_token_hash,setup_expires) VALUES(?,?,?,0,?,?)',
                   ('invited', 'user', int(time.time()), hashlib.sha256(self.invite.encode()).hexdigest(), int(time.time())+300))
        db.commit(); db.close()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), log_web.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        log_web.DB = self.original_db
        log_web.LOGIN_GUARD = self.original_guard
        self.temp.cleanup()

    def request(self, path, form=None, ip='192.0.2.1', cookie=None):
        c = HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        headers = {'X-Real-IP': ip}
        if cookie:
            headers['Cookie'] = cookie
        if form is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            c.request('POST', path, urlencode(form), headers)
        else:
            c.request('GET', path, headers=headers)
        r = c.getresponse(); data = r.read().decode(); status = r.status; h = dict(r.getheaders()); c.close()
        return status, h, data

    def test_login_cooldown_and_session_recovery(self):
        form = {'username': 'test_admin', 'password': 'incorrect'}
        self.assertEqual(self.request('/login', form)[0], 200)
        status, headers, body = self.request('/login', form, ip='192.0.2.2')
        self.assertEqual(status, 429); self.assertEqual(headers['Retry-After'], '300')
        self.assertNotIn('test_admin', body)
        form['password'] = self.password
        self.assertEqual(self.request('/login', form, ip='192.0.2.3')[0], 429)
        self.now = 300
        status, headers, _ = self.request('/login', form, ip='192.0.2.3')
        self.assertEqual(status, 303); self.assertEqual(headers['Location'], '/')
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly; Secure; SameSite=Lax', headers['Set-Cookie'])
        self.assertEqual(self.request('/password', cookie=cookie)[0], 200)
        with closing(log_web.connect()) as db:
            self.assertEqual(db.execute('SELECT role,password_set FROM users WHERE username=?', ('test_admin',)).fetchone(), ('admin', 1))

    def test_ip_budget_unknown_and_invalid_names(self):
        for user in ('missing_a', 'bad/name'):
            status, _, body = self.request('/login', {'username': user, 'password': 'incorrect'})
            self.assertEqual(status, 200); self.assertIn('用户名或密码错误', body)
        status, headers, _ = self.request('/login', {'username': 'missing_b', 'password': 'incorrect'})
        self.assertEqual(status, 429); self.assertEqual(headers['Retry-After'], '600')
        self.assertEqual(self.request('/login', {'username': 'test_admin', 'password': self.password})[0], 429)
        self.assertEqual(self.request('/login', {'username': 'test_admin', 'password': self.password}, ip='192.0.2.2')[0], 303)

    def test_invite_registration_and_password_change(self):
        status, _, body = self.request('/register', {'invite': self.invite, 'password': self.password, 'confirm': self.password})
        self.assertEqual(status, 200); self.assertIn('注册完成', body)
        status, headers, _ = self.request('/login', {'username': 'invited', 'password': self.password})
        self.assertEqual(status, 303)
        cookie = headers['Set-Cookie'].split(';')[0]
        new_password = secrets.token_urlsafe(24)
        self.assertEqual(self.request('/password', {'current': self.password, 'password': new_password, 'confirm': new_password}, cookie=cookie)[0], 303)
        self.assertEqual(self.request('/password', cookie=cookie)[0], 303)
        self.assertEqual(self.request('/login', {'username': 'invited', 'password': new_password})[0], 303)

    def test_auth_event_has_only_safe_fields(self):
        output = StringIO()
        with redirect_stdout(output):
            log_web.login_event('failure', 'raw_identity\nsecret', '192.0.2.1')
        event = json.loads(output.getvalue())
        self.assertEqual(set(event), {'event', 'outcome', 'source_ip', 'user_key'})
        self.assertNotIn('raw_identity', output.getvalue())
        self.assertNotIn('secret', output.getvalue())


if __name__ == '__main__':
    unittest.main()
