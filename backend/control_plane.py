"""Local acceptance account/device/entitlement service; no payment or exchange secrets.

Run: python control_plane.py --bootstrap (interactive admin creation), then
python control_plane.py. Public deployment requires TLS/reverse proxy and review.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import getpass
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, SecretStr
from app.database import ManagedConnection


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[a-zA-Z0-9_.@-]+$')
    password: SecretStr = Field(min_length=12, max_length=128)


class Device(BaseModel):
    device_id: str = Field(min_length=16, max_length=128)


class Grant(BaseModel):
    days: int = Field(ge=0, le=3660)
    device_limit: int = Field(default=2, ge=1, le=20)
    disabled: bool = False


def create_app(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    def database():
        conn = sqlite3.connect(path, timeout=15, factory=ManagedConnection)
        conn.row_factory = sqlite3.Row
        return conn
    with database() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, salt TEXT NOT NULL, password TEXT NOT NULL,
                admin INTEGER NOT NULL DEFAULT 0, expires REAL NOT NULL, device_limit INTEGER NOT NULL DEFAULT 2,
                disabled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS devices(username TEXT NOT NULL, device TEXT NOT NULL, seen REAL NOT NULL,
                PRIMARY KEY(username,device));
            CREATE TABLE IF NOT EXISTS admin_audit(id INTEGER PRIMARY KEY, actor TEXT, target TEXT, action TEXT, timestamp REAL);
        ''')
    app = FastAPI(title='Copier Local Account Service', docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    @app.exception_handler(RequestValidationError)
    async def invalid(_, exc):
        return JSONResponse(status_code=422, content={'detail':'账号或密码格式不符合要求'})
    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response
    attempts = defaultdict(deque)

    def limited(request):
        key = request.client.host if request.client else 'local'
        now = time.monotonic()
        queue = attempts[key]
        while queue and queue[0] < now-60:
            queue.popleft()
        if len(queue) >= 15:
            raise HTTPException(429, '登录请求过多，请稍后重试')
        queue.append(now)

    def user_for(authorization, admin=False):
        token = authorization.removeprefix('Bearer ') if authorization else ''
        with database() as conn:
            user = conn.execute('SELECT u.* FROM users u JOIN sessions s ON u.username=s.username WHERE s.token=? AND s.expires>?', (digest(token), time.time())).fetchone()
        if not user or user['disabled']:
            raise HTTPException(401, '登录已失效')
        if admin and not user['admin']:
            raise HTTPException(403, '需要管理员权限')
        return user

    def create_user(credentials, admin=False):
        salt = secrets.token_hex(16)
        hashed = password_hash(credentials.password.get_secret_value(), salt)
        try:
            with database() as conn:
                conn.execute('INSERT INTO users(username,salt,password,admin,expires) VALUES (?,?,?,?,?)',
                             (credentials.username.lower(), salt, hashed, int(admin), time.time()+7*86400))
        except sqlite3.IntegrityError:
            raise HTTPException(409, '该账号已存在')

    app.state.create_user = create_user

    @app.post('/register')
    def register(body: Credentials, request: Request):
        limited(request)
        create_user(body)
        return {'detail': '账号已创建，试用 7 天，请登录'}

    @app.post('/login')
    def login(body: Credentials, request: Request):
        limited(request)
        with database() as conn:
            user = conn.execute('SELECT * FROM users WHERE username=?', (body.username.lower(),)).fetchone()
            expected = password_hash(body.password.get_secret_value(), user['salt'] if user else '00'*16)
            if not user or user['disabled'] or not hmac.compare_digest(expected, user['password']):
                raise HTTPException(401, '账号或密码错误')
            token = secrets.token_urlsafe(32)
            conn.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            conn.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), user['username'], time.time()+7*86400))
        return {'token': token, 'admin': bool(user['admin']), 'username': user['username']}

    @app.post('/logout')
    def logout(authorization: str = Header(default='')):
        with database() as conn:
            conn.execute('DELETE FROM sessions WHERE token=?', (digest(authorization.removeprefix('Bearer ')),))
        return {'ok': True}

    @app.post('/license/check')
    def license_check(body: Device, authorization: str = Header(default='')):
        user = user_for(authorization)
        if user['expires'] <= time.time():
            raise HTTPException(403, '授权已到期；保留账本和持仓管理，不再接收新自动开单')
        device = digest(body.device_id)
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            exists = conn.execute('SELECT 1 FROM devices WHERE username=? AND device=?', (user['username'], device)).fetchone()
            count = conn.execute('SELECT COUNT(*) FROM devices WHERE username=?', (user['username'],)).fetchone()[0]
            if not exists and count >= user['device_limit']:
                raise HTTPException(403, '设备数量已达到上限，请联系管理员解绑')
            conn.execute('INSERT INTO devices VALUES (?,?,?) ON CONFLICT(username,device) DO UPDATE SET seen=excluded.seen', (user['username'], device, time.time()))
        return {'valid': True, 'username': user['username'], 'expires_at': user['expires'], 'device_limit': user['device_limit']}

    @app.get('/admin/users')
    def users(authorization: str = Header(default='')):
        user_for(authorization, True)
        with database() as conn:
            return [dict(row) for row in conn.execute('SELECT username,admin,expires,device_limit,disabled FROM users ORDER BY username')]

    @app.post('/admin/users/{username}/grant')
    def grant(username: str, body: Grant, authorization: str = Header(default='')):
        admin = user_for(authorization, True)
        if username == admin['username'] and body.disabled:
            raise HTTPException(409, '不能禁用自己')
        with database() as conn:
            result = conn.execute('UPDATE users SET expires=?, device_limit=?, disabled=? WHERE username=?',
                                 (time.time()+body.days*86400, body.device_limit, int(body.disabled), username))
            if not result.rowcount:
                raise HTTPException(404, '账号不存在')
            conn.execute('INSERT INTO admin_audit(actor,target,action,timestamp) VALUES (?,?,?,?)',
                         (admin['username'], username, f'grant_days={body.days};disabled={body.disabled}', time.time()))
        return {'ok': True}

    @app.post('/admin/users/{username}/reset-devices')
    def reset_devices(username: str, authorization: str = Header(default='')):
        admin = user_for(authorization, True)
        with database() as conn:
            conn.execute('DELETE FROM devices WHERE username=?', (username,))
            conn.execute('DELETE FROM sessions WHERE username=?', (username,))
            conn.execute('INSERT INTO admin_audit(actor,target,action,timestamp) VALUES (?,?,?,?)',
                         (admin['username'], username, 'reset_devices_and_sessions', time.time()))
        return {'ok': True}

    @app.get('/')
    def index():
        return FileResponse(Path(__file__).parent / 'control_panel.html', headers={'Cache-Control':'no-store'})

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bootstrap', action='store_true')
    parser.add_argument('--data-dir', type=Path, default=Path(os.getenv('LOCALAPPDATA', '.')) / 'CopierControlPlane')
    args = parser.parse_args()
    application = create_app(args.data_dir / 'accounts.sqlite3')
    if args.bootstrap:
        username = input('Admin username: ')
        password = getpass.getpass('Password (at least 12 characters): ')
        application.state.create_user(Credentials(username=username, password=password), admin=True)
        print('Admin created. Run without --bootstrap to start the local service.')
    else:
        import uvicorn
        uvicorn.run(application, host='127.0.0.1', port=9000, access_log=False)
