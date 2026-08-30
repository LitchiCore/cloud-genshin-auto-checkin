from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import json
import re
import sys
import time

BASE = Path(__file__).resolve().parent
URL = "https://ys.mihoyo.com/cloud/"
IFRAME = "#mihoyo-login-platform-iframe"

def die(msg, code=1):
    print(msg)
    raise SystemExit(code)

if len(sys.argv) != 2:
    die("用法：python qr_login.py <账号名>")

account = sys.argv[1]

if not re.fullmatch(r"[A-Za-z0-9_-]+", account):
    die("账号名只能包含字母、数字、下划线和短横线")

ACCOUNT_DIR = BASE / "accounts" / account
PROFILE = ACCOUNT_DIR / "profile"
SCREENSHOTS = ACCOUNT_DIR / "screenshots"
STATE_FILE = ACCOUNT_DIR / "state.json"

ACCOUNT_DIR.mkdir(parents=True, exist_ok=True)
PROFILE.mkdir(parents=True, exist_ok=True)
SCREENSHOTS.mkdir(parents=True, exist_ok=True)

def save_baseline(minutes):
    if minutes is None:
        return

    data = {
        "account": account,
        "minutes": minutes,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "qr_login",
    }

    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(STATE_FILE)

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=True,
        viewport={"width": 1440, "height": 900},
    )

    page = browser.pages[0] if browser.pages else browser.new_page()

    print(f"云·原神扫码登录：{account}")
    print("打开云·原神...")

    page.goto(
        URL,
        wait_until="domcontentloaded",
        timeout=90000,
    )

    page.wait_for_timeout(5000)

    body = page.locator("body").inner_text(timeout=10000)

    match = re.search(
        r"免费时长\s*[：:]\s*(\d+)\s*分钟",
        body
    )

    if match:
        minutes = int(match.group(1))
        save_baseline(minutes)

        print(f"✅ {account} 已经处于登录状态")
        print(f"免费时长：{minutes} 分钟")
        print("已更新时长基线。")

        browser.close()
        raise SystemExit(0)

    try:
        page.locator(IFRAME).wait_for(
            state="visible",
            timeout=2000,
        )
    except PlaywrightTimeoutError:
        print("打开登录窗口...")

        start = page.get_by_text("开始游戏", exact=True)

        if start.count() == 0:
            page.screenshot(
                path=str(SCREENSHOTS / "login-page-error.png"),
                full_page=True,
            )
            die("❌ 没找到“开始游戏”")

        start.dispatch_event("click")

        page.locator(IFRAME).wait_for(
            state="visible",
            timeout=10000,
        )

    frame = page.frame_locator(IFRAME)

    phone = frame.locator('input[placeholder*="手机号"]')

    phone_visible = False

    if phone.count() > 0:
        try:
            phone_visible = phone.first.is_visible()
        except Exception:
            pass

    if phone_visible:
        print("切换到扫码登录...")

        qr_btn = frame.locator(".qr-login-btn").first
        qr_btn.wait_for(state="visible", timeout=10000)

        box = qr_btn.bounding_box()

        if not box:
            die("❌ 无法取得二维码按钮坐标")

        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2

        page.mouse.move(x, y)
        page.wait_for_timeout(300)
        page.mouse.down()
        page.wait_for_timeout(100)
        page.mouse.up()

        page.wait_for_timeout(3000)

    qr = frame.locator("img.qr-loaded")

    try:
        qr.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeoutError:
        page.screenshot(
            path=str(SCREENSHOTS / "qr-error.png"),
            full_page=True,
        )
        die("❌ 二维码没有出现")

    qr_wrap = frame.locator(".qr-wrap")
    qr_wrap.wait_for(state="visible", timeout=5000)

    # 截取二维码外层容器，给二维码四周多留一圈空白
    qr_wrap.screenshot(
        path=str(SCREENSHOTS / "qr.png")
    )

    page.screenshot(
        path=str(SCREENSHOTS / "qr-full.png"),
        full_page=True,
    )

    print()
    print("✅ 二维码已生成：")
    print(SCREENSHOTS / "qr.png")
    print()
    print("请扫码并在手机端确认。")
    print("等待最多 3 分钟...")

    success = False

    for remaining in range(180, 0, -1):
        try:
            if not page.locator(IFRAME).is_visible():
                success = True
                break
        except Exception:
            success = True
            break

        if remaining % 10 == 0:
            print(f"等待确认... 剩余 {remaining} 秒")

        time.sleep(1)

    if not success:
        page.screenshot(
            path=str(SCREENSHOTS / "qr-timeout.png"),
            full_page=True,
        )

        browser.close()
        die("❌ 扫码等待超时", 2)

    print()
    print(f"✅ {account} 扫码登录成功")

    page.wait_for_timeout(5000)

    body = page.locator("body").inner_text(timeout=10000)

    match = re.search(
        r"免费时长\s*[：:]\s*(\d+)\s*分钟",
        body
    )

    if match:
        minutes = int(match.group(1))
        save_baseline(minutes)

        print(f"免费时长：{minutes} 分钟")
        print("已保存为下次 daily_visit 的比较基线。")

    page.screenshot(
        path=str(SCREENSHOTS / "login-success.png"),
        full_page=True,
    )

    browser.close()
