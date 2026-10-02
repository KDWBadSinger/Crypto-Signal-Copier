"""Unauthenticated production ticker stream. Never a trade or credential transport."""
from __future__ import annotations

import asyncio
import json
import random
import time
from decimal import Decimal, InvalidOperation

from websockets.asyncio.client import connect


class PublicMarketFeed:
    URL = 'wss://ws.bitget.com/v2/ws/public'
    MAX_AGE = 15

    def __init__(self, on_tick, symbols, product_type='USDT-FUTURES'):
        self.on_tick = on_tick
        self.symbols = symbols
        self.product_type = product_type.upper()
        self.cache = {}
        self.task = None
        self.connected = False
        self.detail = '公开行情尚未启动'
        self.reconnects = 0
        self.rejected_ticks = 0

    async def start(self):
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.run(), name='public-market-feed')

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.connected = False

    def quote(self, symbol):
        item = self.cache.get(symbol)
        if not self.connected or not item:
            return None
        if time.monotonic() - item['received'] > self.MAX_AGE or time.time()*1000 - item['exchange_ts'] > self.MAX_AGE*1000:
            return None
        return item

    def snapshot(self):
        return {'connected': self.connected, 'detail': self.detail, 'reconnects': self.reconnects,
                'rejected_ticks': self.rejected_ticks,
                'quotes': {symbol: {'last_price': str(item['last']), 'mark_price': str(item['mark']),
                                   'exchange_ts': item['exchange_ts'], 'stale': self.quote(symbol) is None}
                           for symbol, item in self.cache.items()}}

    async def consume(self, payload):
        if payload.get('event') == 'error':
            self.detail = '行情订阅被拒绝，请检查交易对和网络'
            return
        arg = payload.get('arg') or {}
        if arg.get('channel') != 'ticker' or arg.get('instType') != self.product_type:
            return
        allowed = set(self.symbols())
        for row in payload.get('data') or []:
            try:
                symbol = row.get('instId') or arg.get('instId')
                if symbol not in allowed:
                    continue
                stamp = int(row.get('ts') or payload.get('ts'))
                last = Decimal(str(row.get('lastPr') or row.get('last')))
                mark = Decimal(str(row.get('markPrice')))
                if not all(value.is_finite() and value > 0 for value in (last, mark)):
                    raise ValueError('invalid price')
                age = time.time()*1000-stamp
                if age > self.MAX_AGE*1000 or age < -5000:
                    raise ValueError('clock skew or stale quote')
                previous = self.cache.get(symbol)
                if previous and stamp <= previous['exchange_ts']:
                    continue
                self.cache[symbol] = {'last': last, 'mark': mark, 'exchange_ts': stamp, 'received': time.monotonic()}
                self.detail = '公开行情推送已连接'
                await self.on_tick(symbol, mark)
            except (ValueError, TypeError, InvalidOperation):
                self.rejected_ticks += 1

    async def run(self):
        delay = 1
        while True:
            try:
                self.detail = '正在连接公开行情'
                async with connect(self.URL, ping_interval=None, open_timeout=10, close_timeout=3,
                                   max_size=2**20, max_queue=64) as socket:
                    self.connected = True
                    subscribed = set()
                    last_ping = time.monotonic()
                    last_received = time.monotonic()
                    while True:
                        wanted = set(self.symbols())
                        for operation, symbols in [('unsubscribe', subscribed-wanted), ('subscribe', wanted-subscribed)]:
                            ordered = sorted(symbols)
                            for index in range(0, len(ordered), 20):
                                await socket.send(json.dumps({'op': operation, 'args': [
                                    {'instType': self.product_type, 'channel': 'ticker', 'instId': symbol}
                                    for symbol in ordered[index:index+20]]}))
                        subscribed = wanted
                        now = time.monotonic()
                        if now-last_ping >= 20:
                            await socket.send('ping')
                            last_ping = now
                        if now-last_received > 45:
                            raise TimeoutError('market heartbeat expired')
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=2)
                        except TimeoutError:
                            continue
                        last_received = time.monotonic()
                        delay = 1
                        if raw != 'pong':
                            try:
                                message = json.loads(raw)
                                if isinstance(message, dict):
                                    await self.consume(message)
                            except (json.JSONDecodeError, TypeError):
                                self.rejected_ticks += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.detail = '公开行情断线重连中；有效 REST 行情兜底'
                self.reconnects += 1
            finally:
                self.connected = False
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay*2, 30)
