#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime
import json
import re
import subprocess
import sys

BASE = Path(__file__).resolve().parent
TIMERCTL = Path("/usr/local/sbin/cloud-genshin-timerctl")

if len(sys.argv) != 2:
    raise SystemExit("用法：run_monitored.py <账号名>")

account = sys.argv[1]

if not re.fullmatch(r"[A-Za-z0-9_-]+", account):
    raise SystemExit("非法账号名")

ACCOUNT_DIR = BASE / "accounts" / account
HEALTH = ACCOUNT_DIR / "health.json"
AUTO_PAUSED = ACCOUNT_DIR / "auto-paused-login-expired"

if not ACCOUNT_DIR.exists():
    raise SystemExit("账号不存在")


def read_health():
    try:
        return json.loads(
            HEALTH.read_text(encoding="utf-8")
        )
    except Exception:
        return {}


def write_health(data):
    data["updated_at"] = (
        datetime.now()
        .astimezone()
        .isoformat(timespec="seconds")
    )

    tmp = HEALTH.with_suffix(".json.tmp")

    tmp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8",
    )

    tmp.replace(HEALTH)


cmd = [
    str(BASE / ".venv/bin/python"),
    str(BASE / "daily_visit.py"),
    account,
]

proc = subprocess.run(
    cmd,
    capture_output=True,
    text=True,
)

# 完整输出继续进入 journal
if proc.stdout:
    print(proc.stdout, end="")

if proc.stderr:
    print(proc.stderr, end="", file=sys.stderr)

rc = proc.returncode
old = read_health()

if rc == 0:
    write_health({
        "status": "ok",
        "consecutive_login_expired": 0,
        "auto_paused": AUTO_PAUSED.exists(),
        "last_exit_code": 0,
    })

elif rc == 2:
    previous_count = 0

    if old.get("status") == "login_expired":
        previous_count = int(
            old.get(
                "consecutive_login_expired",
                0
            )
        )

    count = previous_count + 1
    paused = AUTO_PAUSED.exists()

    if count >= 3 and not paused:
        print(
            f"⚠️ {account} 连续 {count} 次登录失效，"
            "自动暂停每日任务"
        )

        result = subprocess.run(
            [
                "sudo",
                "-n",
                str(TIMERCTL),
                "disable",
                account,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            AUTO_PAUSED.touch()
            paused = True
            print("✅ timer 已自动暂停")
        else:
            print(
                "❌ 自动暂停 timer 失败：",
                result.stderr.strip()
            )

    write_health({
        "status": "login_expired",
        "consecutive_login_expired": count,
        "auto_paused": paused,
        "last_exit_code": 2,
    })

else:
    # 网络错误、页面异常等不算连续 LOGIN_EXPIRED
    write_health({
        "status": "error",
        "consecutive_login_expired": 0,
        "auto_paused": AUTO_PAUSED.exists(),
        "last_exit_code": rc,
    })

raise SystemExit(rc)
