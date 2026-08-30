#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import hashlib, hmac, html, json, secrets, sqlite3, subprocess, time

BASE = Path(__file__).resolve().parent.parent
LOG_DIR = BASE / 'logs'
ACCOUNTS = BASE / 'accounts'
DB = BASE / 'web' / 'users.db'
HOST, PORT = '127.0.0.1', 8001
SESSION_SECONDS = 30 * 24 * 3600
PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 6


def esc(v):
    return html.escape(str(v), quote=True)


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


def valid_username(v):
    return bool(v) and len(v) <= 64 and all(c.isalnum() or c in '_-' for c in v)


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ITERATIONS)
    return salt, digest


def verify_password(password, salt, expected):
    return hmac.compare_digest(hash_password(password, salt)[1], expected)


def normalize_invite(code):
    return ''.join(c for c in code.upper() if c.isalnum())


def read_lines(path, limit=500):
    try:
        return path.read_text(encoding='utf-8', errors='replace').splitlines()[-limit:]
    except Exception as e:
        return [f'读取日志失败：{e}']


def last_line(path):
    for line in reversed(read_lines(path, 100)):
        if line.strip():
            return line
    return '暂无记录'


def next_run_time(account):
    unit = f'cloud-genshin@{account}.timer'
    try:
        if subprocess.run(['systemctl', 'is-enabled', '--quiet', unit], timeout=3).returncode != 0:
            return '未启用'
        r = subprocess.run(
            ['systemctl', 'show', unit, '-p', 'NextElapseUSecRealtime', '--value'],
            capture_output=True, text=True, timeout=3
        )
        return r.stdout.strip() or '等待调度'
    except Exception:
        return '未知'


def load_health(account):
    try:
        return json.loads((ACCOUNTS / account / 'health.json').read_text(encoding='utf-8'))
    except Exception:
        return {'status': 'unknown', 'consecutive_login_expired': 0}


STYLE = '''
<style>
:root{color-scheme:dark}body{max-width:900px;margin:32px auto;padding:0 18px;background:#111;color:#eee;font-family:system-ui,-apple-system,sans-serif}
a{color:#8ab4f8;text-decoration:none}.card{background:#1b1b1b;border:1px solid #333;border-radius:12px;padding:16px;margin:14px 0}
.account{font-size:21px;font-weight:650}.entry,.log{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;word-break:break-word}
.log{white-space:pre-wrap;background:#080808;border:1px solid #333;border-radius:10px;padding:16px;line-height:1.7}
.small{opacity:.65;font-size:13px}.top{display:flex;justify-content:space-between;gap:12px;margin-bottom:22px}
form{max-width:420px}input{width:100%;box-sizing:border-box;padding:11px;margin:6px 0 14px;border-radius:8px;border:1px solid #444;background:#181818;color:#eee;font-size:16px}
button,.button{display:inline-block;padding:10px 18px;border:0;border-radius:8px;cursor:pointer;font-size:15px}.error{color:#ff8c8c}.ok{color:#9be29b}.warn{color:#ffd27a}
.shot{display:block;max-width:100%;height:auto;border:1px solid #333;border-radius:10px;margin-top:12px}
</style>
'''


