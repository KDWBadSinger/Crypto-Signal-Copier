"""Online entitlement client. Never forwards trading keys or Telegram data."""
import os
import secrets
import time
from urllib.parse import urlparse

import httpx

from .config import DATA_HOME
from .secrets import read_secrets, write_secrets


class LicenseClient:
    def __init__(self, store):
        self.store = store
        self.required = os.getenv('COPIER_REQUIRE_LICENSE', 'false').lower() == 'true'
        self.url = os.getenv('COPIER_ACCOUNT_SERVER', 'http://127.0.0.1:9000').rstrip('/')
        parsed = urlparse(self.url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/') or not (parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost'}):
            raise ValueError('账号服务仅允许 HTTPS 或本机 HTTP 地址')
        self.device = store.get_setting('account_device_id') or secrets.token_urlsafe(24)
        store.set_setting('account_device_id', self.device)
        self.token = read_secrets(DATA_HOME / 'account-session.bin').get('token', '') if DATA_HOME else ''
        self.cached = {'valid': False, 'detail': '未登录账号服务'}
        self.checked_at = 0

    async def request(self, path, body):
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            try:
                response = await client.post(self.url+path, json=body, headers={'Authorization': 'Bearer '+self.token})
                data = response.json()
                if not response.is_success:
                    raise ValueError(data.get('detail') if isinstance(data.get('detail'), str) else '账号服务拒绝请求')
                return data
            except (httpx.HTTPError, ValueError) as exc:
                if isinstance(exc, httpx.HTTPError):
                    raise ValueError('账号服务不可用，请启动本地服务或检查网络') from exc
                raise

    async def login(self, username, password, register=False):
        if register:
            return await self.request('/register', {'username':username,'password':password})
        result = await self.request('/login', {'username':username,'password':password})
        self.token = result['token']
        if DATA_HOME:
            write_secrets(DATA_HOME / 'account-session.bin', {'token':self.token})
        self.checked_at = 0
        return await self.status(force=True)

    async def logout(self):
        try:
            if self.token:
                await self.request('/logout', {})
        finally:
            self.token = ''
            self.cached = {'valid':False, 'detail':'已退出账号服务'}
            self.checked_at = 0
            if DATA_HOME:
                write_secrets(DATA_HOME / 'account-session.bin', {'token':''})

    async def status(self, force=False):
        if self.token and (force or time.monotonic()-self.checked_at > 30):
            try:
                self.cached = await self.request('/license/check', {'device_id':self.device})
            except ValueError as exc:
                self.cached = {'valid':False, 'detail':str(exc)}
            self.checked_at = time.monotonic()
        return {**self.cached, 'required':self.required, 'server':self.url,
                'mode':'授权校验模式' if self.required else '本地验收模式（不限制跟单）'}

    async def allow_new_order(self):
        if self.required and not (await self.status()).get('valid'):
            raise ValueError('账号授权无效，新开单已暂停；已有持仓管理和停止结算不受影响')
