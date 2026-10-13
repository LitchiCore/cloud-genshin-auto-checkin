from pathlib import Path
from datetime import datetime
import json
import re
import sys
import time

from profile_lock import acquire_profile_lock, ProfileBusyError

BASE = Path(__file__).resolve().parent
URL = 'https://ys.mihoyo.com/cloud/'
IFRAME = '#mihoyo-login-platform-iframe'
SCAN_SECONDS = 180


def verified_minutes(page):
    """A closed login dialog alone is not proof of authentication."""
    if page.locator(IFRAME).is_visible():
        return None
    body = page.locator('body').inner_text(timeout=3000)
    match = re.search(r'免费时长\s*[：:]\s*(\d+)\s*分钟', body)
    return int(match.group(1)) if match else None


def save_json(path, data):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def run(account):
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    directory = BASE / 'accounts' / account
    profile = directory / 'profile'
    shots = directory / 'screenshots'
    shots.mkdir(parents=True, exist_ok=True)
    profile.mkdir(exist_ok=True)

    def status(phase, message, expires_at=None):
        save_json(directory / 'admin-qr-status.json', {
            'phase': phase, 'message': message, 'expires_at': expires_at,
            'updated_at': int(time.time()),
        })

    def finish(page, minutes):
        save_json(directory / 'state.json', {
            'account': account, 'minutes': minutes, 'source': 'qr_login',
            'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        })
        try:
            page.screenshot(path=str(shots / 'login-success.png'), full_page=True)
        except Exception:
            pass  # A diagnostic screenshot must not turn a valid login into failure.
        status('success', '云原神登录已验证，可以继续每日签到。')
        print(f'✅ {account} 登录已验证，免费时长：{minutes} 分钟', flush=True)

    try:
        lock = acquire_profile_lock(directory)
    except ProfileBusyError:
        status('failed', '该账号正在签到或登录，请稍后重试。')
        return 75

    with lock, sync_playwright() as playwright:
        browser = None
        try:
            status('preparing', '正在连接云原神并生成二维码…')
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=True,
                viewport={'width': 1440, 'height': 900},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto(URL, wait_until='domcontentloaded', timeout=90000)
            page.wait_for_timeout(5000)
            minutes = verified_minutes(page)
            if minutes is not None:
                finish(page, minutes)
                return 0

            try:
                page.locator(IFRAME).wait_for(state='visible', timeout=2000)
            except PlaywrightTimeoutError:
                page.get_by_text('开始游戏', exact=True).first.click(timeout=10000)
                page.locator(IFRAME).wait_for(state='visible', timeout=10000)
            frame = page.frame_locator(IFRAME)
            phone = frame.locator('input[placeholder*="手机号"]')
            if phone.count() and phone.first.is_visible():
                frame.locator('.qr-login-btn').first.click(timeout=10000)
            frame.locator('img.qr-loaded').wait_for(state='visible', timeout=10000)
            frame.locator('.qr-wrap').screenshot(path=str(shots / 'qr.png'))
            expires_at = int(time.time()) + SCAN_SECONDS
            status('waiting_scan', '请用米游社或原神 App 扫码，并在手机上确认。', expires_at)
            print('✅ 二维码已生成，等待手机确认。', flush=True)
            deadline = time.monotonic() + SCAN_SECONDS
            verifying = False
            while time.monotonic() < deadline:
                try:
                    minutes = verified_minutes(page)
                    if minutes is not None:
                        finish(page, minutes)
                        return 0
                    if not page.locator(IFRAME).is_visible() and not verifying:
                        status('verifying', '正在验证云原神登录，请稍候…', expires_at)
                        verifying = True
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(1000)
            status('failed', '二维码已过期或登录未完成，请重新生成后扫码。')
            return 2
        except Exception as error:
            print('二维码任务失败：' + type(error).__name__, flush=True)
            status('failed', '暂时无法完成登录，请重新生成二维码；持续失败请联系管理员。')
            return 1
        finally:
            if browser:
                browser.close()


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', sys.argv[1]):
        print('用法：python qr_login.py <账号名>（字母、数字、下划线或短横线）')
        return 4
    return run(sys.argv[1])


if __name__ == '__main__':
    raise SystemExit(main())
