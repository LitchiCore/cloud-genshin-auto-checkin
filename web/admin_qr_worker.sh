#!/usr/bin/env bash
set +e
ACCOUNT="$1"
[[ "$ACCOUNT" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || exit 4
BASE="$(cd "$(dirname "$0")/.." && pwd)"
D="$BASE/accounts/$ACCOUNT"
S="$D/screenshots"
JOB_LOG="$D/admin-qr-job.log"
EXIT_FILE="$D/admin-qr-exit"
PID_FILE="$D/admin-qr-pid"
JOB_ID_FILE="$D/admin-qr-job-id"
AUTO_PAUSED="$D/auto-paused-login-expired"
TIMERCTL="/usr/local/sbin/cloud-genshin-timerctl"
mkdir -p "$S"
exec 9> "$D/qr-worker.lock"
flock -n 9 || exit 75
rm -f "$EXIT_FILE" "$D/admin-qr-status.json" "$S/qr.png" "$S/qr-full.png" "$S/qr-timeout.png"
python3 - <<'PY' > "$JOB_ID_FILE"
import time, secrets
print(f"{time.time_ns()}-{secrets.token_hex(4)}")
PY
echo $$ > "$PID_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] START $ACCOUNT" > "$JOB_LOG"
"$BASE/.venv/bin/python" -u "$BASE/qr_login.py" "$ACCOUNT" >> "$JOB_LOG" 2>&1
RC=$?
if [ "$RC" -eq 0 ]; then
  if [ -f "$AUTO_PAUSED" ]; then
    sudo -n "$TIMERCTL" enable "$ACCOUNT" >> "$JOB_LOG" 2>&1
    [ "$?" -eq 0 ] && rm -f "$AUTO_PAUSED"
  fi
  python3 - "$ACCOUNT" "$BASE" <<'PY'
from pathlib import Path
from datetime import datetime
import json, sys
account=sys.argv[1]
p=Path(sys.argv[2]) / "accounts" / account / "health.json"
paused=(p.parent / 'auto-paused-login-expired').exists()
data={"status":"ok","consecutive_login_expired":0,"auto_paused":paused,"last_exit_code":0,"updated_at":datetime.now().astimezone().isoformat(timespec="seconds")}
t=p.with_suffix(".json.tmp"); t.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8"); t.replace(p)
if paused:
    status=p.parent / 'admin-qr-status.json'
    info=json.loads(status.read_text(encoding='utf-8'))
    info['message']='云原神登录已恢复，但自动签到恢复失败，请联系管理员开启。'
    status.write_text(json.dumps(info,ensure_ascii=False),encoding='utf-8')
PY
fi
echo "$RC" > "$EXIT_FILE"
rm -f "$PID_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] EXIT $RC" >> "$JOB_LOG"
exit "$RC"
