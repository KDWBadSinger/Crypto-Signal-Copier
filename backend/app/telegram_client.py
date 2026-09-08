from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from telethon import TelegramClient, events

from .config import Settings
from .models import ParsedSignal
from .parser import SignalParseError, parse_signal

logger = logging.getLogger(__name__)
SignalHandler = Callable[[ParsedSignal], Awaitable[None]]


class TelegramSignalClient:
    def __init__(self, settings: Settings, on_signal: SignalHandler) -> None:
        self.settings = settings
        self.on_signal = on_signal
        self.client: TelegramClient | None = None
        self.connected = False
        self.detail = "未配置"
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if not self.settings.telegram_configured:
                self.detail = "缺少 API 凭据或允许的频道 ID"
                return
            self.settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
            self.client = TelegramClient(
                str(self.settings.telegram_session_path),
                self.settings.telegram_api_id,
                self.settings.telegram_api_hash,
                sequential_updates=True,
            )
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.detail = "需要先运行一次性 Telegram 登录命令"
                await self.client.disconnect()
                self.client = None
                return

            self.client.add_event_handler(
                self._handle_message,
                events.NewMessage(),
            )
            self.connected = True
            self.detail = f"已连接，监听 {len(self.settings.telegram_allowed_chat_ids)} 个允许来源"
            logger.info(self.detail)

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.disconnect()
        self.client = None
        self.connected = False

    async def _handle_message(self, event) -> None:
        if event.chat_id not in self.settings.telegram_allowed_chat_ids:
            return
        text = (event.raw_text or "").strip()
        if not text:
            return
        chat = await event.get_chat()
        source_name = (
            getattr(chat, "title", None)
            or getattr(chat, "username", None)
            or str(event.chat_id)
        )
        try:
            signal = parse_signal(
                text,
                source_name=source_name,
                chat_id=event.chat_id,
                message_id=event.message.id,
            )
        except SignalParseError as exc:
            logger.info("Ignored non-signal Telegram message %s: %s", event.message.id, exc)
            # A compliant client acknowledges messages instead of implementing ghost mode.
            await self.client.send_read_acknowledge(event.chat_id, event.message)
            return
        await self.on_signal(signal)
        await self.client.send_read_acknowledge(event.chat_id, event.message)

    def state(self) -> tuple[bool, str]:
        return self.connected, self.detail
