from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from .bitget import BitgetDemoClient, BitgetError
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
    ParsedSignal,
    SignalStatus,
    SizingMode,
    SystemStatus,
    TelegramConnectionRequest,
)
from .parser import parse_signal
from .paper import PaperTradingError, PaperTradingStore
from .storage import SignalStore
from .telegram_client import TelegramSignalClient


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
        self.bitget = BitgetDemoClient(settings)
        self.paper = PaperTradingStore(settings.database_path)
        self.telegram = TelegramSignalClient(settings, self.ingest_signal)
        self._paper_monitor_task: asyncio.Task | None = None
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
        self.bitget_connected, self.bitget_detail = await self.bitget.healthcheck()
        if self.bitget_connected:
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
        await self.bitget_stream.stop()
        await self.bitget.close()

    async def ingest_signal(self, signal: ParsedSignal) -> None:
        inserted = self.store.upsert(signal)
        if inserted and self.paper.is_initialized() and self.paper.should_auto_execute(signal.source_name):
            try:
                await self.paper_execute(signal)
            except (PaperTradingError, BitgetError) as exc:
                self.store.add_audit(signal.id, "paper_execution_failed", str(exc))
        if not inserted or self.manual_review_enabled:
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
            requested_leverage = self.resolve_requested_leverage(signal.symbol)
            _, maximum_leverage = await self.bitget.symbol_leverage_limits(signal.symbol)
            effective_leverage = min(requested_leverage, maximum_leverage)
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

    async def paper_execute(self, signal: ParsedSignal) -> None:
        inserted = self.paper.enqueue(signal)
        if not inserted:
            raise PaperTradingError("该信号已经加入程序内模拟账户")
        self.store.add_audit(
            signal.id,
            "paper_order_created",
            "使用 Bitget 实盘行情监控入场区间；不会向交易所提交订单",
        )
        await self.refresh_paper_market([signal.symbol])

    async def refresh_paper_market(self, symbols: list[str] | None = None) -> None:
        active_symbols = symbols if symbols is not None else self.paper.active_symbols()
        for symbol in active_symbols:
            price = await self.bitget.market_price(symbol)
            self.paper.mark(symbol, price)

    async def _paper_market_loop(self) -> None:
        while True:
            try:
                await self.refresh_paper_market()
            except (BitgetError, OSError) as exc:
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
            telegram=status.telegram,
            telegram_api_id=self.settings.telegram_api_id,
            telegram_phone_hint=self._mask(self.settings.telegram_phone, visible=3),
            telegram_api_hash_configured=bool(self.settings.telegram_api_hash),
            telegram_allowed_chat_ids=sorted(self.settings.telegram_allowed_chat_ids),
        )

    async def configure_bitget(
        self, request: BitgetConnectionRequest
    ) -> ConnectionOverview:
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
        )
        new_client = BitgetDemoClient(candidate)
        connected, detail = await new_client.healthcheck()
        if not connected:
            await new_client.close()
            raise BitgetError(detail)

        update_env_file(
            {
                "BITGET_API_KEY": api_key,
                "BITGET_API_SECRET": api_secret,
                "BITGET_API_PASSPHRASE": passphrase,
                "BITGET_API_ENVIRONMENT": request.environment,
            }
        )
        old_client = self.bitget
        old_stream = self.bitget_stream
        await old_stream.stop()
        self.settings = candidate
        self.bitget = new_client
        self.bitget_stream = BitgetPrivateStream(
            candidate, self._on_bitget_stream_event, self._on_bitget_stream_state
        )
        self.bitget_connected = connected
        self.bitget_detail = detail
        await old_client.close()
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
        self.telegram = TelegramSignalClient(candidate, self.ingest_signal)
        await self.telegram.start()
        return await self.connection_overview()

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
            assets_result, settings_result, info_result, positions_result, orders_result = await asyncio.gather(
                self.bitget.account_summary(),
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
        result = await self.bitget.place_signal_order(
            validated_signal,
            size=request.size,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            execution_method=request.execution_method,
        )
        return self.store.update_status(
            signal,
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
