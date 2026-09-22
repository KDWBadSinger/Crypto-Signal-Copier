from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from telethon import TelegramClient, events, utils
from telethon.errors import RPCError, SessionPasswordNeededError
from telethon.sessions import StringSession

from .config import DATA_HOME, Settings
from .models import ParsedSignal
from .parser import parse_signal, is_entry_fragment, merge_reply
from .secrets import read_secrets, write_secrets
from .management import parse_management

logger = logging.getLogger(__name__)
SignalHandler = Callable[[ParsedSignal], Awaitable[None]]


class TelegramSignalClient:
    def __init__(self, settings: Settings, on_signal: SignalHandler, on_message=None, has_message=None, on_management=None) -> None:
        self.settings = settings
        self.on_signal = on_signal
        self.on_message = on_message
        self.has_message = has_message
        self.on_management = on_management
        self.channel_names: dict[int, str] = {}
        self._history_lock = asyncio.Lock()
        self._dispatch_lock = asyncio.Lock()
        self._processing_tasks: set[asyncio.Task] = set()
        self.client: TelegramClient | None = None
        self.connected = False
        self.detail = "未配置"
        self._lock = asyncio.Lock()
        self._phone_code_hash = None
        self._password_required = False
        self._handler_installed = False

    async def _connect(self) -> None:
        if not self.settings.telegram_configured:
            raise ValueError("请先保存 Telegram API ID 和 API Hash")
        if self.client is None:
            self.settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
            session = str(self.settings.telegram_session_path)
            if DATA_HOME:
                session = StringSession(read_secrets(DATA_HOME / "telegram-session.bin").get("session", ""))
            self.client = TelegramClient(
                session, self.settings.telegram_api_id, self.settings.telegram_api_hash,
                sequential_updates=True, connection_retries=3, request_retries=2,
                flood_sleep_threshold=0, auto_reconnect=True,
            )
        if not self.client.is_connected():
            await asyncio.wait_for(self.client.connect(), timeout=20)

    async def _activate(self) -> None:
        await self.client.get_me()
        if not self._handler_installed:
            self.client.add_event_handler(self._on_new_message, events.NewMessage())
            self.client.add_event_handler(self._on_edited_message, events.MessageEdited())
            self._handler_installed = True
        self.connected = True
        self.detail = f"已授权，监听 {len(self.settings.telegram_allowed_chat_ids)} 个频道 / 群组"
        try:
            await asyncio.wait_for(self.dialogs(), timeout=15)
        except (OSError, ValueError, TimeoutError, RPCError):
            logger.info("Channel names will be refreshed on the next inbox sync")
        if DATA_HOME:
            write_secrets(DATA_HOME / "telegram-session.bin", {"session": self.client.session.save()})

    async def start(self) -> None:
        if not self.settings.telegram_configured:
            self.detail = "请填写 Telegram API 凭据并登录"
            return
        async with self._lock:
            try:
                await self._connect()
                if await self.client.is_user_authorized():
                    await self._activate()
                else:
                    self.detail = "API 已保存，请在此页面发送验证码完成登录"
            except (OSError, TimeoutError, RPCError) as exc:
                self.detail = f"Telegram 连接失败（{type(exc).__name__}），可重试登录"

    async def send_code(self) -> dict:
        async with self._lock:
            await self._connect()
            if await self.client.is_user_authorized():
                await self._activate()
                return {"state": "authorized"}
            if not self.settings.telegram_phone:
                raise ValueError("请先保存包含国家区号的手机号")
            result = await self.client.send_code_request(self.settings.telegram_phone)
            self._phone_code_hash = result.phone_code_hash
            self._password_required = False
            return {"state": "code_sent"}

    async def sign_in(self, code: str | None, password: str | None) -> dict:
        async with self._lock:
            await self._connect()
            try:
                if self._password_required:
                    if not password:
                        return {"state": "password_required"}
                    await self.client.sign_in(password=password)
                else:
                    if not self._phone_code_hash or not code:
                        raise ValueError("请先发送验证码，再输入收到的验证码")
                    await self.client.sign_in(phone=self.settings.telegram_phone, code=code,
                                              phone_code_hash=self._phone_code_hash)
            except SessionPasswordNeededError:
                self._password_required = True
                return {"state": "password_required"}
            await self._activate()
            self._phone_code_hash = None
            self._password_required = False
            return {"state": "authorized"}

    async def dialogs(self) -> list[dict]:
        if not self.connected:
            raise ValueError("请先完成 Telegram 登录")
        result = []
        async for dialog in self.client.iter_dialogs():
            if dialog.is_channel or dialog.is_group:
                result.append({"id": dialog.id, "title": dialog.name,
                               "selected": dialog.id in self.settings.telegram_allowed_chat_ids})
                self.channel_names[dialog.id] = dialog.name
        return result

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.disconnect()
        self.client = None
        self.connected = False
        self._handler_installed = False
        for task in self._processing_tasks:
            task.cancel()
        if self._processing_tasks:
            await asyncio.gather(*self._processing_tasks, return_exceptions=True)
        self._processing_tasks.clear()

    def _schedule_message(self, event, edited=False) -> None:
        task = asyncio.create_task(self._handle_message(event, edited=edited))
        self._processing_tasks.add(task)
        def finished(done):
            self._processing_tasks.discard(done)
            if not done.cancelled() and done.exception():
                logger.error("Message handler failed: %s", type(done.exception()).__name__)
        task.add_done_callback(finished)

    async def _on_new_message(self, event) -> None:
        self._schedule_message(event)

    async def _on_edited_message(self, event) -> None:
        self._schedule_message(event, edited=True)

    @staticmethod
    def describe_message(raw, chat_id: int, source_name: str, *, origin: str = "live") -> dict:
        text = (getattr(raw, "message", None) or getattr(raw, "raw_text", None) or "").strip()
        media_kind = None
        for attr, label in [("photo", "图片"), ("video", "视频"), ("voice", "语音"), ("audio", "音频"),
                            ("sticker", "贴纸"), ("document", "文件"), ("poll", "投票"), ("action", "系统事件")]:
            if getattr(raw, attr, None):
                media_kind = label
                break
        if not media_kind and getattr(raw, "media", None):
            media_kind = "附件"
        sent_at = getattr(raw, "date", None)
        reply_peer = getattr(getattr(raw, "reply_to", None), "reply_to_peer_id", None)
        message = {"chat_id": chat_id, "message_id": raw.id, "source_name": source_name,
                   "text": text, "media_kind": media_kind, "origin": origin,
                   "sent_at": sent_at.isoformat() if sent_at else None,
                   "received_at": datetime.now(UTC).isoformat(), "status": "received",
                   "reply_to_message_id": getattr(raw, "reply_to_msg_id", None),
                   "reply_to_chat_id": utils.get_peer_id(reply_peer) if reply_peer else chat_id,
                   "edited_at": getattr(raw, "edit_date", None).isoformat() if getattr(raw, "edit_date", None) else None}
        return message

    @staticmethod
    def inspect_signal(message: dict) -> ParsedSignal | None:
        if not message["text"]:
            message.update(status="media", detail="无文字内容；附件已同步，不进行图片/音频交易信号识别")
            return None
        try:
            signal = parse_signal(message["text"], source_name=message["source_name"],
                                  chat_id=message["chat_id"], message_id=message["message_id"])
            message.update(status="parsed", signal_id=signal.id, parsed_signal=signal.model_dump(mode="json"),
                           detail="包含完整的币种、方向、入场价、止损和止盈；仅表示格式校验通过，不代表交易建议正确")
            return signal
        except ValueError as exc:
            detail = str(exc)
            if detail.startswith("missing required fields: "):
                names = {"symbol": "币种", "unambiguous side": "明确且唯一的多空方向", "entry": "入场价格",
                         "stop loss": "止损价格", "take profit": "止盈价格"}
                missing = [names.get(item, item) for item in detail.split(": ", 1)[1].split(", ")]
                detail = "未识别为完整交易信号：缺少" + "、".join(missing)
            elif "long stop loss" in detail: detail = "多单止损必须低于入场区间"
            elif "short stop loss" in detail: detail = "空单止损必须高于入场区间"
            elif "long take profits" in detail: detail = "多单止盈必须高于入场区间"
            elif "short take profits" in detail: detail = "空单止盈必须低于入场区间"
            message.update(status="unparsed", detail=detail)
            if is_entry_fragment(message["text"]):
                message.update(status="waiting", detail="已识别开单，等待直接回复本消息的止盈和止损；参数完整前不下单")
            return None

    async def inspect_message(self, message: dict) -> ParsedSignal | None:
        command = parse_management(message['text'])
        if command:
            message.update(status='management', management=command, detail='持仓管理指令；历史与编辑仅预览，不执行')
            return None
        parent_id = message.get("reply_to_message_id")
        if not parent_id:
            return self.inspect_signal(message)
        if message.get("reply_to_chat_id", message["chat_id"]) != message["chat_id"]:
            message.update(status="unparsed", detail="跨频道回复不自动合并，避免同号消息错误关联")
            return None
        parent = self.has_message(message["chat_id"], parent_id) if self.has_message else None
        if parent is None and self.client and hasattr(self.client, "get_messages"):
            raw = await self.client.get_messages(message["chat_id"], ids=parent_id)
            if raw:
                parent = self.describe_message(raw, message["chat_id"], message["source_name"], origin="history")
                self.inspect_signal(parent)
                if self.on_message:
                    self.on_message(parent)
        if parent is None:
            self.inspect_signal(message)
            message.update(status="unparsed", detail="无法取得被回复的开单消息，不猜测关联，不自动下单")
            return None
        try:
            combined = merge_reply(parent["text"], message["text"])
            signal = parse_signal(combined, source_name=message["source_name"],
                                  chat_id=message["chat_id"], message_id=parent_id)
        except ValueError as exc:
            message.update(status="unparsed", detail=str(exc))
            return None
        sources = [{"message_id": parent_id, "text": parent["text"]},
                   {"message_id": message["message_id"], "text": message["text"]}]
        signal.raw_text = f"开单 #{parent_id}\n{parent['text']}\n\n止盈止损 #{message['message_id']}（回复 #{parent_id}）\n{message['text']}"
        message.update(status="parsed", signal_id=signal.id, parsed_signal=signal.model_dump(mode="json"),
                       merged_messages=sources, detail=f"按同频道回复关系合并 #{parent_id} + #{message['message_id']}；完整参数已校验")
        if message["origin"] == "live":
            try:
                elapsed = (datetime.fromisoformat(message["sent_at"]) - datetime.fromisoformat(parent["sent_at"])).total_seconds()
            except (TypeError, ValueError):
                elapsed = -1
            if parent.get("origin") != "live" or not 0 <= elapsed <= 900:
                message.update(status="stale", detail="合并仅供查看：开单不是实时收到，或回复间隔超过 15 分钟，不自动跟单")
                return None
        # The entry message retains its original text while pointing to the same order.
        # Edited previews never replace original correlation/execution evidence.
        if message["origin"] != "edit" and not parent.get("merged_messages"):
            parent.update(status="parsed", signal_id=signal.id, parsed_signal=signal.model_dump(mode="json"),
                          merged_messages=sources, detail=message["detail"])
            if self.on_message:
                self.on_message(parent)
        return signal

    async def sync_history(self, chat_id: int, limit: int = 30) -> int:
        if not self.connected or chat_id not in self.settings.telegram_allowed_chat_ids:
            raise ValueError("请先登录，并保存要监听的频道")
        async with self._history_lock:
            # StringSession doesn't persist the entity/access-hash cache across launches.
            await self.dialogs()
            name = self.channel_names.get(chat_id, str(chat_id))
            count = 0
            history = [raw async for raw in self.client.iter_messages(chat_id, limit=limit)]
            for raw in reversed(history):
                if chat_id not in self.settings.telegram_allowed_chat_ids:
                    break
                message = self.describe_message(raw, chat_id, name, origin="history")
                await self.inspect_message(message)
                if self.on_message:
                    self.on_message(message)
                count += 1
            return count

    async def photo(self, chat_id: int, message_id: int) -> bytes:
        if not self.connected or chat_id not in self.settings.telegram_allowed_chat_ids:
            raise ValueError("只能查看当前监听频道的图片")
        await self.dialogs()
        raw = await self.client.get_messages(chat_id, ids=message_id)
        if raw is None or not raw.photo:
            raise ValueError("该消息没有可预览的图片")
        data = await self.client.download_media(raw, file=bytes, thumb=-1)
        if not data or len(data) > 5 * 1024 * 1024:
            raise ValueError("图片不可用或超过 5MB 预览上限")
        return data

    async def _handle_edit(self, event) -> None:
        await self._handle_message(event, edited=True)

    async def _handle_message(self, event, edited: bool = False) -> None:
        if event.chat_id not in self.settings.telegram_allowed_chat_ids:
            return
        previous = self.has_message(event.chat_id, event.message.id) if self.has_message else None
        if not edited and previous:
            return
        try:
            chat = await event.get_chat()
            source_name = getattr(chat, "title", None) or getattr(chat, "username", None) or str(event.chat_id)
        except (OSError, ValueError, RPCError):
            source_name = self.channel_names.get(event.chat_id, str(event.chat_id))
        self.channel_names[event.chat_id] = source_name
        message = self.describe_message(event.message, event.chat_id, source_name, origin="edit" if edited else "live")
        message["text"] = (event.raw_text or "").strip()
        if edited and previous and previous.get("signal_id"):
            message["signal_id"] = previous["signal_id"]
        # Persist and notify immediately, before any exchange request can block.
        if self.on_message:
            self.on_message(message)
        try:
            signal = await self.inspect_message(message)
            sent_at = getattr(event.message, "date", None)
            if edited:
                message["detail"] = "消息已编辑，仅更新原文和解析预览；不会重新下单。" + message.get("detail", "")
            elif message.get('management'):
                async with self._dispatch_lock:
                    if not sent_at or not 0 <= (datetime.now(UTC)-sent_at).total_seconds() <= 120:
                        message.update(status='stale', detail='管理指令时间无效或超过 120 秒，只记录不执行')
                    elif event.chat_id in self.settings.telegram_allowed_chat_ids and self.on_management:
                        await self.on_management(message)
            elif signal and sent_at and (datetime.now(UTC) - sent_at).total_seconds() > 120:
                message.update(status="stale", detail="消息超过 120 秒，只记录不跟单")
            elif signal:
                if self.on_message:
                    self.on_message(message)
                async with self._dispatch_lock:
                    if sent_at and (datetime.now(UTC) - sent_at).total_seconds() > 120:
                        message.update(status="stale", detail="处理队列等待后消息已超过 120 秒，不跟单")
                    elif event.chat_id in self.settings.telegram_allowed_chat_ids:
                        await self.on_signal(signal)
        except Exception as exc:
            logger.error("Signal processing failed: %s", type(exc).__name__)
            message.update(status="error", detail=f"处理失败：{type(exc).__name__}")
        finally:
            if self.on_message:
                self.on_message(message)
            try:
                await self.client.send_read_acknowledge(event.chat_id, event.message)
            except (OSError, RPCError):
                pass

    def state(self) -> tuple[bool, str]:
        online = self.connected and self.client is not None and self.client.is_connected()
        return online, self.detail if online or not self.connected else "Telegram 正在重连"
