"""Account operations shared by the website API. No HTTP or presentation code."""
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
import json
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import threading
import time

import store

BASE = store.BASE
ACCOUNTS = BASE / 'accounts'
LOGS = BASE / 'logs'
TIMERCTL = Path('/usr/local/sbin/cloud-genshin-timerctl')
WORKER = BASE / 'web/admin_qr_worker.sh'
OPERATIONS = threading.RLock()


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def read_json(path, default=None):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else (default or {})
    except (OSError, ValueError):
        return default or {}


def write_json(path, data):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def config():
    data = read_json(BASE / 'web/site_config.json')
    data['public_url'] = os.environ.get('CLOUD_GENSHIN_PUBLIC_URL', data.get('public_url', '')).rstrip('/')
    protected = os.environ.get('CLOUD_GENSHIN_PROTECTED_ACCOUNT', '')
    data['protected_accounts'] = [protected] if protected else data.get('protected_accounts', [])
    return data


def account_names(user):
    if user['role'] != 'admin':
        return [user['username']]
    with store.database() as db:
        names = {row['username'] for row in db.execute('SELECT username FROM users')}
    if ACCOUNTS.exists():
        names.update(p.name for p in ACCOUNTS.iterdir() if p.is_dir() and store.valid_username(p.name))
    return sorted(names)


def authorize(user, username):
    if not store.valid_username(username):
        raise Problem('账号名称无效。', 400)
    if user['role'] != 'admin' and user['username'] != username:
        raise Problem('你没有权限操作这个账号。', 403)
    with store.database() as db:
        exists = db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone()
    if not exists and not (ACCOUNTS / username).is_dir():
        raise Problem('账号不存在。', 404)


def timer_status(username):
    try:
        result = subprocess.run(['systemctl', 'show', f'cloud-genshin@{username}.timer',
            '-p', 'UnitFileState', '-p', 'ActiveState', '-p', 'NextElapseUSecRealtime'],
            capture_output=True, text=True, timeout=3)
        if result.returncode:
            raise OSError()
        props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        enabled = props.get('UnitFileState') in {'enabled', 'enabled-runtime'}
        active = props.get('ActiveState') == 'active'
        return {'enabled': enabled, 'active': active, 'known': True,
                'next': props.get('NextElapseUSecRealtime', '') if enabled else ''}
    except (OSError, subprocess.TimeoutExpired):
        return {'enabled': False, 'active': False, 'known': False, 'next': ''}


