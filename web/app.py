#!/usr/bin/env python3
"""Unified website and JSON API. Standard library only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs, urlencode
import hashlib
import json
import mimetypes
import re
import secrets
import sqlite3
import subprocess
import time

import services
import store
from login_guard import LoginGuard, source_ip

STATIC = Path(__file__).resolve().parent / 'static'
LOGIN_GUARD = LoginGuard()
SHELL = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#f5f7f6">
<title>云原神 · 每日签到</title><link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/assets/style.css"><script type="module" src="/assets/app.js"></script>
</head><body><div id="app"><div class="boot">正在打开云原神签到…</div></div>
<div id="notifications" aria-live="polite" aria-atomic="true"></div><div id="modal-root"></div>
<noscript>请启用 JavaScript 以使用网站。</noscript></body></html>'''


def auth_event(outcome, username, ip):
    print(json.dumps({'event':'login', 'outcome':outcome, 'source_ip':ip,
        'user_key':hashlib.sha256(username.encode()).hexdigest()[:16]}), flush=True)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def send(self, data, status=200, content_type='application/json; charset=utf-8', headers=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def redirect(self, target, status=303):
        self.send(b'', status, headers=[('Location', target)])

    def body(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise services.Problem('请求格式不正确。') from None
        if length < 0 or length > 16_384:
            raise services.Problem('提交内容过长。', 413)
        if self.headers.get_content_type() != 'application/json':
            raise services.Problem('请刷新页面后重试。', 415)
        try:
            result = json.loads(self.rfile.read(length).decode())
        except (UnicodeDecodeError, ValueError):
            raise services.Problem('请求格式不正确。') from None
        if not isinstance(result, dict):
            raise services.Problem('请求格式不正确。')
        return result

    def require_user(self):
        user = store.current_user(self.headers)
        if not user:
            raise services.Problem('网站登录已过期，请重新登录。', 401)
        return user

    def require_admin(self, user):
        if user['role'] != 'admin':
            raise services.Problem('此功能仅供管理员使用。', 403)

    def csrf(self):
        value = self.headers.get('X-CSRF-Token', '')
        expected = store.csrf_token(self.headers)
        if not value or not expected or not secrets.compare_digest(value, expected):
            raise services.Problem('页面已失效，请刷新后重试。', 403)

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def dispatch(self, method):
        try:
            path = urlparse(self.path).path
            if path.startswith('/admin/api/'):
                path = path[len('/admin'):]
            if path.startswith('/api/'):
                return self.api(method, path)
            if method != 'GET':
                raise services.Problem('操作入口已更新，请刷新页面。', 405)
            if path.startswith('/assets/'):
                name = path[len('/assets/'):]
                if name not in {'app.js','api.js','ui.js','pages.js','style.css','favicon.svg'}:
                    raise services.Problem('文件不存在。', 404)
                file = STATIC / name
                return self.send(file.read_bytes(), content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream')
            if path.startswith('/admin/') or path == '/admin':
                match = re.fullmatch(r'/admin/(?:job|relogin)/([A-Za-z0-9_-]{1,64})', path)
                target = '/accounts/' + match.group(1) + ('/login' if '/relogin/' in path else '') if match else '/manage'
                return self.redirect(target)
            if path.startswith('/log/'):
                username = path[len('/log/'):]
                return self.redirect('/accounts/' + username) if store.valid_username(username) else self.redirect('/')
            if path.startswith('/screenshot/'):
                username = path[len('/screenshot/'):]
                user = self.require_user(); services.authorize(user, username)
                return self.image(username, 'screenshot')
            if path in {'/password','/logout'}:
                return self.redirect('/profile')
            # IPv6 certificate maintenance verifies this legacy URL returns HTTP 200.
            if path in {'/','/login','/signin','/register','/profile','/manage','/network'} or re.fullmatch(r'/accounts/[A-Za-z0-9_-]{1,64}(?:/login)?', path):
                return self.send(SHELL, content_type='text/html; charset=utf-8')
            raise services.Problem('页面不存在。', 404)
        except services.Problem as error:
            self.send({'error':error.message}, error.status)
        except (OSError, sqlite3.Error, subprocess.TimeoutExpired):
            self.send({'error':'服务暂时无法响应，请稍后重试。'}, 503)
        except Exception as error:
            print(json.dumps({'event':'server_error','type':type(error).__name__}), flush=True)
            self.send({'error':'操作未完成，请稍后重试。'}, 500)

    def api(self, method, path):
        if method == 'POST' and path == '/api/login' and self.server.server_port == 8003:
            # Both public prefixes must use the same process and failure counters.
            return self.redirect('/api/login', 307)
        if method == 'GET' and path == '/api/session':
            user = store.current_user(self.headers)
            return self.send({'user':user, 'csrf':store.csrf_token(self.headers) if user else None})
        if method == 'POST' and path == '/api/login':
            return self.login(self.body())
        if method == 'POST' and path == '/api/invites/resolve':
            code = self.body().get('invite', '')
            username = store.invite_user(code) if isinstance(code,str) else None
            if not username:
                raise services.Problem('邀请链接已失效或已使用，请联系管理员重新发送。', 400)
            return self.send({'username':username})
        if method == 'POST' and path == '/api/register':
            return self.register(self.body())
        user = self.require_user()
        if method == 'POST':
            self.csrf()
        if method == 'POST' and path == '/api/logout':
            return self.send({'ok':True}, headers=[('Set-Cookie', store.end_session(self.headers))])
        if method == 'POST' and path == '/api/password':
            return self.change_password(user, self.body())
        # Privileged helpers run in the existing admin service; its sudo policy is already configured.
        if method == 'POST' and self.server.server_port == 8001 and path.startswith(('/api/accounts/','/api/users','/api/network')):
            return self.redirect('/admin' + path, 307)
        if method == 'GET' and path == '/api/accounts':
            rows = [services.account(name) for name in services.account_names(user)]
            rows.sort(key=lambda row:(row['status'] != 'login_expired',row['username']))
            return self.send({'accounts':rows})
        match = re.fullmatch(r'/api/accounts/([A-Za-z0-9_-]{1,64})(?:/(login(?:/qr)?|task|logs|screenshot))?', path)
        if match:
            username, action = match.groups()
            services.authorize(user, username)
            if method == 'GET':
                if action is None: return self.send(services.account(username, True))
                if action == 'login': return self.send(services.login_job(username))
                if action == 'logs': return self.send({'lines':services.log_lines(username)})
                if action in {'screenshot','login/qr'}: return self.image(username, action)
            if method == 'POST':
                if action == 'login': self.body(); return self.send(services.start_login(username),202)
                if action == 'task': return self.send(services.task_action(username,self.body().get('action')))
        if path == '/api/users':
            self.require_admin(user)
            if method == 'GET': return self.send({'users':services.users(),'public_url':services.config()['public_url']})
            if method == 'POST': return self.send(services.create_user(self.body().get('username')),201)
        match = re.fullmatch(r'/api/users/([A-Za-z0-9_-]{1,64})/(invite|delete)', path)
        if method == 'POST' and match:
            self.require_admin(user)
            username, action = match.groups(); services.authorize(user, username)
            data = self.body()
            if action == 'invite': return self.send(services.invite(username))
            if data.get('confirm') != username:
                raise services.Problem('请输入完整账号名确认删除。')
            return self.send(services.delete_user(username,user['username']))
        if path == '/api/network':
            self.require_admin(user)
            if method == 'GET':
                return self.send({'public_url':services.config()['public_url'],
                    'ipv6':services.read_json(services.BASE/'web/ipv6_status.json'),
                    'update':services.read_json(services.BASE/'web/ipv6_update_status.json')})
            if method == 'POST': self.body(); return self.send(services.start_network_update(user['username']),202)
        raise services.Problem('接口不存在。',404)

    def login(self, data):
        username = data.get('username',''); password = data.get('password','')
        if not isinstance(username,str) or not isinstance(password,str) or len(username)>256 or len(password)>1024:
            raise services.Problem('用户名或密码格式不正确。')
        username = username.strip()
        ip = source_ip(self.client_address[0],self.headers)
        reservation, retry = LOGIN_GUARD.admit(username,ip)
        if reservation is None:
            return self.send({'error':'登录尝试过多，请稍后再试。','retry_after':retry},429,headers=[('Retry-After',str(retry))])
        success = False
        try:
            with store.database() as db:
                row = db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone() if store.valid_username(username) else None
            success = store.verify_password(password,row)
        finally:
            retry = LOGIN_GUARD.finish(reservation,success)
        auth_event('success' if success else 'cooldown' if retry else 'failure',username,ip)
        if not success:
            return self.send({'error':'登录尝试过多，请稍后再试。' if retry else '用户名或密码不正确。', 'retry_after':retry},429 if retry else 400,
                headers=[('Retry-After',str(retry))] if retry else None)
        return self.send({'ok':True},headers=[('Set-Cookie',store.new_session(username))])

    def register(self, data):
        password = self.valid_password(data)
        code = data.get('invite','')
        if not isinstance(code,str): raise services.Problem('邀请链接无效。')
        salt, digest = store.password_hash(password)
        with store.database() as db:
            invited = db.execute('SELECT username FROM users WHERE setup_token_hash=?', (store.invite_digest(code),)).fetchone()
            result = db.execute('''UPDATE users SET password_salt=?,password_hash=?,password_set=1,
                setup_token_hash=NULL,setup_expires=NULL WHERE setup_token_hash=? AND setup_expires>?
                AND password_set=0''',(salt,digest,store.invite_digest(code),int(time.time())))
            if result.rowcount != 1:
                raise services.Problem('邀请链接已失效或已使用。')
        return self.send({'ok':True},headers=[('Set-Cookie',store.new_session(invited['username']))])

    def valid_password(self, data):
        password = data.get('password','')
        if not isinstance(password,str) or not 8 <= len(password) <= 1024:
            raise services.Problem('新密码需为 8–1024 位。')
        if password != data.get('confirm'):
            raise services.Problem('两次输入的密码不一致。')
        return password

    def change_password(self, user, data):
        password = self.valid_password(data)
        current = data.get('current','')
        if not isinstance(current,str) or len(current)>1024: raise services.Problem('当前密码不正确。')
        with store.database() as db:
            row = db.execute('SELECT * FROM users WHERE username=?',(user['username'],)).fetchone()
            if not store.verify_password(current,row): raise services.Problem('当前网站密码不正确。')
            salt,digest = store.password_hash(password)
            db.execute('UPDATE users SET password_salt=?,password_hash=? WHERE username=?',(salt,digest,user['username']))
            db.execute('DELETE FROM sessions WHERE username=?',(user['username'],))
        return self.send({'ok':True},headers=[('Set-Cookie',store.end_session(self.headers))])

    def image(self, username, action):
        directory = services.ACCOUNTS/username/'screenshots'
        if action == 'login/qr':
            job = services.login_job(username)
            requested = parse_qs(urlparse(self.path).query).get('job',[''])[0]
            if not job['qr_ready'] or requested != job['job_id']:
                raise services.Problem('二维码已过期，请重新生成。',410)
            path = directory/'qr.png'
        else:
            path = directory/'last-page.jpg'
            if not path.exists(): path = directory/'last-page.png'
        if not path.exists(): raise services.Problem('暂时没有图片。',404)
        headers = [('Content-Disposition',f'attachment; filename="{username}-qr.png"')] if action == 'login/qr' and 'download' in parse_qs(urlparse(self.path).query) else None
        return self.send(path.read_bytes(),content_type='image/jpeg' if path.suffix=='.jpg' else 'image/png',headers=headers)

    def log_message(self, *args):
        pass


def run(port=8001):
    store.init()
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()


if __name__ == '__main__':
    run()
