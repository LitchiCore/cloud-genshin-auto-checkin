from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright
import json
import re
import sys
import time

BASE = Path(__file__).resolve().parent
URL = "https://ys.mihoyo.com/cloud/"


def dismiss_reward_popup(page):
    """
    等待并关闭“每日登录奖励”弹窗。
    最多等待约 6 秒，并检查主页面及所有 iframe。
    """

    deadline = time.monotonic() + 6
    found = False

    while time.monotonic() < deadline:
        for frame in page.frames:
            candidates = [
                frame.get_by_role(
                    "button",
                    name="我知道了",
                    exact=True
                ),
                frame.get_by_text(
                    "我知道了",
                    exact=True
                ),
                frame.locator(
                    'button:has-text("我知道了")'
                ),
                frame.locator(
                    '[class*="btn"]:has-text("我知道了")'
                ),
            ]

            for locator in candidates:
                try:
                    if locator.count() < 1:
                        continue

                    target = locator.first

                    if not target.is_visible():
                        continue

                    found = True
                    print("检测到每日登录奖励弹窗，关闭中...")

                    try:
                        target.click(
                            timeout=1500
                        )
                    except Exception:
                        try:
                            target.click(
                                force=True,
                                timeout=1500
                            )
                        except Exception:
                            target.dispatch_event(
                                "click"
                            )

                    page.wait_for_timeout(1200)

                    print("✅ 已点击「我知道了」")
                    return True

                except Exception:
                    pass

        page.wait_for_timeout(300)

    if found:
        print("⚠️ 找到了奖励按钮，但未能成功关闭")
    else:
        print("未发现需要关闭的每日奖励弹窗")

    return False


def die(msg, code=1):
    print(msg)
    raise SystemExit(code)

if len(sys.argv) != 2:
    die("用法：python daily_visit.py <账号名>")

account = sys.argv[1]

if not re.fullmatch(r"[A-Za-z0-9_-]+", account):
    die("账号名只能包含字母、数字、下划线和短横线")

ACCOUNT_DIR = BASE / "accounts" / account
PROFILE = ACCOUNT_DIR / "profile"
SCREENSHOTS = ACCOUNT_DIR / "screenshots"
STATE_FILE = ACCOUNT_DIR / "state.json"

LOG_DIR = BASE / "logs"
LOG_FILE = LOG_DIR / f"{account}.log"

if not PROFILE.exists():
    die(f"账号 {account} 尚未建立登录 Profile", 4)

SCREENSHOTS.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

start_monotonic = time.monotonic()
now = datetime.now()
stamp = now.strftime("%Y%m%d-%H%M%S")

def load_previous_minutes():
    # 优先读取 state.json
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        value = data.get("minutes")
        if isinstance(value, int):
            return value
    except Exception:
        pass

    # 兼容升级前已有的简洁日志，尝试从最后记录中恢复基线
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            matches = re.findall(r"(\d+)\s*min", line)
            if matches:
                return int(matches[-1])
    except Exception:
        pass

    return None

def save_state(minutes):
    data = {
        "account": account,
        "minutes": minutes,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "daily_visit",
    }

    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(STATE_FILE)

def append_log(status, minutes=None, detail=None):
    end = datetime.now()
    duration = round(time.monotonic() - start_monotonic)

    if status == "OK" and minutes is not None:
        previous = load_previous_minutes()

        if previous is None:
            line = (
                f"{end:%Y-%m-%d %H:%M:%S} | {account} | OK | "
                f"{minutes} min | baseline | {duration}s"
            )
        else:
            delta = minutes - previous
            delta_text = f"Δ{delta:+d}" if delta != 0 else "Δ0"

            line = (
                f"{end:%Y-%m-%d %H:%M:%S} | {account} | OK | "
                f"{previous}→{minutes} min | {delta_text} | {duration}s"
            )

        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

        save_state(minutes)
        return

    line = (
        f"{end:%Y-%m-%d %H:%M:%S} | {account} | "
        f"{status} | {detail or '-'} | {duration}s"
    )

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

print("=" * 55)
print(f"云·原神每日访问：{account}")
print("时间：", now.strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 55)

browser = None

try:
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=True,
            viewport={"width": 1440, "height": 900},
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        response = page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=90000,
        )

        if response:
            print("HTTP:", response.status)

        # 页面加载本身可能触发每日登录相关请求
        page.wait_for_timeout(15000)

        # 每日登录奖励已经到账后，网页可能弹出“我知道了”。
        # 主动关闭它，避免遮挡截图，也减少后续 DOM 干扰。
        try:
            ack = page.get_by_text("我知道了", exact=True).last
            ack.wait_for(state="visible", timeout=1200)
            ack.click(timeout=3000)
            print("检测到每日登录奖励弹窗，已点击‘我知道了’")
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # 每日奖励可能在页面加载完成数秒后才出现
        dismiss_reward_popup(page)

        body = page.locator("body").inner_text(timeout=10000)

        # 保存每次执行后的完整页面，只保留最新一张，避免长期占用磁盘。
        try:
            page.screenshot(
                path=str(SCREENSHOTS / "last-page.jpg"),
                type="jpeg",
                quality=65,
                full_page=True,
            )
        except Exception as e:
            print("保存完整页面截图失败：", repr(e))

        match = re.search(
            r"免费时长\s*[：:]\s*(\d+)\s*分钟",
            body
        )

        if match:
            minutes = int(match.group(1))

            print(f"✅ {account} 登录状态有效")
            print(f"免费时长：{minutes} 分钟")
            print("每日访问完成")

            append_log("OK", minutes=minutes)
            browser.close()
            raise SystemExit(0)

        if "开始游戏" in body:
            print(f"❌ {account} 登录状态可能已经失效")

            page.screenshot(
                path=str(SCREENSHOTS / f"login-expired-{stamp}.png"),
                full_page=True,
            )

            append_log("LOGIN_EXPIRED", detail="relogin required")
            browser.close()
            raise SystemExit(2)

        print(f"⚠️ {account} 页面状态无法识别")
        print(body[:2000])

        page.screenshot(
            path=str(SCREENSHOTS / f"unknown-{stamp}.png"),
            full_page=True,
        )

        append_log("FAIL(3)", detail="unknown page")
        browser.close()
        raise SystemExit(3)

except SystemExit:
    raise

except Exception as e:
    print("❌ 执行异常：", repr(e))

    append_log(
        "FAIL(1)",
        detail=type(e).__name__
    )

    try:
        if browser:
            browser.close()
    except Exception:
        pass

    raise SystemExit(1)
