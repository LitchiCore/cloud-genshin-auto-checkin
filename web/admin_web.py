#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, urlencode
import hashlib, hmac, html, json, os, re, secrets, shutil, sqlite3, subprocess, threading, time

BASE = Path(__file__).resolve().parent.parent
ACCOUNTS = BASE / 'accounts'
LOG_DIR = BASE / 'logs'
DB = BASE / 'web' / 'users.db'
WORKER = BASE / 'web' / 'admin_qr_worker.sh'
TIMERCTL = Path('/usr/local/sbin/cloud-genshin-timerctl')
IPV6_STATUS_FILE = BASE / 'web' / 'ipv6_status.json'
HOST, PORT = '127.0.0.1', 8003
INVITE_SECONDS = 24 * 3600
USERNAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
INVITE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
PUBLIC_URL = os.environ.get('CLOUD_GENSHIN_PUBLIC_URL', 'http://127.0.0.1:8001').rstrip('/')
PROTECTED_ACCOUNT = os.environ.get('CLOUD_GENSHIN_PROTECTED_ACCOUNT', '')
RELOGIN_CSRF_KEY = secrets.token_bytes(32)
JOB_START_LOCK = threading.Lock()


def esc(v):
    return html.escape(str(v), quote=True)


def valid_username(v):
    return bool(USERNAME_RE.fullmatch(v or ''))


def connect():
    db = sqlite3.connect(DB)
    db.execute('''CREATE TABLE IF NOT EXISTS users(
        username TEXT PRIMARY KEY,
        password_salt BLOB,
        password_hash BLOB,
        role TEXT NOT NULL DEFAULT 'user',
        setup_token_hash TEXT,
        setup_expires INTEGER,
        created_at INTEGER NOT NULL,
        password_set INTEGER NOT NULL DEFAULT 0
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS sessions(
        token_hash TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        expires INTEGER NOT NULL
    )''')
    db.commit()
    return db


def get_user_from_cookie(headers):
    try:
        c = SimpleCookie(); c.load(headers.get('Cookie', ''))
        if 'cg_session' not in c:
            return None
        th = hashlib.sha256(c['cg_session'].value.encode()).hexdigest()
        db = connect()
        row = db.execute('''SELECT u.username,u.role,s.expires FROM sessions s
                            JOIN users u ON u.username=s.username WHERE s.token_hash=?''', (th,)).fetchone()
        db.close()
        if not row or row[2] < int(time.time()):
            return None
        return {'username': row[0], 'role': row[1]}
    except Exception:
        return None


def ensure_user_record(username):
    db = connect()
    row = db.execute('SELECT password_set,role FROM users WHERE username=?', (username,)).fetchone()
    if not row:
        db.execute('INSERT INTO users(username,role,created_at,password_set) VALUES(?,?,?,0)',
                   (username, 'user', int(time.time())))
        db.commit()
        row = (0, 'user')
    db.close()
    return {'password_set': bool(row[0]), 'role': row[1]}


def load_health(username):
    try:
        return json.loads((ACCOUNTS / username / 'health.json').read_text(encoding='utf-8'))
    except Exception:
        return {'status': 'unknown', 'consecutive_login_expired': 0, 'auto_paused': False}


def pid_alive(pid_file):
    try:
        os.kill(int(pid_file.read_text().strip()), 0)
        return True
    except Exception:
        return False