def make_page(title, body, user=None):
    top = ''
    if user:
        admin = ' · <a href="/admin/">管理后台</a>' if user['role'] == 'admin' else ''
        top = f'''<div class="top"><div class="small">登录：{esc(user['username'])} · {esc(user['role'])}{admin}</div>
<div class="small"><a href="/password">修改密码</a> · <a href="/logout">退出</a></div></div>'''
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>{STYLE}</head><body>{top}{body}</body></html>'''


class Handler(BaseHTTPRequestHandler):
    def security_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")

    def send_html(self, content, status=200, headers=None):
        data = content.encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.security_headers()
        if headers:
            for k, v in headers:
                self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, target, headers=None):
        self.send_response(303)
        self.send_header('Location', target)
        self.security_headers()
        if headers:
            for k, v in headers:
                self.send_header(k, v)
        self.end_headers()

    def read_form(self):
        try:
            length = min(int(self.headers.get('Content-Length', '0')), 16384)
        except ValueError:
            return {}
        parsed = parse_qs(self.rfile.read(length).decode('utf-8', errors='replace'))
        return {k: v[0] for k, v in parsed.items() if v}

    def session_token(self):
        try:
            c = SimpleCookie()
            c.load(self.headers.get('Cookie', ''))
            return c['cg_session'].value if 'cg_session' in c else None
        except Exception:
            return None

    def current_user(self):
        token = self.session_token()
        if not token:
            return None
        th = hashlib.sha256(token.encode()).hexdigest()
        db = connect()
        row = db.execute('''SELECT u.username,u.role,s.expires FROM sessions s
                            JOIN users u ON u.username=s.username WHERE s.token_hash=?''', (th,)).fetchone()
        if not row:
            db.close()
            return None
        username, role, expires = row
        if expires < int(time.time()):
            db.execute('DELETE FROM sessions WHERE token_hash=?', (th,))
            db.commit()
            db.close()
            return None
        db.close()
        return {'username': username, 'role': role}

    def create_session(self, username):
        token = secrets.token_urlsafe(48)
        th = hashlib.sha256(token.encode()).hexdigest()
        exp = int(time.time()) + SESSION_SECONDS
        db = connect()
        db.execute('DELETE FROM sessions WHERE expires < ?', (int(time.time()),))
        db.execute('INSERT INTO sessions(token_hash,username,expires) VALUES(?,?,?)', (th, username, exp))
        db.commit()
        db.close()
        return ('Set-Cookie', f'cg_session={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; Secure; SameSite=Lax')

    def clear_session(self):
        token = self.session_token()
        if token:
            db = connect()
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
            db.commit()
            db.close()
        return ('Set-Cookie', 'cg_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax')

    def require_user(self):
        u = self.current_user()
        if not u:
            self.redirect('/login')
        return u

    def authorized_account(self, user, account):
        return user['role'] == 'admin' or user['username'] == account

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == '/login':
            return self.redirect('/') if self.current_user() else self.login_page()
        if p.path == '/register':
            invite = parse_qs(p.query).get('invite', [''])[0]
            return self.register_page(invite=invite)
        if p.path == '/setup':
            return self.send_html(make_page('旧链接已停用', '<h1>旧设置链接已停用</h1><p>请向管理员索取邀请码，然后使用<a href="/register">邀请码注册</a>。</p>'), 410)
        if p.path == '/logout':
            return self.redirect('/login', [self.clear_session()])
        user = self.require_user()
        if not user:
            return
        if p.path == '/':
            return self.index(user)
        if p.path == '/password':
            return self.password_page(user)
        if p.path.startswith('/log/'):
            account = unquote(p.path[len('/log/'):])
            if not valid_username(account):
                return self.send_error(400)
            if not self.authorized_account(user, account):
                return self.send_html(make_page('403', '<h1>403 Forbidden</h1>', user), 403)
            return self.show_log(user, account)
        if p.path.startswith('/screenshot/'):
            account = unquote(p.path[len('/screenshot/'):])
            if not valid_username(account):
                return self.send_error(400)
            if not self.authorized_account(user, account):
                return self.send_error(403)
            return self.send_screenshot(account)
        self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == '/login':
            return self.login_submit()
        if p.path == '/register':
            return self.register_submit()
        user = self.require_user()
        if not user:
            return
        if p.path == '/password':
            return self.password_submit(user)
        self.send_error(404)

    def login_page(self, error=None):
        e = f'<p class="error">{esc(error)}</p>' if error else ''
        body = f'''<h1>云·原神日志</h1><p class="small">请登录</p>{e}
<form method="post" action="/login"><label>用户名</label><input name="username" autocomplete="username" required>
<label>密码</label><input name="password" type="password" autocomplete="current-password" required>
<button type="submit">登录</button></form><p><a href="/register">使用邀请码注册 / 设置密码</a></p>'''
        self.send_html(make_page('登录', body))

    def login_submit(self):
        f = self.read_form()
        username = f.get('username', '').strip()
        password = f.get('password', '')
        if not valid_username(username):
            return self.login_page('用户名或密码错误')
        db = connect()
        row = db.execute('SELECT password_salt,password_hash,password_set FROM users WHERE username=?', (username,)).fetchone()
        db.close()
        if not row or not row[2]:
            time.sleep(.4)
            return self.login_page('用户名或密码错误')
        if not verify_password(password, row[0], row[1]):
            time.sleep(.5)
            return self.login_page('用户名或密码错误')
        self.redirect('/', [self.create_session(username)])

    def register_page(self, error=None, invite=''):
        e = f'<p class="error">{esc(error)}</p>' if error else ''
        body = f'''<h1>邀请码注册</h1><p class="small">邀请码由管理员生成；邀请码已经绑定到对应的云·原神账号，不需要自己填写用户名。</p>{e}
