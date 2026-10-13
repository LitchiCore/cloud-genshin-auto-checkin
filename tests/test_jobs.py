from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
import json
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'web'))
import qr_login
import services


class JobsTests(unittest.TestCase):
    def test_closed_login_dialog_requires_actual_account_data(self):
        page=Mock();page.locator.return_value.is_visible.return_value=False
        page.locator.return_value.inner_text.return_value='开始游戏'
        self.assertIsNone(qr_login.verified_minutes(page))
        page.locator.return_value.inner_text.return_value='免费时长：0 分钟'
        self.assertEqual(qr_login.verified_minutes(page),0)
        page.locator.return_value.is_visible.return_value=True
        self.assertIsNone(qr_login.verified_minutes(page))

    def test_qr_expiry_worker_completion_and_expired_health(self):
        with TemporaryDirectory() as temporary,patch.object(services,'ACCOUNTS',Path(temporary)):
            d=Path(temporary)/'alice';(d/'screenshots').mkdir(parents=True)
            (d/'screenshots/qr.png').write_bytes(b'image')
            services.write_json(d/'admin-qr-status.json',{'phase':'waiting_scan','expires_at':int(time.time())+60})
            with patch.object(services,'worker_running',return_value=True):
                self.assertTrue(services.login_job('alice')['qr_ready'])
                services.write_json(d/'admin-qr-status.json',{'phase':'waiting_scan','expires_at':int(time.time())-1})
                self.assertFalse(services.login_job('alice')['qr_ready'])
                services.write_json(d/'admin-qr-status.json',{'phase':'success'})
                self.assertEqual(services.login_job('alice')['phase'],'verifying')
            (d/'admin-qr-exit').write_text('0')
            services.write_json(d/'health.json',{'status':'login_expired'})
            with patch.object(services,'worker_running',return_value=False):
                self.assertEqual(services.login_job('alice')['phase'],'expired')

    @unittest.skipIf(sys.platform=='win32','Linux profile locks')
    def test_profile_lock_excludes_parallel_browser_launch(self):
        from profile_lock import acquire_profile_lock,ProfileBusyError
        with TemporaryDirectory() as temporary:
            with acquire_profile_lock(temporary):
                with self.assertRaises(ProfileBusyError):acquire_profile_lock(temporary)
            acquire_profile_lock(temporary).close()


if __name__=='__main__':unittest.main()