def timer_status(username):
    try:
        r = subprocess.run(['sudo','-n',str(TIMERCTL),'status',username], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else 'unknown'
    except Exception:
        return 'unknown'


def next_run(username):
    try:
        r = subprocess.run(['systemctl','show',f'cloud-genshin@{username}.timer','-p','NextElapseUSecRealtime','--value'],
                           capture_output=True,text=True,timeout=3)
        return r.stdout.strip() or '—'
    except Exception:
        return '—'


def new_invite_code():
    raw = ''.join(secrets.choice(INVITE_ALPHABET) for _ in range(10))
    return raw[:5] + '-' + raw[5:]


def normalize_invite(code):
    return ''.join(c for c in code.upper() if c.isalnum())


def generate_invite(username, force=False):
    account_dir = ACCOUNTS / username
    account_dir.mkdir(parents=True, exist_ok=True)
    info_file = account_dir / 'admin-invite.json'
    db = connect()
    row = db.execute('SELECT password_set,setup_token_hash,setup_expires FROM users WHERE username=?', (username,)).fetchone()
    if not row:
        db.close()
        return None
    password_set, token_hash, expires = row
    if password_set:
        db.close()
        try: info_file.unlink()
        except FileNotFoundError: pass
        return None
    if not force and info_file.exists() and token_hash and expires and expires > int(time.time()):
        try:
            info = json.loads(info_file.read_text(encoding='utf-8'))
            if info.get('expires') == expires:
                return info
        except Exception:
            pass
    code = new_invite_code()
    normalized = normalize_invite(code)
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    expires = int(time.time()) + INVITE_SECONDS
    db.execute('UPDATE users SET setup_token_hash=?,setup_expires=? WHERE username=?', (digest, expires, username))
    db.commit(); db.close()
    info = {'code': code, 'expires': expires}
    info_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(info_file, 0o600)
    return info


def job_status(username):
    d = ACCOUNTS / username
    pid_file = d / 'admin-qr-pid'
    exit_file = d / 'admin-qr-exit'
    job_id_file = d / 'admin-qr-job-id'
    qr = d / 'screenshots' / 'qr.png'
    try: progress = json.loads((d / 'admin-qr-status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): progress = {}
    running = pid_alive(pid_file)
    try: job_id = job_id_file.read_text().strip()
    except Exception: job_id = ''
    exit_code = None
    if exit_file.exists():
        try: exit_code = int(exit_file.read_text().strip())
        except Exception: pass
    remaining = None
    if running and qr.exists():
        remaining = max(0, int(progress.get('expires_at') or (qr.stat().st_mtime + 180)) - int(time.time()))
    if running:
        phase = progress.get('phase', 'waiting_scan' if qr.exists() else 'preparing')
        if phase not in {'preparing', 'waiting_scan', 'verifying'}:
            phase = 'verifying'  # Wait until the worker has updated health and exited.
    elif exit_code == 0:
        phase = 'expired' if load_health(username).get('status') == 'login_expired' else 'success'
    elif exit_code is not None:
        phase = 'failed'
    else:
        phase = 'idle'
    return {
        'phase': phase,
        'running': running,
        'qr_ready': qr.exists() and phase == 'waiting_scan' and remaining != 0,
        'remaining': remaining,
        'exit_code': exit_code,
        'job_id': job_id,
        'message': progress.get('message', ''),
    }


def start_qr_job(username):
    # Serialize the PID check/start in this service; the worker also takes a file lock.
    with JOB_START_LOCK:
        directory = ACCOUNTS / username
        (directory / 'screenshots').mkdir(parents=True, exist_ok=True)
        if pid_alive(directory / 'admin-qr-pid'):
            return
        subprocess.Popen([str(WORKER), username], cwd=str(BASE), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(.3)


def relogin_csrf(headers, username):
    cookie = SimpleCookie()
    cookie.load(headers.get('Cookie', ''))
    token = cookie['cg_session'].value if 'cg_session' in cookie else ''
    return hmac.new(RELOGIN_CSRF_KEY, (token + '\0' + username).encode(), hashlib.sha256).hexdigest()


STYLE = '''
<style>
:root{color-scheme:dark}body{max-width:920px;margin:32px auto;padding:0 18px;background:#111;color:#eee;font-family:system-ui,-apple-system,sans-serif}
a{color:#8ab4f8;text-decoration:none}.card{padding:18px;margin:16px 0;border:1px solid #333;border-radius:12px;background:#1b1b1b}
input{box-sizing:border-box;width:100%;max-width:420px;padding:11px;margin:8px 0 14px;border:1px solid #444;border-radius:8px;background:#151515;color:white;font-size:16px}
.flow{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:20px 0}.flow span{padding:7px 12px;border-radius:20px;background:#252525;color:#aaa}.flow .current{background:#203d36;color:#b1efdb}.primary{background:#a8e4cd;color:#10251e}button:disabled{opacity:.55;cursor:wait}.row form{margin:0}.qr{background:white;padding:12px;box-sizing:border-box}.notice{border-left:3px solid #d7ad54;padding-left:14px}
button,.button{display:inline-block;padding:10px 16px;border:0;border-radius:8px;background:#eee;color:#111;cursor:pointer;font-size:15px;text-decoration:none}.secondary{background:#333;color:#eee}.danger{background:#8b1e1e!important;color:#fff!important}
.qr{display:block;width:220px;max-width:75vw;image-rendering:pixelated;margin:18px 0;border-radius:8px}pre,.code{white-space:pre-wrap;word-break:break-word;padding:12px;background:#080808;border-radius:8px;font-family:ui-monospace,monospace}
.ok{color:#9be29b}.warn{color:#ffd27a}.err{color:#ff8c8c}.small{opacity:.65;font-size:13px}.row{display:flex;gap:8px;flex-wrap:wrap}.user{display:flex;justify-content:space-between;gap:12px;align-items:center}
.button.primary,button.primary{background:#a8e4cd;color:#10251e}summary{cursor:pointer}a:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid #a8e4cd;outline-offset:4px}@media(max-width:540px){.user{display:block}.user .row{margin-top:14px}.small{line-height:1.6}}
</style>
'''



def ipv6_status_banner():
    try:
        data = json.loads(
            IPV6_STATUS_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return ""

    status = data.get(
        "status",
        "unknown"
    )

    expected = data.get(
        "expected_ipv6"
    ) or "?"

    current = data.get(
        "current_ipv6"
    ) or "未检测到"

    interface = data.get(
        "interface"
    ) or "?"

    checked = data.get(
        "checked_at"
    ) or "?"


    # -------------------------------------------------
    # updater 状态
    #
    # 只有 queued / running 才覆盖 Watch 的正常显示。
    # ok/error 都回到实际 IPv6 Watch 状态。
    # -------------------------------------------------

    update_data = {}

    try:
        update_file = (
            BASE
            / "web"
            / "ipv6_update_status.json"
        )

        update_data = json.loads(
            update_file.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        update_data = {}


    update_status = update_data.get(
        "status"
    )

    update_message = update_data.get(
        "message"
    ) or "后台正在处理 IPv6 直连。"


    # -------------------------------------------------
    # 更新中
    # -------------------------------------------------

    if update_status in (
        "queued",
        "running",
        "busy",
    ):

        update_current = update_data.get(
            "new_ipv6"
        ) or current

        return f"""
<div class="card"
     style="border-color:#8a6d25">

<h2 class="warn">
◌ IPv6 直连更新中
</h2>

<p>
{esc(update_message)}
</p>

<p>
当前检测地址：
</p>

<div class="code">
{esc(update_current)}
</div>

<p class="small">
更新任务在后台运行，
无需保持当前页面打开。
<br>
Cloudflare Tunnel 主入口不受影响。
</p>

</div>
"""


    # -------------------------------------------------
    # 正常
    # -------------------------------------------------

    if status == "ok":

        ip = (
            current
            if current != "未检测到"
            else expected
        )

        direct_url = (
            f"https://[{ip}]:8000/"
        )

        return f"""
<div class="card"
     style="border-color:#376b45">

<h2 class="ok">
✓ IPv6 直连正常
</h2>

<p>
当前公网 IPv6：
</p>

<div class="code">
{esc(ip)}
</div>

<p>
IPv6 高速直连：
</p>

<div class="code">
<a
    href="{esc(direct_url)}"
    target="_blank"
    rel="noopener noreferrer"
>
{esc(direct_url)}
</a>
</div>

<p style="margin-top:14px">
<a
    class="button secondary"
    href="{esc(direct_url)}"
    target="_blank"
    rel="noopener noreferrer"
>
打开 IPv6 直连
</a>
</p>

<p class="small">
接口：{esc(interface)}
<br>
检查时间：{esc(checked)}
<br>
Cloudflare Tunnel 主入口同时保持可用。
</p>

</div>
"""


    # -------------------------------------------------
    # IPv6 已变化
    # -------------------------------------------------

    if status == "changed":

        error_html = ""

        if update_status == "error":

            message = update_data.get(
                "message"
            ) or "IPv6 更新任务失败"

            # 管理首页不塞进过长的 Certbot traceback。
            if len(message) > 900:
                message = (
                    message[:900]
                    + "\n……"
                )

            error_html = f"""
<div
    class="code"
    style="
        border:1px solid #633;
        margin-top:14px;
    "
>
<strong class="err">
最近一次更新失败：
</strong>
<br>
{esc(message)}
</div>
"""


        return f"""
<div class="card"
     style="border-color:#a33">

<h2 class="err">
⚠ 公网 IPv6 已变化
</h2>

<p>
原直连地址：
</p>

<div class="code">
{esc(expected)}
</div>

<p>
当前检测地址：
</p>

<div class="code">
{esc(current)}
</div>

<p class="small">
接口：{esc(interface)}
<br>
检查时间：{esc(checked)}
</p>

<p>
<strong>
Cloudflare Tunnel 主入口不受影响。
</strong>
</p>

{error_html}

<form
    method="post"
    action="/admin/ipv6/update"
    style="margin-top:18px"
    onsubmit="return confirm('将自动申请新的 IPv6 HTTPS 证书并更新 :8000 直连。继续吗？');"
>

<button type="submit">
一键更新 IPv6 直连
</button>

</form>

<p class="small">
系统会重新检测本机当前公网 IPv6，
不会使用浏览器提交的 IP 地址。
<br>
更新任务会在后台运行，
可以离开页面。
</p>

</div>
"""


    # -------------------------------------------------
    # 没有公网 IPv6
    # -------------------------------------------------

    if status == "unavailable":

        return f"""
<div class="card"
     style="border-color:#8a6d25">

<h2 class="warn">
⚠ 当前没有检测到公网 IPv6
</h2>

<p>
原 IPv6 直连地址：
</p>

<div class="code">
{esc(expected)}
</div>

<p class="small">
接口：{esc(interface)}
<br>
检查时间：{esc(checked)}
<br>
这可能只是临时断网或运营商暂时未下发公网 IPv6。
<br>
Cloudflare Tunnel 主入口不受影响。
</p>

</div>
"""


    # -------------------------------------------------
    # 未知状态
    # -------------------------------------------------

    return f"""
<div class="card"
     style="border-color:#8a6d25">

<strong class="warn">
⚠ IPv6 状态未知
</strong>

<p class="small">
状态：{esc(status)}
<br>
当前地址：{esc(current)}
<br>
检查时间：{esc(checked)}
</p>

</div>
"""


def page(title, body):
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>{STYLE}</head><body>
<p><a href="/">← 账号首页</a> · <a href="/admin/">账号管理</a> · <a href="/admin/#create">＋ 添加新用户</a></p>{body}<details class="card"><summary>网络入口与 IPv6 状态</summary>{ipv6_status_banner()}</details></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def admin(self):
        u = get_user_from_cookie(self.headers)
        return u if u and u['role'] == 'admin' else None

    def require_admin(self):
        u = self.admin()
        if not u:
            path = urlparse(self.path).path
            target = path if path.startswith('/admin/job/') and valid_username(path[len('/admin/job/'):]) else '/admin/'
            self.redirect('/login?' + urlencode({'next':target}))
        return u

    def send_html(self, content, status=200):
        data = content.encode()
        self.send_response(status)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def redirect(self, path):
        self.send_response(303); self.send_header('Location',path); self.end_headers()

    def read_form(self):
        try: n=min(int(self.headers.get('Content-Length','0')),8192)
        except ValueError: return {}
        p=parse_qs(self.rfile.read(n).decode('utf-8',errors='replace'))
        return {k:v[0] for k,v in p.items() if v}

    def do_GET(self):
        if self.relogin_route('GET'): return
        if not self.require_admin(): return
        p=urlparse(self.path)
        if p.path in ('/admin','/admin/'):
            return self.index()
        if p.path.startswith('/admin/api/job/'):
            username=unquote(p.path[len('/admin/api/job/'):])
            if not valid_username(username): return self.send_error(400)
            return self.send_json(job_status(username))
        if p.path.startswith('/admin/api/health/'):
            username=unquote(p.path[len('/admin/api/health/'):])
            if not valid_username(username): return self.send_error(400)
            return self.send_json(load_health(username))
        if p.path.startswith('/admin/job/'):
            username=unquote(p.path[len('/admin/job/'):])
            if not valid_username(username): return self.send_error(400)
            return self.job_page(username)
        if p.path.startswith('/admin/qr/'):
            username=unquote(p.path[len('/admin/qr/'):])
            if not valid_username(username): return self.send_error(400)
            return self.send_qr(username, parse_qs(p.query).get('download',['0'])[0]=='1')
        if p.path.startswith('/admin/delete/'):
            username=unquote(p.path[len('/admin/delete/'):])
            if not valid_username(username) or username == PROTECTED_ACCOUNT: return self.send_error(400)
            return self.delete_page(username)
        self.send_error(404)

    def do_POST(self):
        if self.relogin_route('POST'): return
        if not self.require_admin(): return
        p=urlparse(self.path)
        if p.path=='/admin/create': return self.create_user()
        if p.path.startswith('/admin/invite/'):
            username=unquote(p.path[len('/admin/invite/'):])
            if not valid_username(username): return self.send_error(400)
            generate_invite(username, force=True)
            return self.redirect(f'/admin/job/{username}')
        if p.path.startswith('/admin/timer/'):
            parts=p.path.split('/')
            if len(parts)!=5: return self.send_error(400)
            username,action=unquote(parts[3]),parts[4]
            if not valid_username(username) or action not in {'enable','disable','run'}: return self.send_error(400)
            try:
                r=subprocess.run(['sudo','-n',str(TIMERCTL),action,username],capture_output=True,text=True,timeout=20 if action!='run' else 5)
            except Exception as e:
                return self.send_html(page('操作失败',f'<h1 class="err">操作失败</h1><pre>{esc(e)}</pre>'),500)
            if r.returncode!=0:
                return self.send_html(page('操作失败',f'<h1 class="err">操作失败</h1><pre>{esc(r.stdout)}\n{esc(r.stderr)}</pre>'),500)
            return self.redirect(f'/admin/job/{username}' + ('?checking=1' if action == 'run' else '?saved=1'))
        if p.path.startswith('/admin/delete/'):
            username=unquote(p.path[len('/admin/delete/'):])
            if not valid_username(username) or username == PROTECTED_ACCOUNT: return self.send_error(400)
            return self.delete_user(username)
        self.send_error(404)

    def index(self):
        db=connect()
        db_rows={r[0]:{'role':r[1],'password_set':bool(r[2])} for r in db.execute('SELECT username,role,password_set FROM users').fetchall()}
        db.close()
        names=set(db_rows)
        if ACCOUNTS.exists():
            names.update(p.name for p in ACCOUNTS.iterdir() if p.is_dir() and valid_username(p.name))
        rows=[]
        for username in sorted(names):
            info=db_rows.get(username,{'role':'user','password_set':False})
            st=job_status(username)
            profile=(ACCOUNTS/username/'profile').exists()
            if info['password_set']: web='密码已设置'
            elif st['phase']=='success': web='等待邀请码注册'
            else: web='尚未注册'
            health=load_health(username).get('status', 'unknown')
            label={'ok':'登录正常','login_expired':'需要重新登录','error':'最近执行异常'}.get(health,'尚未检查')
            protected=' · 受保护账号' if username == PROTECTED_ACCOUNT else ''
            rows.append(f'''<div class="card user"><div><strong>{esc(username)}</strong><br><span class="small">{esc(label)} · {esc(web)}{protected}</span></div><div class="row"><a class="button secondary" href="/admin/job/{esc(username)}">账号设置</a><a class="button primary" href="/admin/relogin/{esc(username)}">{'重新登录' if health == 'login_expired' else '扫码登录'}</a></div></div>''')
        body=f'''<h1>账号管理</h1><p class="small">查看云原神状态，重新登录，或调整每日签到。</p><h2>现有账号</h2>{''.join(rows) or '<p>暂无账号。</p>'}<div class="card" id="create"><h2>＋ 添加新用户</h2>
<form method="post" action="/admin/create"><label>用户名</label><br><input name="username" placeholder="例如 xiaoming" pattern="[A-Za-z0-9_-]+" required><br><button type="submit">创建并生成二维码</button></form>
<p class="small">创建后先扫码登录云原神，再发送邀请码设置网站密码。</p></div>'''
        self.send_html(page('用户管理',body))

    def create_user(self):
        username=self.read_form().get('username','').strip()
        if not valid_username(username) or username == PROTECTED_ACCOUNT:
            return self.send_html(page('错误','<h1 class="err">非法用户名</h1>'),400)
        ensure_user_record(username)
        start_qr_job(username)
        self.redirect(f'/admin/job/{username}')

    def relogin_route(self, method):
        path = urlparse(self.path).path
        routes = {'/admin/relogin/': 'page', '/admin/relogin-status/': 'status',
                  '/admin/relogin-qr/': 'qr'}
        prefix = next((prefix for prefix in routes if path.startswith(prefix)), None)
        if prefix is None:
            return False
        username = unquote(path[len(prefix):])
        if not valid_username(username):
            self.send_error(400); return True
        user = get_user_from_cookie(self.headers)
        if not user:
            self.redirect('/login?' + urlencode({'next':'/admin/relogin/' + username})); return True
        if user['role'] != 'admin' and user['username'] != username:
            self.send_error(403); return True
        if not (ACCOUNTS / username).is_dir():
            self.send_error(404); return True
        kind = routes[prefix]
        if method == 'POST':
            if kind != 'page':
                self.send_error(405); return True
            form = self.read_form()
            if not secrets.compare_digest(form.get('csrf', ''), relogin_csrf(self.headers, username)):
                self.send_html(f'<h1>页面已失效</h1><p>服务已更新，请刷新页面后继续。</p><a href="/admin/relogin/{esc(username)}">返回重新登录页面</a>', 403)
                return True
            start_qr_job(username)
            self.redirect('/admin/relogin/' + username)
        elif kind == 'status':
            self.send_json(job_status(username))
        elif kind == 'qr':
            status = job_status(username)
            job = parse_qs(urlparse(self.path).query).get('job', [''])[0]
            if not status['qr_ready'] or job != status['job_id']:
                self.send_error(410)
            else:
                self.send_qr(username, parse_qs(urlparse(self.path).query).get('download', ['0'])[0] == '1')
        else:
            self.relogin_page(username, user)
        return True

    def relogin_page(self, username, user):
        status = job_status(username)
        phase = status['phase']
        titles = {'idle':'重新登录云原神', 'expired':'云原神登录已失效',
                  'preparing':'正在生成二维码', 'waiting_scan':'请扫码并在手机上确认',
                  'verifying':'正在验证登录', 'success':'云原神登录已恢复', 'failed':'本次登录未完成'}
        active = status['running']
        title = titles.get(phase, '重新登录云原神')
        if phase in {'idle', 'expired'}:
            message = '点击下方按钮生成二维码。扫码只更新云原神登录，不需要重设网站密码。'
        elif phase == 'success':
            message = status['message'] or '已验证云原神登录。你可以返回账号详情查看状态。'
        else:
            message = status['message'] or ('正在连接云原神，请稍候…' if active else '请重新生成二维码后再试。')
        csrf = relogin_csrf(self.headers, username)
        action = '' if active else f'''<form method="post" action="/admin/relogin/{esc(username)}"><input type="hidden" name="csrf" value="{csrf}"><button class="primary" type="submit">{'生成二维码' if phase in {'idle','expired'} else '重新生成二维码'}</button></form>'''
        qr = ''
        if status['qr_ready']:
            url = f'/admin/relogin-qr/{esc(username)}?job={esc(status["job_id"])}'
            qr = f'''<img class="qr" src="{url}" alt="云原神登录二维码"><p>有效期剩余 <strong id="countdown">{status['remaining']}</strong> 秒</p><a class="button secondary" href="{url}&download=1">下载二维码</a><p class="small">同一手机操作时，可下载图片后在 App 中识别。</p>'''
        admin_link = f' · <a href="/admin/job/{esc(username)}">账号设置</a>' if user['role'] == 'admin' else ''
        initial = json.dumps({k:status[k] for k in ('phase','qr_ready','job_id','remaining')}, ensure_ascii=False)
        flow = '确认登录' if phase in {'verifying','success'} else '扫码确认' if phase == 'waiting_scan' else '生成二维码'
        body = f'''<p><a href="/">← 账号首页</a> · <a href="/log/{esc(username)}">账号详情</a> {admin_link}</p><p class="small">{esc(username)} / 云原神登录</p>
<h1>{esc(title)}</h1><div class="flow"><span class="{'current' if flow == '生成二维码' else ''}">1 生成二维码</span><span class="{'current' if flow == '扫码确认' else ''}">2 手机扫码确认</span><span class="{'current' if flow == '确认登录' else ''}">3 验证登录</span></div>
<div class="card" role="status"><p>{esc(message)}</p>{qr}<div class="row">{action}<a class="button secondary" href="/log/{esc(username)}">返回账号详情</a></div></div>
<p class="small">若每日签到因登录失效被自动暂停，登录成功后会自动恢复；手动停止的任务保持停止。</p>
<script>let cur={initial};let remaining=cur.remaining;const cd=document.getElementById('countdown');
setInterval(()=>{{if(cd && remaining!==null){{remaining=Math.max(0,remaining-1);cd.textContent=remaining;if(remaining===0) location.reload();}}}},1000);
const polling=setInterval(async()=>{{try{{const r=await fetch('/admin/relogin-status/{esc(username)}',{{cache:'no-store'}});if(r.status===303 || r.redirected){{location.href='/login';return;}}if(!r.ok)return;const n=await r.json();remaining=n.remaining;if(n.phase!==cur.phase || n.job_id!==cur.job_id || n.qr_ready!==cur.qr_ready){{location.reload();return;}}cur=n;}}catch(e){{}}}},2000);
if(!['preparing','waiting_scan','verifying'].includes(cur.phase))clearInterval(polling);
document.querySelectorAll('form').forEach(f=>f.addEventListener('submit',()=>{{const b=f.querySelector('button');b.disabled=true;b.textContent='正在生成…';}}));</script>'''
        self.send_html(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(username)} · 云原神登录</title>{STYLE}</head><body>{body}</body></html>')

    def job_page(self, username):
        ensure_user_record(username)
        st=job_status(username)
        d=ACCOUNTS/username; qr=d/'screenshots'/'qr.png'
        health=load_health(username); hs=health.get('status','unknown')
        health_text={'ok':'🟢 云·原神登录正常','login_expired':'🔴 云·原神登录失效','error':'🟡 最近一次执行异常'}.get(hs,'⚪ 尚无健康状态')
        invite=None
        db=connect(); row=db.execute('SELECT password_set FROM users WHERE username=?',(username,)).fetchone(); db.close()
        password_set=bool(row and row[0])
        if st['phase']=='success' and not password_set:
            invite=generate_invite(username)
        if st['phase']=='preparing': status_html='<h2 class="warn">正在生成二维码…</h2><p id="countdown">请稍候</p>'
        elif st['phase']=='waiting_scan': status_html='<h2 class="warn">等待扫码确认</h2><p>剩余：<strong id="countdown">—</strong></p>'
        elif st['phase']=='success': status_html='<h2 class="ok">✓ 云原神登录已验证</h2>'
        elif st['phase']=='verifying': status_html='<h2 class="warn">正在验证登录…</h2>'
        elif st['phase']=='failed': status_html=f'<h2 class="warn">最近一次扫码未完成</h2><p>{esc(st["message"] or "请打开扫码登录重新生成二维码。")}</p>'
        elif st['phase']=='expired': status_html='<h2 class="warn">需要重新扫码登录</h2>'
        else: status_html='<h2>云原神扫码登录</h2><p>用于首次绑定或恢复过期的云原神登录。</p>'
        status_html += f'<p><a class="button primary" href="/admin/relogin/{esc(username)}">打开扫码登录</a> <a class="button secondary" href="/log/{esc(username)}">查看账号详情</a></p>'
        qr_html=''
        if st['qr_ready']:
            qr_html=f'''<div class="card"><h2>二维码</h2><img class="qr" src="/admin/qr/{esc(username)}?job={esc(st['job_id'])}" alt="QR">
<p><a class="button" href="/admin/qr/{esc(username)}?download=1&job={esc(st['job_id'])}">下载 {esc(username)}-qr.png</a></p><p class="small">二维码只在任务状态变化时重新加载。</p></div>'''
        invite_html=''
        if password_set:
            invite_html='<div class="card"><h2 class="ok">网站账号已注册</h2><p>该用户已经设置密码。</p></div>'
        elif invite:
            exp=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(invite['expires']))
            invite_code = invite['code']
            register_link = f"{PUBLIC_URL}/register?invite={invite_code}"
            code_js = json.dumps(invite_code, ensure_ascii=False)
            link_js = json.dumps(register_link, ensure_ascii=False)
            invite_html=f'''<div class="card"><h2>邀请码</h2>
<div class="code" style="font-size:24px">{esc(invite_code)}</div>
<p class="small">邀请码有效至：{esc(exp)}</p>
<p><strong>邀请注册链接</strong></p><div class="code">{esc(register_link)}</div>
<div class="row" style="margin-top:12px">
<button type="button" onclick='copyValue({code_js}, this)'>复制邀请码</button>
<button type="button" onclick='copyValue({link_js}, this)'>复制邀请链接</button>
</div>
<p class="small">朋友打开邀请链接后邀请码会自动填好，只需要设置密码。</p>
<form method="post" action="/admin/invite/{esc(username)}" style="margin-top:12px"><button class="secondary" type="submit">重新生成邀请码</button></form></div>'''
        tstat=timer_status(username); nxt=next_run(username)
        timer_label={'enabled active':'已开启','disabled inactive':'已停止','enabled inactive':'已启用，等待启动'}.get(tstat,tstat)
        timer_html=f'''<div class="card"><h2>每日签到</h2><p>状态：<strong>{esc(timer_label)}</strong><br>下一次：<strong>{esc(nxt)}</strong></p><div class="row">
<form method="post" action="/admin/timer/{esc(username)}/enable"><button type="submit">开启每日签到</button></form>
<form method="post" action="/admin/timer/{esc(username)}/disable"><button class="secondary" type="submit">暂停每日签到</button></form>
<form method="post" action="/admin/timer/{esc(username)}/run"><button class="secondary" type="submit">检查登录并签到</button></form></div></div>'''
        health_html=f'<div class="card"><h2>{health_text}</h2></div>'
        danger='<div class="card"><h2>账号保护</h2><p class="small">此账号受删除保护，可以重新扫码登录和调整签到任务。</p></div>' if username == PROTECTED_ACCOUNT else f'''<details class="card"><summary>删除账号</summary><p>删除网站账号、云原神登录资料和日志。</p><a class="button danger" href="/admin/delete/{esc(username)}">删除用户</a></details>'''
        initial=json.dumps({'phase':st['phase'],'qr_ready':st['qr_ready'],'job_id':st['job_id'],'remaining':st['remaining']},ensure_ascii=False)
        copy_js='''<script>
async function copyValue(value, btn){
  const old=btn.textContent;
  try{
    await navigator.clipboard.writeText(value);
    btn.textContent='已复制 ✓';
  }catch(e){
    const t=document.createElement('textarea');
    t.value=value; t.style.position='fixed'; t.style.opacity='0';
    document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove();
    btn.textContent='已复制 ✓';
  }
  setTimeout(()=>btn.textContent=old,1400);
}
</script>'''
        js=copy_js+f'''<script>
const initial={initial}; let cur=initial; let remaining=initial.remaining; const cd=document.getElementById('countdown');
function draw(){{if(cd && remaining!==null) cd.textContent=Math.max(0,remaining)+' 秒';}}
draw(); setInterval(()=>{{if(remaining!==null && remaining>0){{remaining--;draw();}}}},1000);
async function poll(){{try{{const r=await fetch('/admin/api/job/{esc(username)}',{{cache:'no-store'}});const n=await r.json();remaining=n.remaining;
if(n.job_id!==cur.job_id || n.phase!==cur.phase || (!cur.qr_ready && n.qr_ready)){{location.reload();return;}}cur=n;draw();}}catch(e){{}}}}
setInterval(poll,2000);
</script>'''
        checking = parse_qs(urlparse(self.path).query).get('checking') == ['1']
        notice = '<div class="card notice" id="check-notice">检查已提交，正在等待签到结果…</div>' if checking else '<p class="small">设置已保存。</p>' if parse_qs(urlparse(self.path).query).get('saved') == ['1'] else ''
        if checking:
            updated = json.dumps(health.get('updated_at'))
            js += f'''<script>const started=Date.now();const healthPoll=setInterval(async()=>{{try{{const r=await fetch('/admin/api/health/{esc(username)}',{{cache:'no-store'}});if(!r.ok)return;const h=await r.json();if(h.updated_at!=={updated}){{location.replace('/admin/job/{esc(username)}');return;}}if(Date.now()-started>190000){{clearInterval(healthPoll);document.getElementById('check-notice').textContent='检查仍未返回结果，请查看运行日志或稍后刷新。';}}}}catch(e){{}}}},3000);</script>'''
        body=f'''<h1>{esc(username)} · 账号设置</h1>{notice}{health_html}<div class="card">{status_html}</div>{qr_html}{timer_html}{invite_html}{danger}{js}'''
        self.send_html(page(f'{username} - 用户管理',body))

    def send_qr(self, username, download):
        path=ACCOUNTS/username/'screenshots'/'qr.png'
        if not path.exists(): return self.send_error(404)
        data=path.read_bytes(); self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Cache-Control','no-store')
        if download: self.send_header('Content-Disposition',f'attachment; filename="{username}-qr.png"')
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)

    def delete_page(self, username):
        body=f'''<h1 class="err">删除用户：{esc(username)}</h1><div class="card"><p><strong>此操作不可恢复。</strong></p>
<p>将停止 Timer，并删除网站账户、Session、日志、Profile、二维码和截图。</p><form method="post" action="/admin/delete/{esc(username)}">
<label>请输入用户名确认：</label><input name="confirm" required><br><button class="danger" type="submit">永久删除</button></form></div>'''
        self.send_html(page('删除用户',body))

    def delete_user(self, username):
        if self.read_form().get('confirm','').strip()!=username:
            return self.send_html(page('确认失败','<h1 class="err">用户名确认不一致</h1>'),400)
        d=ACCOUNTS/username
        if pid_alive(d/'admin-qr-pid'):
            try: os.kill(int((d/'admin-qr-pid').read_text().strip()),15)
            except Exception: pass
        if d.exists() and TIMERCTL.exists():
            r=subprocess.run(['sudo','-n',str(TIMERCTL),'disable',username],capture_output=True,text=True,timeout=20)
            if r.returncode!=0:
                return self.send_html(page('删除失败',f'<h1 class="err">无法停止 Timer</h1><pre>{esc(r.stdout)}\n{esc(r.stderr)}</pre><p>数据尚未删除。</p>'),500)
        db=connect(); db.execute('DELETE FROM sessions WHERE username=?',(username,)); db.execute('DELETE FROM users WHERE username=?',(username,)); db.commit(); db.close()
        try: (LOG_DIR/f'{username}.log').unlink()
        except FileNotFoundError: pass
        if d.exists(): shutil.rmtree(d)
        self.send_html(page('删除完成',f'<h1 class="ok">✓ 已删除 {esc(username)}</h1><p><a class="button" href="/admin/">返回管理员后台</a></p>'))

    def log_message(self,fmt,*args):
        pass


if __name__=='__main__':
    DB.parent.mkdir(parents=True,exist_ok=True); connect().close()
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
