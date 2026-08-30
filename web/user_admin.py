#!/usr/bin/env python3
from pathlib import Path
import argparse, getpass, hashlib, json, secrets, sqlite3, time, os

BASE = Path(__file__).resolve().parent.parent
DB = BASE / 'web' / 'users.db'
ACCOUNTS = BASE / 'accounts'
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 6


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


def normalize(code):
    return ''.join(c for c in code.upper() if c.isalnum())


def code():
    s = ''.join(secrets.choice(ALPHABET) for _ in range(10))
    return s[:5] + '-' + s[5:]


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ITERATIONS)
    return salt, digest


def invite(username):
    db = connect()
    row = db.execute('SELECT password_set FROM users WHERE username=?', (username,)).fetchone()
    if not row:
        db.close(); raise SystemExit('用户不存在')
    if row[0]:
        db.close(); raise SystemExit('该用户已经设置密码；如需改密码请在网站内修改')
    c = code(); exp = int(time.time()) + 24 * 3600
    db.execute('UPDATE users SET setup_token_hash=?,setup_expires=? WHERE username=?',
               (hashlib.sha256(normalize(c).encode()).hexdigest(), exp, username))
    db.commit(); db.close()
    d = ACCOUNTS / username; d.mkdir(parents=True, exist_ok=True)
    f = d / 'admin-invite.json'
    f.write_text(json.dumps({'code': c, 'expires': exp}, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(f, 0o600)
    print('邀请码：', c)
    public_url = os.environ.get('CLOUD_GENSHIN_PUBLIC_URL', 'http://127.0.0.1:8001').rstrip('/')
    print('注册网址：' + public_url + '/register?invite=' + c)
    print('24 小时有效')


def list_users():
    db = connect(); rows = db.execute('SELECT username,role,password_set FROM users ORDER BY username').fetchall(); db.close()
    for u, r, p in rows:
        print(f'{u:20} {r:8} {"SET" if p else "NOT SET"}')


def create_admin(username):
    if not username or len(username) > 64 or not all(c.isalnum() or c in '_-' for c in username):
        raise SystemExit('用户名只能包含字母、数字、下划线和连字符')

    p1 = getpass.getpass(f'为管理员 {username} 设置密码（至少 {MIN_PASSWORD_LENGTH} 位）：')
    p2 = getpass.getpass('再次输入密码：')
    if len(p1) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f'密码至少 {MIN_PASSWORD_LENGTH} 位')
    if p1 != p2:
        raise SystemExit('两次密码不一致')

    salt, digest = hash_password(p1)
    db = connect()
    db.execute('''INSERT INTO users(username,password_salt,password_hash,role,created_at,password_set)
                  VALUES(?,?,?,?,?,1)
                  ON CONFLICT(username) DO UPDATE SET
                    password_salt=excluded.password_salt,
                    password_hash=excluded.password_hash,
                    role='admin',
                    password_set=1,
                    setup_token_hash=NULL,
                    setup_expires=NULL''',
               (username, salt, digest, 'admin', int(time.time())))
    db.execute('DELETE FROM sessions WHERE username=?', (username,))
    db.commit(); db.close()
    print(f'✅ 管理员 {username} 已创建/更新')


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest='cmd', required=True)
p = sub.add_parser('invite'); p.add_argument('username')
sub.add_parser('list')
p = sub.add_parser('create-admin'); p.add_argument('username')
a = parser.parse_args()
if a.cmd == 'invite':
    invite(a.username)
elif a.cmd == 'create-admin':
    create_admin(a.username)
else:
    list_users()