<form method="post" action="/register"><label>邀请码</label><input name="invite" value="{esc(invite)}" autocomplete="one-time-code" required>
<label>设置密码（至少 {MIN_PASSWORD_LENGTH} 位）</label><input name="password" type="password" minlength="{MIN_PASSWORD_LENGTH}" autocomplete="new-password" required>
<label>再次输入密码</label><input name="confirm" type="password" minlength="{MIN_PASSWORD_LENGTH}" autocomplete="new-password" required>
<button type="submit">注册</button></form><p><a href="/login">返回登录</a></p>'''
        self.send_html(make_page('邀请码注册', body, self.current_user()))

    def register_submit(self):
        f = self.read_form()
        code = normalize_invite(f.get('invite', ''))
        password = f.get('password', '')
        confirm = f.get('confirm', '')
        if len(password) < MIN_PASSWORD_LENGTH:
            return self.register_page(f'密码至少 {MIN_PASSWORD_LENGTH} 位', code)
        if password != confirm:
            return self.register_page('两次密码不一致', code)
        if len(code) < 6:
            return self.register_page('邀请码无效', code)
        ih = hashlib.sha256(code.encode()).hexdigest()
        db = connect()
        row = db.execute('SELECT username,setup_expires,password_set FROM users WHERE setup_token_hash=?', (ih,)).fetchone()
        if not row:
            db.close()
            return self.register_page('邀请码无效或已经使用', code)
        username, exp, password_set = row
        if password_set or not exp or exp < int(time.time()):
            db.close()
            return self.register_page('邀请码已失效或过期', code)
        salt, digest = hash_password(password)
        db.execute('''UPDATE users SET password_salt=?,password_hash=?,password_set=1,
                      setup_token_hash=NULL,setup_expires=NULL WHERE username=?''', (salt, digest, username))
        db.execute('DELETE FROM sessions WHERE username=?', (username,))
        db.commit()
        db.close()
        body = f'''<h1 class="ok">注册完成</h1><p>用户名：<strong>{esc(username)}</strong></p>
<p>密码已经设置。此页面不会自动切换成该用户。</p><p><a class="button" href="/login">前往登录</a></p>'''
        self.send_html(make_page('注册完成', body, self.current_user()))

    def password_page(self, user, error=None):
        e = f'<p class="error">{esc(error)}</p>' if error else ''
        body = f'''<h1>修改密码</h1>{e}<form method="post" action="/password">
