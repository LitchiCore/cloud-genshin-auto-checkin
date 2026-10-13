from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock
import hashlib
import json
import re
import secrets
import sys
import threading
import time
import unittest
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'web'))
import admin_web
import qr_login


class ReloginTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [patch.object(admin_web, 'DB', self.base / 'users.db'),
                        patch.object(admin_web, 'ACCOUNTS', self.base / 'accounts'),
                        patch.object(admin_web, 'PROTECTED_ACCOUNT', 'lihe')]
        for p in self.patches: p.start()
        self.cookies = {}
        with closing(admin_web.connect()) as db:
            for user, role in (('lihe','user'), ('other','user'), ('manager','admin')):
                (admin_web.ACCOUNTS / user / 'screenshots').mkdir(parents=True)
                db.execute('INSERT INTO users(username,role,created_at,password_set) VALUES(?,?,?,1)', (user,role,int(time.time())))
                token = secrets.token_urlsafe(32)
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(),user,int(time.time())+300))
                self.cookies[user] = 'cg_session=' + token
            db.commit()
        self.server = ThreadingHTTPServer(('127.0.0.1',0), admin_web.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def request(self, path, user=None, form=None):
        headers = {'Cookie':self.cookies[user]} if user else {}
        conn = HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        if form is None:
            conn.request('GET',path,headers=headers)
        else:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            conn.request('POST',path,urlencode(form),headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_owner_routes_require_session_and_account_permission(self):
        for prefix in ('relogin','relogin-status','relogin-qr'):
            path = '/admin/' + prefix + '/lihe'
            self.assertEqual(self.request(path)[0],303)
            self.assertEqual(self.request(path,'other')[0],403)
        self.assertEqual(self.request('/admin/relogin/lihe','lihe')[0],200)
        self.assertEqual(self.request('/admin/relogin/lihe','manager')[0],200)
        self.assertEqual(self.request('/admin/relogin/not-found','manager')[0],404)
        self.assertEqual(self.request('/admin/','lihe')[0],303)

    def test_protected_owner_can_relogin_but_cannot_be_deleted(self):
        status, _, body = self.request('/admin/relogin/lihe','lihe')
        token = re.search(rb'name="csrf" value="([0-9a-f]+)"',body).group(1).decode()
        with patch.object(admin_web,'start_qr_job') as start:
            self.assertEqual(self.request('/admin/relogin/lihe','lihe',{})[0],403)
            self.assertEqual(self.request('/admin/relogin/other','manager',{'csrf':token})[0],403)
            status, headers, _ = self.request('/admin/relogin/lihe','lihe',{'csrf':token})
            self.assertEqual(status,303)
            self.assertEqual(headers['Location'],'/admin/relogin/lihe')
            start.assert_called_once_with('lihe')
        self.assertEqual(self.request('/admin/delete/lihe','manager')[0],400)
        self.assertEqual(self.request('/admin/delete/lihe','manager',{'confirm':'lihe'})[0],400)
        with closing(admin_web.connect()) as db:
            self.assertEqual(db.execute('SELECT role,password_set FROM users WHERE username="lihe"').fetchone(),('user',1))

    def test_admin_lists_protected_account_and_recovery_entry(self):
        with patch.object(admin_web,'ipv6_status_banner',return_value=''):
            status, _, body = self.request('/admin/','manager')
        self.assertEqual(status,200)
        self.assertIn('受保护账号'.encode(),body)
        self.assertIn(b'/admin/relogin/lihe',body)

    def test_qr_job_id_and_expiry_prevent_stale_image(self):
        d = admin_web.ACCOUNTS / 'lihe'
        (d/'admin-qr-job-id').write_text('new-job')
        (d/'screenshots/qr.png').write_bytes(b'image-data')
        (d/'admin-qr-status.json').write_text(json.dumps({'phase':'waiting_scan','expires_at':int(time.time())+60}))
        with patch.object(admin_web,'pid_alive',return_value=True):
            self.assertEqual(self.request('/admin/relogin-qr/lihe?job=old-job','lihe')[0],410)
            self.assertEqual(self.request('/admin/relogin-qr/lihe?job=new-job','lihe')[2],b'image-data')
            (d/'admin-qr-status.json').write_text(json.dumps({'phase':'waiting_scan','expires_at':int(time.time())-1}))
            self.assertEqual(self.request('/admin/relogin-qr/lihe?job=new-job','lihe')[0],410)
            self.assertNotIn(b'class="qr"',self.request('/admin/relogin/lihe','lihe')[2])

    def test_completed_job_waits_for_worker_and_expired_health_is_not_success(self):
        d = admin_web.ACCOUNTS / 'lihe'
        (d/'admin-qr-status.json').write_text(json.dumps({'phase':'success'}))
        with patch.object(admin_web,'pid_alive',return_value=True):
            self.assertEqual(admin_web.job_status('lihe')['phase'],'verifying')
        (d/'admin-qr-exit').write_text('0')
        (d/'health.json').write_text(json.dumps({'status':'login_expired'}))
        with patch.object(admin_web,'pid_alive',return_value=False):
            self.assertEqual(admin_web.job_status('lihe')['phase'],'expired')
        (d/'health.json').write_text(json.dumps({'status':'ok'}))
        with patch.object(admin_web,'pid_alive',return_value=False):
            self.assertEqual(admin_web.job_status('lihe')['phase'],'success')


class VerificationTests(unittest.TestCase):
    def test_closed_dialog_without_account_data_is_not_success(self):
        page = Mock()
        page.locator.return_value.is_visible.return_value = False
        page.locator.return_value.inner_text.return_value = '开始游戏'
        self.assertIsNone(qr_login.verified_minutes(page))
        page.locator.return_value.inner_text.return_value = '免费时长：0 分钟'
        self.assertEqual(qr_login.verified_minutes(page),0)
        page.locator.return_value.is_visible.return_value = True
        self.assertIsNone(qr_login.verified_minutes(page))

    @unittest.skipIf(sys.platform == 'win32','Linux profile locking')
    def test_profile_lock_excludes_parallel_login_and_visit(self):
        from profile_lock import acquire_profile_lock, ProfileBusyError
        with TemporaryDirectory() as directory:
            with acquire_profile_lock(directory):
                with self.assertRaises(ProfileBusyError):
                    acquire_profile_lock(directory)
            acquire_profile_lock(directory).close()


if __name__ == '__main__':
    unittest.main()
