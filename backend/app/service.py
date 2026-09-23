from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from .bitget import BitgetDemoClient, BitgetError, BitgetOrderUncertain
from .bitget_ws import BitgetPrivateStream
from .config import Settings, update_env_file
from .models import (
    ApproveSignalRequest,
    AutoExecutionSettings,
    AutoExecutionSettingsRequest,
    BitgetConnectionRequest,
    BitgetAccountSnapshot,
    BitgetAsset,
    BitgetOpenOrder,
    BitgetPosition,
    ConnectionOverview,
    ConnectionState,
    ExecutionMethod,
    LeverageOverrides,
    MarketOverview,
    MarketTicker,
    ParsedSignal,
    PaperAccount,
    SignalStatus,
    SizingMode,
    SystemStatus,
    TelegramConnectionRequest,
)
from .parser import parse_signal, PARSER_VERSION
from .paper import PaperTradingError, PaperTradingStore
from .storage import SignalStore
from .telegram_client import TelegramSignalClient
from .market_feed import PublicMarketFeed
from .licensing import LicenseClient
from .account_curve import AccountCurve
from .uta_runtime import UtaRuntime


DEMO_SIGNAL = """ETHUSDT SHORT
Entry: 3680 - 3720
SL: 3785
TP1: 3560
TP2: 3440
Risk: 1%"""


