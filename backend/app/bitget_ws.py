from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .config import Settings


EventHandler = Callable[[str], Awaitable[None]]
StateHandler = Callable[[bool, str], Awaitable[None]]


def create_websocket_signature(timestamp: str, secret: str) -> str:
    message = f"{timestamp}GET/user/verify"
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class BitgetPrivateStream:
    """Read-only Bitget UTA private-channel listener with reconnect handling."""

    def __init__(
        self,
        settings: Settings,
        on_event: EventHandler,
        on_state: StateHandler,
    ) -> None:
        self.settings = settings
        self.on_event = on_event
        self.on_state = on_state
        self.connected = False
        self.detail = "实时连接未启动"
        self.last_event_at: datetime | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    @property
    def url(self) -> str:
        if self.settings.bitget_is_demo:
            return "wss://wspap.bitget.com/v3/ws/private"
        return "wss://ws.bitget.com/v3/ws/private"

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self.settings.bitget_configured:
            await self._set_state(False, "Bitget API 尚未配置")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="bitget-private-websocket")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await self._set_state(False, "实时连接已停止")

    async def _set_state(self, connected: bool, detail: str) -> None:
        self.connected = connected
        self.detail = detail
        await self.on_state(connected, detail)

    async def _login(self, websocket) -> None:
        timestamp = str(int(time.time()))
        await websocket.send(
            json.dumps(
                {
                    "op": "login",
                    "args": [
                        {
                            "apiKey": self.settings.bitget_api_key,
                            "passphrase": self.settings.bitget_api_passphrase,
                            "timestamp": timestamp,
                            "sign": create_websocket_signature(
                                timestamp, self.settings.bitget_api_secret or ""
                            ),
                        }
                    ],
                },
                separators=(",", ":"),
            )
        )
        response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=12))
        if response.get("event") != "login" or str(response.get("code", "0")) not in {"0", "00000"}:
            raise RuntimeError(
                f"Bitget WebSocket login failed {response.get('code', '')}: {response.get('msg', 'unknown error')}"
            )

    async def _subscribe(self, websocket) -> None:
        await websocket.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "args": [
                        {"instType": "UTA", "topic": "account"},
                        {"instType": "UTA", "topic": "position"},
                        {"instType": "UTA", "topic": "order"},
                    ],
                },
                separators=(",", ":"),
            )
        )

    async def _heartbeat(self, websocket) -> None:
        while True:
            await asyncio.sleep(25)
            await websocket.send("ping")

    async def _listen_once(self) -> None:
        async with connect(
            self.url,
            ping_interval=None,
            close_timeout=5,
            open_timeout=12,
            max_size=2**20,
        ) as websocket:
            await self._login(websocket)
            await self._subscribe(websocket)
            await self._set_state(True, "Bitget UTA 实时连接正常")
            heartbeat = asyncio.create_task(self._heartbeat(websocket))
            try:
                async for raw_message in websocket:
                    if raw_message == "pong":
                        continue
                    message = json.loads(raw_message)
                    if message.get("event") == "error":
                        raise RuntimeError(
                            f"Bitget WebSocket error {message.get('code', '')}: {message.get('msg', 'unknown error')}"
                        )
                    topic = (message.get("arg") or {}).get("topic")
                    if topic in {"account", "position", "order"} and message.get("data") is not None:
                        self.last_event_at = datetime.now(UTC)
                        await self.on_event(str(topic))
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass

    async def _run(self) -> None:
        attempt = 0
        while not self._stopping:
            try:
                await self._set_state(False, "正在连接 Bitget UTA 实时频道")
                await self._listen_once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, TimeoutError, ValueError, RuntimeError) as exc:
                attempt += 1
                delay = min(30.0, 2 ** min(attempt - 1, 5)) + random.uniform(0, 0.5)
                await self._set_state(False, f"实时连接中断，{delay:.1f} 秒后重试：{exc}")
                await asyncio.sleep(delay)