def worker_running(directory):
    try:
        pid = int((directory / 'admin-qr-pid').read_text().strip())
        if pid <= 1:
            return False
        os.kill(pid, 0)
        command = (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
        return any(arg.endswith(b'admin_qr_worker.sh') for arg in command) and directory.name.encode() in command
    except (OSError, ValueError):
        return False


def login_job(username):
    directory = ACCOUNTS / username
    progress = read_json(directory / 'admin-qr-status.json')
    running = worker_running(directory)
    try: job_id = (directory / 'admin-qr-job-id').read_text().strip()
    except OSError: job_id = ''
    try: exit_code = int((directory / 'admin-qr-exit').read_text().strip())
    except (OSError, ValueError): exit_code = None
    qr = directory / 'screenshots/qr.png'
    remaining = None
    if running:
        phase = progress.get('phase', 'preparing')
        if phase not in {'preparing', 'waiting_scan', 'verifying'}:
            phase = 'verifying'
        if phase == 'waiting_scan' and qr.exists():
            remaining = max(0, int(progress.get('expires_at') or (qr.stat().st_mtime + 180)) - int(time.time()))
    elif exit_code == 0:
        phase = 'expired' if read_json(directory / 'health.json').get('status') == 'login_expired' else 'success'
    elif exit_code is not None:
        phase = 'failed'
    else:
        phase = 'idle'
    return {'phase': phase, 'running': running, 'job_id': job_id, 'remaining': remaining,
            'qr_ready': phase == 'waiting_scan' and qr.exists() and bool(remaining),
            'message': progress.get('message', '')}


def start_login(username):
    with OPERATIONS:
        directory = ACCOUNTS / username
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
        if not worker_running(directory):
            subprocess.Popen([str(WORKER), username], cwd=str(BASE), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            time.sleep(.2)
    return login_job(username)


def log_lines(username):
    path = LOGS / f'{username}.log'
    try:
        # Only read the end of long-lived logs.
        with path.open('rb') as handle:
            handle.seek(max(0, path.stat().st_size - 256_000))
            return handle.read().decode('utf-8', errors='replace').splitlines()[-500:]
    except OSError:
        return []


def records(username, limit=20):
    rows = []
    for line in reversed(log_lines(username)):
        parts = [part.strip() for part in line.split('|')]
        if len(parts) < 3:
            continue
        result = parts[2]
        duration = re.search(r'(\d+)s$', parts[-1])
        delta = re.search(r'Δ([+-]?\d+)', line)
        rows.append({'time': parts[0], 'result': 'ok' if result == 'OK' else 'expired' if result == 'LOGIN_EXPIRED' else 'error',
            'minutes': int(delta.group(1)) if delta else None,
            'duration': int(duration.group(1)) if duration else None})
        if len(rows) >= limit:
            break
    return rows


def account(username, detail=False):
    directory = ACCOUNTS / username
    health = read_json(directory / 'health.json')
    state = read_json(directory / 'state.json')
    status = health.get('status', 'unknown')
    if not state and status != 'ok':
        status = 'unbound'
    latest = records(username, 20 if detail else 1)
    timer = timer_status(username)
    pending = read_json(directory / 'web-run-request.json')
    running_check = bool(pending and time.time() - pending.get('requested_at', 0) < 185
        and pending.get('previous_update') == health.get('updated_at'))
    result = {'username': username, 'status': status, 'checked_at': health.get('updated_at'),
        'minutes': state.get('minutes'), 'timer': timer, 'latest': latest[0] if latest else None,
        'protected': username in config()['protected_accounts'], 'checking': running_check,
        'auto_paused': bool(health.get('auto_paused'))}
    if detail:
        shots = directory / 'screenshots'
        result.update({'records': latest, 'job': login_job(username),
                       'screenshot': (shots / 'last-page.jpg').exists() or (shots / 'last-page.png').exists()})
    return result


def task_action(username, action):
    if action not in {'enable', 'disable', 'run'}:
        raise Problem('无法识别的签到操作。')
    directory = ACCOUNTS / username
    directory.mkdir(parents=True, exist_ok=True)
    if action in {'enable', 'run'} and not (directory / 'state.json').exists():
        raise Problem('请先扫码绑定云原神，再开启签到。', 409)
    if action == 'enable' and read_json(directory / 'health.json').get('status') == 'login_expired':
        raise Problem('请先重新登录云原神，再恢复每日签到。', 409)
    if action == 'run' and login_job(username)['running']:
        raise Problem('正在扫码登录，完成后再检查签到。', 409)
    previous_update = read_json(directory / 'health.json').get('updated_at')
    try:
        result = subprocess.run(['sudo', '-n', str(TIMERCTL), action, username],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise Problem('签到服务暂时无法响应，请稍后重试。', 503) from None
    if result.returncode:
        raise Problem('操作未完成，请联系管理员检查签到服务。', 503)
    if action == 'run':
        # Snapshot before a queued visit completes; API callers poll for a changed update.
        path = directory / 'web-run-request.json'
        write_json(path, {'requested_at': time.time(), 'previous_update': previous_update})
    elif action == 'enable':
        (directory / 'auto-paused-login-expired').unlink(missing_ok=True)
    return account(username, True)


def invite(username):
    directory = ACCOUNTS / username
    with store.database() as db:
        row = db.execute('SELECT password_set FROM users WHERE username=?', (username,)).fetchone()
        if not row:
            raise Problem('网站账号不存在。', 404)
        if row['password_set']:
            raise Problem('该用户已经注册，可在个人设置中修改网站密码。', 409)
        raw = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(10))
        code = raw[:5] + '-' + raw[5:]
        expires = int(time.time()) + 86400
        db.execute('UPDATE users SET setup_token_hash=?,setup_expires=? WHERE username=?',
            (store.invite_digest(code), expires, username))
    directory.mkdir(parents=True, exist_ok=True)
    info = {'code': code, 'expires': expires}
    path = directory / 'admin-invite.json'
    path.write_text(json.dumps(info), encoding='utf-8')
    os.chmod(path, 0o600)
    return info


def existing_invite(username, row):
    info = read_json(ACCOUNTS / username / 'admin-invite.json')
    if not row['password_set'] and info.get('expires', 0) > time.time() and row['setup_token_hash'] == store.invite_digest(info.get('code', '')):
        return info
    return None


def password_reset(username, administrator):
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + 3600
    with store.database() as db:
        row = db.execute('SELECT password_set FROM users WHERE username=?',(username,)).fetchone()
        if not row or not row['password_set']:
            raise Problem('该用户尚未注册，请发送邀请链接。',409)
        db.execute('''INSERT INTO password_resets(username,token_hash,expires,created_by) VALUES(?,?,?,?)
            ON CONFLICT(username) DO UPDATE SET token_hash=excluded.token_hash,
            expires=excluded.expires,created_by=excluded.created_by''',
            (username,hashlib.sha256(token.encode()).hexdigest(),expires,administrator))
    return {'username':username,'token':token,'expires':expires}


def users():
    with store.database() as db:
        rows = db.execute('SELECT username,role,password_set,setup_token_hash,created_at FROM users ORDER BY username').fetchall()
    return [{'username': row['username'], 'role': row['role'], 'registered': bool(row['password_set']),
        'created_at': row['created_at'], 'protected': row['username'] in config()['protected_accounts'],
        'invite': existing_invite(row['username'], row)} for row in rows]


def create_user(username):
    if not store.valid_username(username):
        raise Problem('账号名需为 1–64 位字母、数字、下划线或短横线。')
    with OPERATIONS, store.database() as db:
        if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone() or (ACCOUNTS / username).exists():
            raise Problem('这个账号名已经存在。', 409)
        db.execute('INSERT INTO users(username,role,created_at,password_set) VALUES(?,?,?,0)',
                   (username, 'user', int(time.time())))
        (ACCOUNTS / username).mkdir(parents=True)
    return {'username': username, 'invite': invite(username)}


@contextmanager
def deletion_lock(directory):
    if not directory.exists():
        yield
        return
    import fcntl
    with (directory / 'profile.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Problem('账号正在登录或签到，请等待完成后删除。', 409) from None
        yield


def delete_user(username, current_username):
    if not store.valid_username(username):
        raise Problem('账号名称无效。')
    if username == current_username or username in config()['protected_accounts']:
        raise Problem('此账号受到保护，不能删除。', 409)
    with OPERATIONS, deletion_lock(ACCOUNTS / username):
        directory = ACCOUNTS / username
        if login_job(username)['running']:
            raise Problem('账号正在登录，请等待任务结束后删除。', 409)
        result = subprocess.run(['systemctl', 'is-active', f'cloud-genshin@{username}.service'],
                                capture_output=True, text=True, timeout=3)
        if result.stdout.strip() in {'active', 'activating', 'deactivating'}:
            raise Problem('账号正在签到，请等待完成后删除。', 409)
        task_action(username, 'disable')
        archive = BASE / 'backups' / ('deleted-' + username + '-' + str(time.time_ns()))
        archive.mkdir(parents=True, mode=0o700)
        with store.database() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if row:
                data = {key: {'hex': value.hex()} if isinstance(value, bytes) else value for key, value in dict(row).items()}
                saved = archive / 'website-account.json'
                saved.write_text(json.dumps(data), encoding='utf-8'); os.chmod(saved, 0o600)
            if directory.exists():
                shutil.move(str(directory), str(archive / 'account'))
            log = LOGS / (username + '.log')
            if log.exists():
                shutil.move(str(log), str(archive / log.name))
            db.execute('DELETE FROM sessions WHERE username=?', (username,))
            db.execute('DELETE FROM password_resets WHERE username=?', (username,))
            db.execute('DELETE FROM users WHERE username=?', (username,))
    return {'deleted': True}


def start_network_update(username):
    with OPERATIONS:
        path = BASE / 'web/ipv6_update_status.json'
        current = read_json(path)
        if current.get('status') in {'queued','running','busy'}:
            return current
        queued = {'status':'queued','message':'正在更新 IPv6 直连入口…',
            'updated_at':datetime.now().astimezone().isoformat(timespec='seconds'),'requested_by':username}
        write_json(path, queued)
        def update():
            try:
                with (BASE/'web/ipv6_update_job.log').open('ab') as log:
                    result = subprocess.run(['sudo','-n','/usr/local/sbin/cloud-genshin-ipv6-update'],stdin=subprocess.DEVNULL,
                        stdout=log,stderr=subprocess.STDOUT,timeout=300)
                if result.returncode:
                    raise OSError()
            except (OSError,subprocess.TimeoutExpired):
                write_json(path, {'status':'error','message':'直连更新未完成，请联系管理员检查服务。',
                    'updated_at':datetime.now().astimezone().isoformat(timespec='seconds')})
        threading.Thread(target=update,daemon=True).start()
        return queued