<label>当前密码</label><input name="current" type="password" required>
<label>新密码（至少 {MIN_PASSWORD_LENGTH} 位）</label><input name="password" type="password" minlength="{MIN_PASSWORD_LENGTH}" required>
<label>再次输入</label><input name="confirm" type="password" minlength="{MIN_PASSWORD_LENGTH}" required>
<button type="submit">修改密码</button></form>'''
        self.send_html(make_page('修改密码', body, user))

    def password_submit(self, user):
        f = self.read_form()
        current = f.get('current', '')
        password = f.get('password', '')
        confirm = f.get('confirm', '')
        if len(password) < MIN_PASSWORD_LENGTH:
            return self.password_page(user, f'密码至少 {MIN_PASSWORD_LENGTH} 位')
        if password != confirm:
            return self.password_page(user, '两次密码不一致')
        db = connect()
        row = db.execute('SELECT password_salt,password_hash FROM users WHERE username=?', (user['username'],)).fetchone()
        if not row or not verify_password(current, row[0], row[1]):
            db.close()
            return self.password_page(user, '当前密码错误')
        salt, digest = hash_password(password)
        db.execute('UPDATE users SET password_salt=?,password_hash=? WHERE username=?', (salt, digest, user['username']))
        db.execute('DELETE FROM sessions WHERE username=?', (user['username'],))
        db.commit()
        db.close()
        self.redirect('/login', [self.clear_session()])

    def index(self, user):
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        if user["role"] == "admin":
            names = set()

            # 网站账户
            try:
                db = connect()
                rows = db.execute(
                    "SELECT username FROM users"
                ).fetchall()
                db.close()

                names.update(
                    row[0]
                    for row in rows
                    if valid_username(row[0])
                )
            except Exception:
                pass

            # 云原神账号目录
            if ACCOUNTS.exists():
                names.update(
                    path.name
                    for path in ACCOUNTS.iterdir()
                    if path.is_dir()
                    and valid_username(path.name)
                )

            # 已经存在的日志
            names.update(
                path.stem
                for path in LOG_DIR.glob("*.log")
                if valid_username(path.stem)
            )

            accounts = sorted(names)

        else:
            accounts = [
                user["username"]
            ]

        cards = []

        for account in accounts:
            path = LOG_DIR / f"{account}.log"

            if path.exists():
                last = last_line(path)
            else:
                last = "暂无运行日志"

            nxt = next_run_time(account)

            health = load_health(account)

            hs = health.get(
                "status",
                "unknown"
            )

            badge = {
                "ok": "🟢 登录正常",
                "login_expired": "🔴 登录失效",
                "error": "🟡 最近执行异常",
            }.get(
                hs,
                "⚪ 尚无健康状态"
            )

            actions = (
                f'<p>'
                f'<a href="/log/{esc(account)}">'
                f'查看日志'
                f'</a>'
            )

            if user["role"] == "admin":
                actions += (
                    f' · '
                    f'<a href="/admin/job/{esc(account)}">'
                    f'管理用户'
                    f'</a>'
                )

            actions += '</p>'

            cards.append(
                f"""
<div class="card">

<div class="account">
{esc(account)}
</div>

<p>
{badge}
</p>

<div class="entry">
{esc(last)}
</div>

<p class="small">
下一次执行：
{esc(nxt)}
</p>

{actions}

</div>
"""
            )

        admin_actions = (
            """
<p>
<a href="/admin/">⚙ 管理后台</a>
&nbsp; · &nbsp;
<a href="/admin/#create">＋ 添加新用户</a>
</p>
"""
            if user["role"] == "admin"
            else ""
        )

        self.send_html(
            make_page(
                "云·原神日志",
                f"""
<h1>云·原神运行日志</h1>

{admin_actions}

{''.join(cards) or '<div class="card">暂无用户</div>'}
""",
                user
            )
        )


    def show_log(self, user, account):
        path = LOG_DIR / f'{account}.log'
        health = load_health(account)
        hs = health.get('status', 'unknown')
        badge = {
            'ok': '🟢 云·原神登录正常',
            'login_expired': '🔴 云·原神登录已失效',
            'error': '🟡 最近一次执行异常',
        }.get(hs, '⚪ 尚无登录健康状态')

        shot_dir = ACCOUNTS / account / 'screenshots'
        jpg = shot_dir / 'last-page.jpg'
        png = shot_dir / 'last-page.png'
        shot = jpg if jpg.exists() else png if png.exists() else None

        shot_html = ''
        if shot:
            m = int(shot.stat().st_mtime)
            size_kb = max(1, round(shot.stat().st_size / 1024))
            kind = 'JPEG' if shot.suffix.lower() == '.jpg' else 'PNG'
            shot_html = f'''<div class="card"><strong>上一次完整页面截图</strong>
<p class="small">{kind} · 约 {size_kb} KB · 点击图片查看原图</p>
<a href="/screenshot/{esc(account)}?v={m}" target="_blank"><img class="shot" loading="eager" decoding="async" fetchpriority="high" src="/screenshot/{esc(account)}?v={m}" alt="上一次页面截图"></a></div>'''

        # 详情页按“最新 -> 最旧”排列。
        if path.exists():
            recent_lines = list(
                reversed(
                    read_lines(path)
                )
            )

            logs_html = esc(
                chr(10).join(
                    recent_lines
                )
            )

        else:
            logs_html = "暂无运行日志"

        body = f'''<p><a href="/">← 返回</a></p><h1>{esc(account)}</h1>
<div class="card"><strong>{badge}</strong><br><br>下一次执行：<strong>{esc(next_run_time(account))}</strong></div>
{shot_html}
<p class="small">最近 500 条记录 · 最新在上</p><div class="log">{logs_html}</div>'''
        self.send_html(make_page(f'{account} - 云·原神日志', body, user))

    def send_screenshot(self, account):
        shot_dir = ACCOUNTS / account / 'screenshots'
        jpg = shot_dir / 'last-page.jpg'
        png = shot_dir / 'last-page.png'
        path = jpg if jpg.exists() else png if png.exists() else None
        if not path:
            return self.send_error(404)

        data = path.read_bytes()
        content_type = 'image/jpeg' if path.suffix.lower() == '.jpg' else 'image/png'

        self.send_response(200)
        self.send_header('Content-Type', content_type)
        # URL 带 ?v=<mtime>，文件更新时 URL 会变化；旧图可以放心让浏览器长期缓存。
        self.send_header('Cache-Control', 'private, max-age=31536000, immutable')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    DB.parent.mkdir(parents=True, exist_ok=True)
    connect().close()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