class CopierService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = SignalStore(settings.database_path)
        self.account_curve = AccountCurve(self.store)
        self.license = LicenseClient(self.store)
        self.bitget = BitgetDemoClient(settings)
        self.uta_runtime = UtaRuntime(self.bitget,self.store)
        self.paper = PaperTradingStore(settings.database_path)
        self._message_subscribers: set[asyncio.Queue] = set()
        self.telegram = TelegramSignalClient(settings, self.ingest_signal, self.record_telegram_message, self.store.get_message, self.manage_telegram_position, self.store.canonicalize_signal, self.parse_live_signal)
        self._execution_lock = asyncio.Lock()
        self._paper_monitor_task: asyncio.Task | None = None
        self._paper_lock = asyncio.Lock()
        self.market_feed = PublicMarketFeed(self._on_market_tick, self._market_symbols, settings.bitget_product_type)
        self._account_reconcile_task: asyncio.Task | None = None
        self.bitget_connected = False
        self.bitget_detail = "未配置"
        self._account_snapshot_cache: BitgetAccountSnapshot | None = None
        self._account_refresh_lock = asyncio.Lock()
        self._account_subscribers: set[asyncio.Queue[str]] = set()
        self.bitget_stream = BitgetPrivateStream(
            settings, self._on_bitget_stream_event, self._on_bitget_stream_state
        )
        stored_review_mode = self.store.get_setting("manual_review_enabled")
        self.manual_review_enabled = (
            settings.require_manual_review
            if stored_review_mode is None
            else stored_review_mode == "true"
        )
        self._auto_execution = AutoExecutionSettings(
            enabled=not self.manual_review_enabled,
            execution_method=self.store.get_setting("auto_execution_method") or ExecutionMethod.MARKET,
            sizing_mode=self.store.get_setting("auto_sizing_mode") or SizingMode.FIXED_USDT,
            fixed_usdt=Decimal(self.store.get_setting("auto_fixed_usdt") or "200"),
            position_percent=Decimal(self.store.get_setting("auto_position_percent") or "5"),
            default_leverage=int(self.store.get_setting("auto_default_leverage") or "10"),
        )
        stored_overrides = self.store.get_setting("leverage_overrides") or "[]"
        self._leverage_overrides = LeverageOverrides.model_validate(
            {"items": json.loads(stored_overrides)}
        )

    async def start(self) -> None:
        if self.store.get_setting('telegram_parser_version')!=PARSER_VERSION:
            await self.reparse_cached_messages()
            self.store.set_setting('telegram_parser_version',PARSER_VERSION)
        await self.uta_runtime.start()
        self.paper.runtime_start()
        await self.market_feed.start()
        self.bitget_connected, self.bitget_detail = await self.bitget.healthcheck()
        if self.bitget_connected:
            if isinstance((await self.bitget.account_summary()).get("data"), dict):
                await self.bitget_stream.start()
        if self.settings.seed_demo_data and self.store.latest() is None:
            await self.ingest_signal(
                parse_signal(DEMO_SIGNAL, source_name="CryptoAlpha Premium")
            )
        self._paper_monitor_task = asyncio.create_task(self._paper_market_loop())
        self._account_reconcile_task = asyncio.create_task(
            self._account_reconcile_loop()
        )
        await self.telegram.start()

    async def stop(self) -> None:
        await self.uta_runtime.stop()
        await self.market_feed.stop()
        if self._paper_monitor_task:
            self._paper_monitor_task.cancel()
            try:
                await self._paper_monitor_task
            except asyncio.CancelledError:
                pass
        if self._account_reconcile_task:
            self._account_reconcile_task.cancel()
            try:
                await self._account_reconcile_task
            except asyncio.CancelledError:
                pass
        await self.telegram.stop()
        self.paper.runtime_stop()
        await self.bitget_stream.stop()
        await self.bitget.close()

    async def parse_live_signal(self,text,message,parent=None):
        from .parser import parse_signal, normalize_signal_text, _extract_symbol
        existing=self.store.get(parent['signal_id']) if parent and parent.get('signal_id') else None
        if not (existing and existing.entry_correction) and not self.uta_runtime.enabled and not (self.paper.is_initialized() and self.paper.should_auto_execute(message['source_name'])):
            return parse_signal(text,source_name=message['source_name'],chat_id=message['chat_id'],message_id=message['message_id'])
        symbol=_extract_symbol(normalize_signal_text(text))
        if not symbol: raise ValueError('未识别唯一交易币种')
        if parent and not existing:
            raise ValueError('没有本程序实时开仓意图，回复不追补新单')
        # Protection replies use the pinned entry reference, not a moving quote.
        # Fresh current geometry is checked separately before modifying protection.
        price=existing.reference_entry if existing and existing.entry_correction else await self.public_price(symbol)
        return parse_signal(text,source_name=message['source_name'],chat_id=message['chat_id'],
                            message_id=message['message_id'],market_price=price,allow_pending=True)

    async def ingest_signal(self, signal: ParsedSignal) -> None:
        previous=self.store.get(signal.id)
        if previous and previous.entry_correction and not signal.awaiting_protection:
            results=[]
            try:
                price=await self.public_price(signal.symbol)
                async with self._paper_lock:
                    if self.paper.receive_protection(signal,price): results.append('模拟仓位保护已更新')
            except (ValueError,PaperTradingError,BitgetError) as exc:
                self.store.add_audit(signal.id,'paper_protection_rejected',str(exc))
            if self.uta_runtime.management_authorized:
                try:
                    self.uta_runtime._authorize_write()
                    row=await self.uta_runtime.engine.receive_protection(signal)
                    if row:
                        self.uta_runtime._sync_signal(row)
                        results.append('实盘原仓位保护已更新')
                except (ValueError,BitgetError) as exc:
                    self.store.add_audit(signal.id,'uta_protection_rejected',str(exc))
            if results:
                current=self.store.get(signal.id)
                completed=current.model_copy(update={'awaiting_protection':False,'stop_loss':signal.stop_loss,
                    'take_profits':signal.take_profits,'raw_text':signal.raw_text})
                self.store.upsert(completed)
            # All consumers retain durable execution state; a reply never opens.
            self.store.add_audit(signal.id,'protection_reply','；'.join(results) or '未更新：持仓不存在、已结束、重复回复或保护条件不满足')
            return
        inserted = self.store.upsert(signal, update=False)
        if inserted:
            try:
                await self.license.allow_new_order()
            except ValueError as exc:
                self.store.add_audit(signal.id, 'license_blocked', str(exc))
                return
        if inserted and self.paper.is_initialized() and self.paper.should_auto_execute(signal.source_name):
            try:
                await self.paper_execute(signal)
            except (PaperTradingError, BitgetError, ValueError) as exc:
                self.store.add_audit(signal.id, "paper_execution_failed", str(exc))
        if inserted and not self.settings.bitget_is_demo:
            try:
                if signal.source_chat_id not in self.settings.telegram_allowed_chat_ids:
                    raise BitgetError('仅执行已选择 Telegram 频道的实时信号')
                override=next((item for item in self._leverage_overrides.items if item.symbol==signal.symbol),None)
                requested=override.leverage if override else None
                await self.uta_runtime.ingest(signal,requested)
                self.store.add_audit(signal.id,'uta_execution','实盘开单已进入持久化交易所核对流程')
            except (ValueError,BitgetError) as exc:
                self.store.add_audit(signal.id,'auto_execution_blocked',str(exc))
            return
        if not inserted or self.manual_review_enabled:
            return
        if signal.awaiting_protection:
            self.store.add_audit(signal.id,'auto_execution_blocked','先开仓后补保护仅接入本地模拟及 UTA 实盘，不向经典 V2 模拟盘发送无完整保护订单')
            return
        if not self.settings.bitget_is_demo:
            self.store.add_audit(signal.id, "auto_execution_blocked", "实盘仅支持只读连接")
            return
        if not self.settings.bitget_enable_demo_orders:
            self.store.add_audit(
                signal.id,
                "auto_execution_blocked",
                "Bitget demo execution switch is disabled",
            )
            return
        if not self.bitget_connected:
            self.store.add_audit(
                signal.id,
                "auto_execution_blocked",
                "Bitget demo account is not connected",
            )
            return
        try:
            from .follow_policy import exchange_leverage
            override=next((item for item in self._leverage_overrides.items if item.symbol==signal.symbol),None)
            requested_leverage = override.leverage if override else None
            minimum_leverage, maximum_leverage = await self.bitget.symbol_leverage_limits(signal.symbol)
            effective_leverage = exchange_leverage(maximum_leverage,minimum_leverage,requested_leverage)
            order_size = await self._resolve_auto_order_size(signal, effective_leverage)
            await self.bitget.set_cross_leverage(signal.symbol, effective_leverage)
            self.store.add_audit(
                signal.id,
                "leverage_set",
                f"margin_mode=crossed requested={requested_leverage} max={maximum_leverage} effective={effective_leverage}",
            )
            await self.approve(
                signal,
                ApproveSignalRequest(
                    size=order_size,
                    execution_method=self._auto_execution.execution_method,
                    entry_price=signal.reference_entry,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profits[0],
                    confirm_demo_order=True,
                ),
            )
        except (ValueError, BitgetError) as exc:
            self.store.add_audit(signal.id, "auto_execution_failed", str(exc))
            latest = self.store.get(signal.id)
            if latest.status not in {SignalStatus.UNKNOWN, SignalStatus.SUBMITTING}:
                self.store.update_status(signal, SignalStatus.REJECTED, detail=str(exc))

    async def paper_execute(self, signal: ParsedSignal) -> None:
        await self.license.allow_new_order()
        minimum,maximum=await self.bitget.symbol_leverage_limits(signal.symbol)
        from .follow_policy import exchange_leverage
        override=next((item for item in self._leverage_overrides.items if item.symbol==signal.symbol),None)
        effective=exchange_leverage(maximum,minimum,override.leverage if override else None)
        async with self._paper_lock:
            inserted = self.paper.enqueue(signal,leverage=effective)
        if not inserted:
            raise PaperTradingError("该信号已经加入程序内模拟账户")
        self.store.add_audit(
            signal.id,
            "paper_order_created",
            f"使用 Bitget 实盘行情；不会向交易所提交订单。交易所最大杠杆 {maximum}x，实际 {effective}x（{'单币种配置' if override else '最大杠杆 50%，向下取整'}）",
        )
        await self.refresh_paper_market([signal.symbol])

    async def manage_telegram_position(self, message):
        command = message['management']
        live_result=None
        if self.uta_runtime.management_authorized:
            try:
                if command.get('ambiguous') or message.get('origin')!='live' or message['chat_id'] not in self.settings.telegram_allowed_chat_ids:
                    raise BitgetError('仅处理已监听频道的实时明确管理指令')
                if message.get('reply_to_chat_id',message['chat_id'])!=message['chat_id']:
                    raise BitgetError('不执行跨频道回复指令')
                parent=self.store.get_message(message['chat_id'],message['reply_to_message_id']) if message.get('reply_to_message_id') else None
                sid,detail=await self.uta_runtime.manage(message,parent.get('signal_id') if parent else None)
                live_result={'status':'managed','signal_id':sid,'detail':'UTA 实盘：'+detail}
            except (ValueError,BitgetError,OSError) as exc:
                live_result={'status':'management_rejected','detail':'UTA 实盘管理等待核对：'+str(exc)}
        try:
            if command['ambiguous']:
                raise PaperTradingError('管理消息含多个币种、否定或条件表述，不自动执行')
            root_id = message.get('reply_to_message_id')
            if message.get('reply_to_chat_id', message['chat_id']) != message['chat_id']:
                raise PaperTradingError('跨频道回复不执行管理指令')
            parent = self.store.get_message(message['chat_id'], root_id) if root_id else None
            signal_id = parent.get('signal_id') if parent else None
            if not command['symbol'] and not root_id:
                raise PaperTradingError('缺少币种和直接回复关系，不能定位持仓')
            async with self._paper_lock:
                target = self.paper.management_target(message['chat_id'], command['symbol'],
                                                       None if signal_id else root_id, signal_id)
                price = await self.public_price(target['symbol'])
                detail = self.paper.manage(message['chat_id'], message['message_id'], target['id'], command['actions'], price)
            message.update(status='managed', signal_id=target['signal_id'], detail='程序内模拟：'+detail+'。未向 Bitget 交易所发送管理操作。')
            self.store.add_audit(target['signal_id'], 'paper_management', message['detail'])
        except (PaperTradingError, BitgetError, OSError) as exc:
            message.update(status='management_rejected', detail=f'管理指令未执行：{exc}；交易所管理操作未启用')
        if live_result:
            message.update(live_result)

    async def refresh_paper_market(self, symbols: list[str] | None = None) -> None:
        async with self._paper_lock:
            if not self.paper.is_initialized() or self.paper.snapshot().lifecycle == 'stopped':
                return
            try:
                # Refresh the whole account before recording a coherent daily equity point.
                active_symbols = self.paper.active_symbols()
                prices = {symbol: await self.public_price(symbol) for symbol in active_symbols}
                for symbol, price in prices.items():
                    self.paper.mark(symbol, price)
                self.paper.heartbeat(market_ok=True)
            except (BitgetError, OSError, PaperTradingError) as exc:
                self.paper.heartbeat(error="公开行情暂不可用，保留上次估值；恢复连接后继续")
                raise

    async def stop_paper(self) -> PaperAccount:
        async with self._paper_lock:
            account = self.paper.snapshot()
            if account.lifecycle == 'stopped':
                return account
            symbols = {trade.symbol for trade in account.trades if trade.status == 'open'}
            prices = {symbol: await self.public_price(symbol) for symbol in symbols}
            return self.paper.settle(prices)

    def _market_symbols(self):
        return sorted(set(['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']) | set(self.paper.active_symbols()))

    async def public_price(self, symbol):
        quote = self.market_feed.quote(symbol)
        return quote['mark'] if quote else await self.bitget.market_price(symbol)

    async def _on_market_tick(self, symbol, price):
        if symbol not in self.paper.active_symbols():
            return
        async with self._paper_lock:
            quote = self.market_feed.quote(symbol)
            if quote:
                self.paper.mark(symbol, quote['mark'])

    async def market_overview(self) -> MarketOverview:
        core_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
        tracked_symbols = set(self.paper.active_symbols())
        if self.bitget_connected:
            try:
                account = await self.bitget_account_snapshot()
                tracked_symbols.update(position.symbol for position in account.positions)
                tracked_symbols.update(order.symbol for order in account.open_orders)
            except BitgetError:
                pass

        symbols = core_symbols + sorted(tracked_symbols.difference(core_symbols))
        snapshot = await self.bitget.market_snapshot(symbols)
        items: list[MarketTicker] = []
        for symbol in symbols:
            ticker, closes = snapshot[symbol]
            raw_price = ticker.get("lastPr") or ticker.get("markPrice")
            if not raw_price:
                continue
            items.append(
                MarketTicker(
                    symbol=symbol,
                    last_price=Decimal(str(raw_price)),
                    change_24h=Decimal(str(ticker.get("change24h") or "0")),
                    high_24h=Decimal(str(ticker.get("high24h") or "0")),
                    low_24h=Decimal(str(ticker.get("low24h") or "0")),
                    closes=closes,
                    tracked=symbol in tracked_symbols,
                )
            )
        return MarketOverview(items=items, updated_at=datetime.now(UTC))

    async def _paper_market_loop(self) -> None:
        while True:
            try:
                await self.refresh_paper_market()
            except (BitgetError, OSError, PaperTradingError) as exc:
                self.store.add_audit(None, "paper_market_refresh_failed", str(exc))
            await asyncio.sleep(5)

    async def status(self) -> SystemStatus:
        telegram_connected, telegram_detail = self.telegram.state()
        return SystemStatus(
            telegram=ConnectionState(
                configured=self.settings.telegram_configured,
                connected=telegram_connected,
                detail=telegram_detail,
            ),
            bitget=ConnectionState(
                configured=self.settings.bitget_configured,
                connected=self.bitget_connected,
                detail=self.bitget_detail,
            ),
            demo_order_execution_enabled=self.settings.bitget_enable_demo_orders,
            manual_review_enabled=self.manual_review_enabled,
            auto_order_size=self.settings.auto_demo_order_size,
            environment=self.settings.environment,
            bitget_environment=self.settings.bitget_api_environment,
            telegram_selected_channels=self.selected_telegram_channels(),
        )

    @staticmethod
    def _mask(value: str | None, visible: int = 4) -> str | None:
        if not value:
            return None
        if len(value) <= visible * 2:
            return "•" * len(value)
        return f"{value[:visible]}…{value[-visible:]}"

    async def connection_overview(self) -> ConnectionOverview:
        status = await self.status()
        return ConnectionOverview(
            bitget=status.bitget,
            bitget_environment=self.settings.bitget_api_environment,
            bitget_api_key_hint=self._mask(self.settings.bitget_api_key),
            demo_order_execution_enabled=self.settings.bitget_enable_demo_orders,
            telegram=status.telegram,
            telegram_api_id=self.settings.telegram_api_id,
            telegram_phone_hint=self._mask(self.settings.telegram_phone, visible=3),
            telegram_api_hash_configured=bool(self.settings.telegram_api_hash),
            telegram_allowed_chat_ids=sorted(self.settings.telegram_allowed_chat_ids),
        )

    async def configure_bitget(
        self, request: BitgetConnectionRequest
    ) -> ConnectionOverview:
        if self.uta_runtime.enabled or any(r['state'] not in {'closed','rejected'} for r in self.uta_runtime.engine.all()):
            raise BitgetError('请先停止新开仓并完成现有实盘仓位结算，再更换 API，避免遗失仓位管理')
        api_key = request.api_key.get_secret_value().strip() if request.api_key else self.settings.bitget_api_key
        api_secret = request.api_secret.get_secret_value().strip() if request.api_secret else self.settings.bitget_api_secret
        passphrase = request.passphrase.get_secret_value().strip() if request.passphrase else self.settings.bitget_api_passphrase
        if not api_key or not api_secret or not passphrase:
            raise ValueError("首次连接必须填写 API Key、Secret Key 和 Passphrase")

        candidate = replace(
            self.settings,
            bitget_api_key=api_key,
            bitget_api_secret=api_secret,
            bitget_api_passphrase=passphrase,
            bitget_api_environment=request.environment,
            bitget_enable_demo_orders=request.environment == "demo" and request.enable_demo_orders,
        )
        new_client = BitgetDemoClient(candidate)
        connected, detail = await new_client.healthcheck()
        if not connected:
            await new_client.close()
            raise BitgetError(detail)

        if candidate.bitget_enable_demo_orders:
            account = await new_client.account_summary()
            if isinstance(account.get("data"), dict):
                await new_client.close()
                raise BitgetError("本版本自动跟单使用经典合约模拟盘 V2；该 Key 属于 UTA 账户。请创建经典模拟盘 Key，或保持只读。")

        update_env_file(
            {
                "BITGET_API_KEY": api_key,
                "BITGET_API_SECRET": api_secret,
                "BITGET_API_PASSPHRASE": passphrase,
                "BITGET_API_ENVIRONMENT": request.environment,
                "BITGET_ENABLE_DEMO_ORDERS": str(candidate.bitget_enable_demo_orders).lower(),
            }
        )
        old_client = self.bitget
        desktop_authorized=self.uta_runtime.host_authorized
        await self.uta_runtime.stop()
        old_stream = self.bitget_stream
        await old_stream.stop()
        self.settings = candidate
        self.bitget = new_client
        self.uta_runtime = UtaRuntime(self.bitget,self.store)
        self.uta_runtime.host_authorized=desktop_authorized
        await self.uta_runtime.start()
        self.bitget_stream = BitgetPrivateStream(
            candidate, self._on_bitget_stream_event, self._on_bitget_stream_state
        )
        self.bitget_connected = connected
        self.bitget_detail = detail
        await old_client.close()
        if isinstance((await self.bitget.account_summary()).get("data"), dict):
            await self.bitget_stream.start()
        return await self.connection_overview()

    async def configure_telegram(
        self, request: TelegramConnectionRequest
    ) -> ConnectionOverview:
        api_hash = request.api_hash.get_secret_value().strip() if request.api_hash else self.settings.telegram_api_hash
        if not api_hash:
            raise ValueError("首次连接必须填写 Telegram API Hash")
        phone = request.phone.strip() if request.phone else self.settings.telegram_phone
        allowed_chat_ids = frozenset(request.allowed_chat_ids)
        candidate = replace(
            self.settings,
            telegram_api_id=request.api_id,
            telegram_api_hash=api_hash,
            telegram_phone=phone,
            telegram_allowed_chat_ids=allowed_chat_ids,
        )
        update_env_file(
            {
                "TELEGRAM_API_ID": str(request.api_id),
                "TELEGRAM_API_HASH": api_hash,
                "TELEGRAM_PHONE": phone or "",
                "TELEGRAM_ALLOWED_CHAT_IDS": ",".join(str(item) for item in sorted(allowed_chat_ids)),
            }
        )
        await self.telegram.stop()
        self.settings = candidate
        self.telegram = TelegramSignalClient(candidate, self.ingest_signal, self.record_telegram_message, self.store.get_message, self.manage_telegram_position, self.store.canonicalize_signal, self.parse_live_signal)
        await self.telegram.start()
        return await self.connection_overview()

    async def select_telegram_channels(self, chat_ids: list[int]) -> ConnectionOverview:
        dialogs = await self.telegram_channels()
        accessible = {item["id"] for item in dialogs}
        if not set(chat_ids).issubset(accessible):
            raise ValueError("只能选择当前 Telegram 账号已加入的频道或群组")
        update_env_file({"TELEGRAM_ALLOWED_CHAT_IDS": ",".join(map(str, sorted(set(chat_ids))))})
        self.settings = replace(self.settings, telegram_allowed_chat_ids=frozenset(chat_ids))
        self.telegram.settings = self.settings
        self.telegram.detail = f"已授权，监听 {len(set(chat_ids))} 个频道 / 群组"
        self.notify_telegram_subscribers()
        return await self.connection_overview()

    async def telegram_channels(self) -> list[dict]:
        channels = await self.telegram.dialogs()
        self.store.set_setting("telegram_channel_names", json.dumps({str(c["id"]): c["title"] for c in channels}, ensure_ascii=False))
        return channels

    def selected_telegram_channels(self) -> list[dict]:
        names = json.loads(self.store.get_setting("telegram_channel_names") or "{}")
        return [{"id": chat_id, "title": self.telegram.channel_names.get(chat_id) or names.get(str(chat_id)) or str(chat_id)}
                for chat_id in sorted(self.settings.telegram_allowed_chat_ids)]

    def record_telegram_message(self, message: dict) -> None:
        if message.get('_preview_only') or message.get('origin')=='repair':
            self.store.update_message_preview(message)
        else:
            self.store.record_message(message)
        self.notify_telegram_subscribers()

    async def reparse_cached_messages(self):
        """Local-only migration: no on_signal/on_management/network calls."""
        count=0
        async with self.telegram._dispatch_lock:
            for cached in reversed(self.store.messages(10000)):
                if cached.get('origin')=='edit' or cached.get('status') in {'managed','management_rejected','management','received','media'}:
                    continue
                # A user-triggered repair must not retire a freshly received
                # entry while its live protection reply is still on the way.
                if self.telegram.connected:
                    try:
                        if (datetime.now(UTC)-datetime.fromisoformat(cached['sent_at'])).total_seconds()<900:
                            continue
                    except (KeyError,TypeError,ValueError):
                        continue
                message={k:v for k,v in cached.items() if k not in {'parsed_signal','merged_messages','duplicate_of','signal_id'}}
                message.update(origin='repair',display_only=True,reparsed_at=datetime.now(UTC).isoformat())
                try: await self.telegram.inspect_message(message)
                except ValueError as exc: message.update(status='unparsed',detail=str(exc))
                self.store.update_message_preview(message)
                count+=1
        self.notify_telegram_subscribers()
        return count

    def notify_telegram_subscribers(self) -> None:
        for queue in tuple(self._message_subscribers):
            if not queue.full():
                queue.put_nowait(True)

    def telegram_inbox(self, chat_id: int | None = None, limit: int = 100) -> dict:
        allowed = self.settings.telegram_allowed_chat_ids
        channels = self.selected_telegram_channels()
        connected, detail = self.telegram.state()
        if chat_id is not None and chat_id not in allowed:
            messages = []
        else:
            messages = [m for m in self.store.messages(1000 if chat_id is None else limit, chat_id)
                        if m["chat_id"] in allowed][:limit]
        for message in messages:
            signal = self.store.get(message.get("signal_id", ""))
            message["execution"] = signal.model_dump(mode="json") if signal else None
            if signal:
                message["audit"] = self.store.audit_for(signal.id)[-10:]
                if signal.status == SignalStatus.PENDING_REVIEW:
                    message["execution_note"] = ("自动跟单未启用" if self.manual_review_enabled else
                        "请查看执行记录：模拟盘可能未连接或未允许下单")
            elif message.get('display_only'):
                message['execution_note']='旧消息已用新版规则重新解析，仅供核对，不补发订单'
            elif message.get("origin") == "history":
                message["execution_note"] = "历史补读，仅供查看，不触发跟单"
            elif message.get("origin") == "edit":
                message["execution_note"] = "编辑消息仅更新预览，不重新下单"
            else:
                message["execution_note"] = "未进入交易执行流程"
        for channel in channels:
            recent = self.store.messages(1, channel["id"])
            channel["last_message_at"] = recent[0].get("sent_at") or recent[0]["received_at"] if recent else None
        return {"channels": channels, "messages": messages, "connected": connected, "detail": detail,
                "auto_execution_enabled": (not self.manual_review_enabled) if self.settings.bitget_is_demo else self.uta_runtime.enabled,
                "updated_at": datetime.now(UTC).isoformat()}

    @staticmethod
    def _decimal(value, default: str = "0") -> Decimal:
        try:
            return Decimal(str(value if value not in {None, ""} else default))
        except (InvalidOperation, ValueError):
            return Decimal(default)

    @staticmethod
    def _timestamp(value) -> datetime | None:
        try:
            return datetime.fromtimestamp(int(value) / 1000, tz=UTC) if value else None
        except (TypeError, ValueError, OSError):
            return None

    async def bitget_account_snapshot(self) -> BitgetAccountSnapshot:
        return await self.refresh_bitget_account_snapshot()

    async def refresh_bitget_account_snapshot(self) -> BitgetAccountSnapshot:
        if not self.settings.bitget_configured or not self.bitget_connected:
            raise BitgetError("Bitget API 尚未连接")
        async with self._account_refresh_lock:
            assets_result = await self.bitget.account_summary()
            if isinstance(assets_result.get("data"), list):
                snapshot = await self._classic_account_snapshot(assets_result["data"])
                self._account_snapshot_cache = snapshot
                await self._publish_account_snapshot(snapshot)
                return snapshot
            settings_result, info_result, positions_result, orders_result = await asyncio.gather(
                self.bitget.account_settings(),
                self.bitget.account_info(),
                self.bitget.current_positions(),
                self.bitget.open_orders(),
            )
        account = assets_result.get("data") or {}
        if not isinstance(account, dict):
            raise BitgetError("当前资金管理页面需要 Bitget 统一账户（UTA）")
        account_settings = settings_result.get("data") or {}
        account_info = info_result.get("data") or {}
        position_rows = (positions_result.get("data") or {}).get("list") or []
        order_rows = (orders_result.get("data") or {}).get("list") or []

        assets = [
            BitgetAsset(
                coin=str(item.get("coin") or ""),
                equity=self._decimal(item.get("equity")),
                usd_value=self._decimal(item.get("usdValue")),
                balance=self._decimal(item.get("balance")),
                available=self._decimal(item.get("available")),
                locked=self._decimal(item.get("locked")),
                debt=self._decimal(item.get("debt")),
                bonus=self._decimal(item.get("bonus")),
            )
            for item in account.get("assets") or []
            if item.get("coin")
        ]
        positions = [
            BitgetPosition(
                symbol=str(item.get("symbol") or ""),
                side=str(item.get("posSide") or ""),
                margin_mode=str(item.get("marginMode") or ""),
                margin_coin=str(item.get("marginCoin") or ""),
                total=self._decimal(item.get("total")),
                leverage=self._decimal(item.get("leverage")),
                average_price=self._decimal(item.get("avgPrice")),
                mark_price=self._decimal(item.get("markPrice")),
                liquidation_price=(
                    self._decimal(item.get("liquidationPrice"))
                    if item.get("liquidationPrice") not in {None, ""}
                    else None
                ),
                unrealised_pnl=self._decimal(item.get("unrealisedPnl")),
                realised_pnl=self._decimal(item.get("curRealisedPnl")),
                profit_rate=self._decimal(item.get("profitRate")),
            )
            for item in position_rows
            if item.get("symbol")
        ]
        open_orders = [
            BitgetOpenOrder(
                symbol=str(item.get("symbol") or ""),
                category=str(item.get("category") or ""),
                side=str(item.get("side") or ""),
                position_side=item.get("posSide") or None,
                order_type=str(item.get("orderType") or ""),
                price=self._decimal(item.get("price")),
                quantity=self._decimal(item.get("qty")),
                filled_quantity=self._decimal(item.get("cumExecQty")),
                status=str(item.get("orderStatus") or ""),
                created_at=self._timestamp(item.get("createdTime")),
            )
            for item in order_rows
            if item.get("symbol")
        ]
        snapshot = BitgetAccountSnapshot(
            environment=self.settings.bitget_api_environment,
            account_mode=account_settings.get("accountMode"),
            account_level=account_settings.get("accountLevel"),
            asset_mode=account_settings.get("assetMode"),
            hold_mode=account_settings.get("holdMode"),
            permission_type=account_info.get("permType"),
            permissions=[str(item) for item in account_info.get("permissions") or []],
            account_equity_usd=self._decimal(account.get("accountEquity")),
            account_equity_usdt=self._decimal(account.get("usdtEquity")),
            account_equity_btc=self._decimal(account.get("btcEquity")),
            effective_equity_usd=self._decimal(account.get("effEquity")),
            unrealised_pnl_usd=self._decimal(account.get("unrealisedPnl")),
            initial_margin_usd=self._decimal(account.get("imr")),
            maintenance_margin_usd=self._decimal(account.get("mmr")),
            margin_ratio=self._decimal(account.get("mgnRatio")),
            position_value_usd=self._decimal(account.get("positionValue")),
            leverage=self._decimal(account.get("leverage")),
            assets=assets,
            positions=positions,
            open_orders=open_orders,
            realtime_connected=self.bitget_stream.connected,
            realtime_detail=self.bitget_stream.detail,
            last_realtime_event_at=self.bitget_stream.last_event_at,
            updated_at=datetime.now(UTC),
        )
        self._account_snapshot_cache = snapshot
        await self._publish_account_snapshot(snapshot)
        return snapshot

    async def _classic_account_snapshot(self, accounts: list[dict]) -> BitgetAccountSnapshot:
        positions, orders = await asyncio.gather(
            self.bitget._request("GET", "/api/v2/mix/position/all-position", params={
                "productType": self.settings.bitget_product_type, "marginCoin": self.settings.bitget_margin_coin}),
            self.bitget._request("GET", "/api/v2/mix/order/orders-pending", params={
                "productType": self.settings.bitget_product_type}),
        )
        assets = [BitgetAsset(coin=row.get("marginCoin", "USDT"),
                             equity=self._decimal(row.get("accountEquity")),
                             usd_value=self._decimal(row.get("usdtEquity") or row.get("accountEquity")),
                             available=self._decimal(row.get("available"))) for row in accounts]
        equity = sum((a.usd_value for a in assets), Decimal(0))
        position_rows = positions.get("data") or []
        order_rows = (orders.get("data") or {}).get("entrustedList") or []
        return BitgetAccountSnapshot(
            environment=self.settings.bitget_api_environment, account_mode="classic",
            account_equity_usd=equity, account_equity_usdt=equity, assets=assets,
            unrealised_pnl_usd=sum((self._decimal(row.get("unrealizedPL")) for row in position_rows), Decimal(0)),
            positions=[BitgetPosition(symbol=row["symbol"], side=row.get("holdSide", ""),
                margin_mode=row.get("marginMode", ""), margin_coin=row.get("marginCoin", "USDT"),
                total=self._decimal(row.get("total")), leverage=self._decimal(row.get("leverage")),
                average_price=self._decimal(row.get("openPriceAvg")), mark_price=self._decimal(row.get("markPrice")),
                unrealised_pnl=self._decimal(row.get("unrealizedPL"))) for row in position_rows if row.get("symbol")],
            open_orders=[BitgetOpenOrder(symbol=row["symbol"], category=self.settings.bitget_product_type,
                side=row.get("side", ""), order_type=row.get("orderType", ""),
                price=self._decimal(row.get("price")), quantity=self._decimal(row.get("size")),
                filled_quantity=self._decimal(row.get("baseVolume")), status=row.get("status", ""),
                created_at=self._timestamp(row.get("cTime"))) for row in order_rows if row.get("symbol")],
            realtime_detail="经典模拟盘：REST 定时校准", updated_at=datetime.now(UTC),
        )

    async def _on_bitget_stream_event(self, _: str) -> None:
        try:
            await self.refresh_bitget_account_snapshot()
        except (BitgetError, OSError):
            pass

    async def _account_reconcile_loop(self) -> None:
        """Periodically reconcile WebSocket state with Bitget's REST snapshot."""
        while True:
            await asyncio.sleep(60)
            if not self._account_subscribers or not self.bitget_connected:
                continue
            try:
                await self.refresh_bitget_account_snapshot()
            except (BitgetError, OSError):
                pass

    async def _on_bitget_stream_state(self, connected: bool, detail: str) -> None:
        if self._account_snapshot_cache is None:
            return
        snapshot = self._account_snapshot_cache.model_copy(
            update={
                "realtime_connected": connected,
                "realtime_detail": detail,
                "last_realtime_event_at": self.bitget_stream.last_event_at,
            }
        )
        self._account_snapshot_cache = snapshot
        await self._publish_account_snapshot(snapshot)

    async def _publish_account_snapshot(self, snapshot: BitgetAccountSnapshot) -> None:
        self.account_curve.record(self.settings, snapshot)
        payload = snapshot.model_dump_json()
        for queue in tuple(self._account_subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def subscribe_bitget_account(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        self._account_subscribers.add(queue)
        return queue

    def unsubscribe_bitget_account(self, queue: asyncio.Queue[str]) -> None:
        self._account_subscribers.discard(queue)

    def set_manual_review(self, enabled: bool) -> None:
        self.manual_review_enabled = enabled
        self.store.set_setting(
            "manual_review_enabled", "true" if enabled else "false"
        )
        self._auto_execution = self._auto_execution.model_copy(update={"enabled": not enabled})

    def auto_execution_settings(self) -> AutoExecutionSettings:
        return self._auto_execution

    def set_auto_execution_settings(
        self, request: AutoExecutionSettingsRequest
    ) -> AutoExecutionSettings:
        self._auto_execution = AutoExecutionSettings.model_validate(request.model_dump())
        self.manual_review_enabled = not self._auto_execution.enabled
        values = {
            "manual_review_enabled": "true" if self.manual_review_enabled else "false",
            "auto_execution_method": self._auto_execution.execution_method.value,
            "auto_sizing_mode": self._auto_execution.sizing_mode.value,
            "auto_fixed_usdt": format(self._auto_execution.fixed_usdt, "f"),
            "auto_position_percent": format(self._auto_execution.position_percent, "f"),
            "auto_default_leverage": str(self._auto_execution.default_leverage),
        }
        for key, value in values.items():
            self.store.set_setting(key, value)
        return self._auto_execution

    def leverage_overrides(self) -> LeverageOverrides:
        return self._leverage_overrides

    def set_leverage_overrides(self, request: LeverageOverrides) -> LeverageOverrides:
        self._leverage_overrides = request
        self.store.set_setting(
            "leverage_overrides",
            json.dumps(
                [item.model_dump() for item in request.items],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        return self._leverage_overrides

    def resolve_requested_leverage(self, symbol: str) -> int:
        normalized = symbol.strip().upper()
        override = next(
            (item for item in self._leverage_overrides.items if item.symbol == normalized),
            None,
        )
        return override.leverage if override else self._auto_execution.default_leverage

    async def _resolve_auto_order_size(
        self, signal: ParsedSignal, effective_leverage: int
    ) -> Decimal:
        if self._auto_execution.sizing_mode == SizingMode.FIXED_USDT:
            committed_margin = self._auto_execution.fixed_usdt
        else:
            snapshot = await self.bitget_account_snapshot()
            committed_margin = (
                snapshot.account_equity_usdt
                * self._auto_execution.position_percent
                / Decimal("100")
            )
        notional = committed_margin * Decimal(effective_leverage)
        if notional <= 0:
            raise ValueError("automatic order notional must be greater than 0")
        size = (notional / signal.reference_entry).quantize(Decimal("0.00000001"))
        if size <= 0:
            raise ValueError("automatic order size is too small")
        return size

    async def approve(
        self, signal: ParsedSignal, request: ApproveSignalRequest
    ) -> ParsedSignal:
        await self.license.allow_new_order()
        async with self._execution_lock:
            latest = self.store.get(signal.id) or signal
            return await self._approve_locked(latest, request)

    async def _approve_locked(
        self, signal: ParsedSignal, request: ApproveSignalRequest
    ) -> ParsedSignal:
        if signal.awaiting_protection:
            raise ValueError('市价开仓意图只能由专用临时保护生命周期执行，不能走普通审批接口')
        if signal.status not in {
            SignalStatus.PENDING_REVIEW,
            SignalStatus.APPROVED_DRY_RUN,
        }:
            raise ValueError(f"signal cannot be approved from status {signal.status}")

        entry_price = request.entry_price or signal.reference_entry
        stop_loss = request.stop_loss or signal.stop_loss
        take_profit = request.take_profit or signal.take_profits[0]
        validated_signal = ParsedSignal.model_validate(
            {
                **signal.model_dump(),
                "entry_low": entry_price,
                "entry_high": entry_price,
                "stop_loss": stop_loss,
                "take_profits": [take_profit],
            }
        )

        if not request.confirm_demo_order or not self.settings.bitget_enable_demo_orders:
            return self.store.update_status(
                signal,
                SignalStatus.APPROVED_DRY_RUN,
                client_oid=f"mia_{signal.id}"[:32],
                detail=(
                    f"dry-run size={request.size} entry={entry_price} "
                    f"sl={stop_loss} tp={take_profit}"
                ),
            )

        if not self.bitget_connected:
            raise BitgetError("Bitget demo account is not connected")
        if request.execution_method == ExecutionMethod.MARKET:
            current_price = await self.bitget.market_price(signal.symbol)
            if abs(current_price / entry_price - 1) > Decimal("0.02"):
                raise ValueError("当前市价偏离信号参考入场价超过 2%，停止自动追单")
            ParsedSignal.model_validate({**validated_signal.model_dump(), "entry_low": current_price,
                                         "entry_high": current_price})
        self.store.update_status(signal, SignalStatus.SUBMITTING, client_oid=f"mia_{signal.id}"[:32],
                                 detail="正在提交模拟盘；中断时请通过 clientOid 核对，勿重复执行")
        try:
            result = await self.bitget.place_signal_order(
                validated_signal, size=request.size, entry_price=entry_price,
                stop_loss=stop_loss, take_profit=take_profit,
                execution_method=request.execution_method,
            )
        except BitgetOrderUncertain as exc:
            self.store.update_status(signal, SignalStatus.UNKNOWN, client_oid=f"mia_{signal.id}"[:32], detail=str(exc))
            raise
        except (ValueError, BitgetError) as exc:
            self.store.update_status(signal, SignalStatus.REJECTED, detail=str(exc))
            raise
        return self.store.update_status(
            signal.model_copy(update={"execution_size": result.size or request.size}),
            SignalStatus.SUBMITTED,
            order_id=result.order_id,
            client_oid=result.client_oid,
            detail=f"Bitget demo order submitted: {result.order_id}",
        )

    def ignore(self, signal: ParsedSignal) -> ParsedSignal:
        return self.store.update_status(
            signal,
            SignalStatus.IGNORED,
            detail="Ignored by user",
        )
