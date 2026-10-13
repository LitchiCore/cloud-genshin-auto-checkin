"""Website accounts and sessions. Compatible with the original SQLite schema."""
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time

BASE = Path(os.environ.get('CLOUD_GENSHIN_HOME', Path(__file__).resolve().parent.parent))
DB = BASE / 'web/users.db'
SESSION_SECONDS = 30 * 86400
ITERATIONS = 600_000
USERNAME = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
DUMMY_SALT = secrets.token_bytes(32)
DUMMY_HASH = hashlib.pbkdf2_hmac('sha256', secrets.token_bytes(32), DUMMY_SALT, ITERATIONS)


def init():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY, password_salt BLOB, password_hash BLOB,
            role TEXT NOT NULL DEFAULT 'user', setup_token_hash TEXT, setup_expires INTEGER,
            created_at INTEGER NOT NULL, password_set INTEGER NOT NULL DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS sessions(
            token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires INTEGER NOT NULL)''')


@contextmanager
def database():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def valid_username(username):
    return isinstance(username, str) and bool(USERNAME.fullmatch(username))


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(32)
    return salt, hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)


def verify_password(password, row):
    usable = bool(row and row['password_set'] and row['password_salt'] and row['password_hash'])
    salt = row['password_salt'] if usable else DUMMY_SALT
    expected = row['password_hash'] if usable else DUMMY_HASH
    matches = hmac.compare_digest(password_hash(password, salt)[1], expected)
    return usable and matches


def cookie_token(headers):
    try:
        cookie = SimpleCookie()
        cookie.load(headers.get('Cookie', ''))
        return cookie['cg_session'].value if 'cg_session' in cookie else ''
    except Exception:
        return ''


def current_user(headers):
    token = cookie_token(headers)
    if not token or len(token) > 256:
        return None
    with database() as db:
        row = db.execute('''SELECT u.username,u.role FROM sessions s JOIN users u
            ON u.username=s.username WHERE s.token_hash=? AND s.expires>?''',
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
    return dict(row) if row else None


def csrf_token(headers):
    token = cookie_token(headers)
    return hashlib.sha256(('cloud-genshin-csrf:' + token).encode()).hexdigest() if token else ''


def new_session(username):
    token = secrets.token_urlsafe(48)
    with database() as db:
        db.execute('DELETE FROM sessions WHERE expires<=?', (int(time.time()),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), username, int(time.time()) + SESSION_SECONDS))
    return f'cg_session={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; Secure; SameSite=Lax'


def end_session(headers):
    digest = hashlib.sha256(cookie_token(headers).encode()).hexdigest()
    with database() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))
    return 'cg_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax'


def invite_digest(code):
    normalized = ''.join(c for c in code.upper() if c.isalnum())
    return hashlib.sha256(normalized.encode()).hexdigest()


def invite_user(code):
    with database() as db:
        row = db.execute('''SELECT username FROM users WHERE setup_token_hash=?
            AND setup_expires>? AND password_set=0''', (invite_digest(code), int(time.time()))).fetchone()
    return row['username'] if row else None
