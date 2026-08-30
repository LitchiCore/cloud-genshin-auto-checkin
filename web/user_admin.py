#!/usr/bin/env python3
from pathlib import Path
import argparse, hashlib, json, secrets, sqlite3, time, os

BASE=Path(__file__).resolve().parent.parent
DB=BASE/'web'/'users.db'
ACCOUNTS=BASE/'accounts'
ALPHABET='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def connect():
    return sqlite3.connect(DB)


def normalize(code):
    return ''.join(c for c in code.upper() if c.isalnum())


def code():
    s=''.join(secrets.choice(ALPHABET) for _ in range(10))
    return s[:5]+'-'+s[5:]


def invite(username):
    db=connect(); row=db.execute('SELECT password_set FROM users WHERE username=?',(username,)).fetchone()
    if not row:
        db.close(); raise SystemExit('用户不存在')
    if row[0]:
        db.close(); raise SystemExit('该用户已经设置密码；如需改密码请在网站内修改')
    c=code(); exp=int(time.time())+24*3600
    db.execute('UPDATE users SET setup_token_hash=?,setup_expires=? WHERE username=?',(hashlib.sha256(normalize(c).encode()).hexdigest(),exp,username))
    db.commit(); db.close()
    d=ACCOUNTS/username; d.mkdir(parents=True,exist_ok=True)
    f=d/'admin-invite.json'; f.write_text(json.dumps({'code':c,'expires':exp},ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(f,0o600)
    print('邀请码：',c)
    public_url=os.environ.get('CLOUD_GENSHIN_PUBLIC_URL','http://127.0.0.1:8001').rstrip('/')
    print('注册网址：'+public_url+'/register')
    print('24 小时有效')


def list_users():
    db=connect(); rows=db.execute('SELECT username,role,password_set FROM users ORDER BY username').fetchall(); db.close()
    for u,r,p in rows: print(f'{u:20} {r:8} {"SET" if p else "NOT SET"}')

parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='cmd',required=True)
p=sub.add_parser('invite'); p.add_argument('username')
sub.add_parser('list')
a=parser.parse_args()
if a.cmd=='invite': invite(a.username)
else: list_users()
