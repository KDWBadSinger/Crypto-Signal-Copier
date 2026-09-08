import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import load_settings
from app.models import (
    ApproveSignalRequest,
    AutoExecutionSettingsRequest,
    ExecutionMethod,
    LeverageOverride,
    LeverageOverrides,
    SignalStatus,
    SizingMode,
)
from app.parser import parse_signal
from app.service import CopierService


def _service(tmp_path) -> CopierService:
    settings = replace(
        load_settings(),
        database_path=tmp_path / "signals.sqlite3",
        seed_demo_data=False,
        bitget_enable_demo_orders=False,
    )
    return CopierService(settings)


def test_approval_defaults_to_dry_run(tmp_path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        signal = parse_signal(
            "ETHUSDT SHORT\nEntry: 3680 - 3720\nSL: 3785\nTP1: 3560",
            source_name="test",
        )
        service.store.upsert(signal)
        try:
            updated = await service.approve(
                signal,
                ApproveSignalRequest(
                    size=Decimal("0.01"),
                    entry_price=Decimal("3700"),
                    stop_loss=Decimal("3785"),
                    take_profit=Decimal("3560"),
                ),
            )
            assert updated.status == SignalStatus.APPROVED_DRY_RUN
        finally:
            await service.bitget.close()

    asyncio.run(scenario())


def test_approval_revalidates_edited_price_geometry(tmp_path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        signal = parse_signal(
            "ETHUSDT SHORT\nEntry: 3680 - 3720\nSL: 3785\nTP1: 3560",
            source_name="test",
        )
        try:
            with pytest.raises(ValidationError, match="short stop loss"):
                await service.approve(
                    signal,
                    ApproveSignalRequest(
                        size=Decimal("0.01"),
                        entry_price=Decimal("3700"),
                        stop_loss=Decimal("3600"),
                        take_profit=Decimal("3560"),
                    ),
                )
        finally:
            await service.bitget.close()

    asyncio.run(scenario())


def test_auto_mode_stays_pending_when_demo_execution_is_disabled(tmp_path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        service.set_manual_review(False)
        signal = parse_signal(
            "SOLUSDT LONG\nEntry: 180\nSL: 170\nTP1: 200",
            source_name="test",
        )
        try:
            await service.ingest_signal(signal)
            stored = service.store.get(signal.id)
            assert stored is not None
            assert stored.status == SignalStatus.PENDING_REVIEW
            assert service.store.audit_for(signal.id)[-1]["event"] == "auto_execution_blocked"
        finally:
            await service.bitget.close()

    asyncio.run(scenario())


def test_auto_execution_settings_are_validated_and_persisted(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        saved = service.set_auto_execution_settings(
            AutoExecutionSettingsRequest(
                enabled=True,
                execution_method=ExecutionMethod.MARKET,
                sizing_mode=SizingMode.FIXED_USDT,
                fixed_usdt=Decimal("200"),
                position_percent=Decimal("5"),
                default_leverage=20,
            )
        )
        assert saved.enabled is True
        assert service.manual_review_enabled is False
        assert service.store.get_setting("auto_fixed_usdt") == "200"
        assert service.store.get_setting("auto_default_leverage") == "20"

        restored = CopierService(service.settings)
        try:
            assert restored.auto_execution_settings() == saved
        finally:
            asyncio.run(restored.bitget.close())
    finally:
        asyncio.run(service.bitget.close())


def test_auto_execution_position_percent_is_limited_to_ten(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        with pytest.raises(ValidationError):
            AutoExecutionSettingsRequest(
                enabled=True,
                sizing_mode=SizingMode.POSITION_PERCENT,
                fixed_usdt=Decimal("200"),
                position_percent=Decimal("10.1"),
            )
    finally:
        asyncio.run(service.bitget.close())


def test_symbol_leverage_overrides_are_persisted_and_take_priority(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        service.set_auto_execution_settings(
            AutoExecutionSettingsRequest(default_leverage=12)
        )
        saved = service.set_leverage_overrides(
            LeverageOverrides(
                items=[LeverageOverride(symbol="btc/usdt", leverage=25)]
            )
        )
        assert saved.items[0].symbol == "BTCUSDT"
        assert service.resolve_requested_leverage("btcusdt") == 25
        assert service.resolve_requested_leverage("ETHUSDT") == 12

        restored = CopierService(service.settings)
        try:
            assert restored.leverage_overrides() == saved
            assert restored.resolve_requested_leverage("BTCUSDT") == 25
        finally:
            asyncio.run(restored.bitget.close())
    finally:
        asyncio.run(service.bitget.close())


def test_fixed_usdt_is_committed_margin_and_leverage_expands_notional(tmp_path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        service.set_auto_execution_settings(
            AutoExecutionSettingsRequest(
                sizing_mode=SizingMode.FIXED_USDT,
                fixed_usdt=Decimal("5"),
                default_leverage=50,
            )
        )
        signal = parse_signal(
            "SOLUSDT LONG\nEntry: 180\nSL: 170\nTP1: 200",
            source_name="test",
        )
        try:
            size = await service._resolve_auto_order_size(signal, 50)
            assert size == Decimal("1.38888889")
            assert size * signal.reference_entry == Decimal("250.000000200")
        finally:
            await service.bitget.close()

    asyncio.run(scenario())


def test_position_percent_is_percent_of_equity_committed_as_margin(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        service.set_auto_execution_settings(
            AutoExecutionSettingsRequest(
                sizing_mode=SizingMode.POSITION_PERCENT,
                position_percent=Decimal("10"),
                default_leverage=50,
            )
        )
        signal = parse_signal(
            "SOLUSDT LONG\nEntry: 180\nSL: 170\nTP1: 200",
            source_name="test",
        )

        async def account_snapshot():
            return SimpleNamespace(account_equity_usdt=Decimal("50"))

        monkeypatch.setattr(service, "bitget_account_snapshot", account_snapshot)
        try:
            size = await service._resolve_auto_order_size(signal, 50)
            assert size == Decimal("1.38888889")
        finally:
            await service.bitget.close()

    asyncio.run(scenario())


def test_connection_overview_masks_credentials(tmp_path) -> None:
    async def scenario() -> None:
        settings = replace(
            load_settings(),
            database_path=tmp_path / "signals.sqlite3",
            seed_demo_data=False,
            bitget_api_key="public-key-1234",
            bitget_api_secret="never-return-this-secret",
            bitget_api_passphrase="never-return-this-passphrase",
        )
        service = CopierService(settings)
        try:
            overview = await service.connection_overview()
            payload = overview.model_dump_json()
            assert overview.bitget_api_key_hint == "publ…1234"
            assert "never-return-this-secret" not in payload
            assert "never-return-this-passphrase" not in payload
        finally:
            await service.bitget.close()

    asyncio.run(scenario())


def test_bitget_account_snapshot_maps_only_safe_fields(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        service.bitget_connected = True

        async def account_summary():
            return {"data": {"accountEquity": "12.5", "usdtEquity": "12.4", "assets": [{"coin": "USDT", "equity": "12.4", "usdValue": "12.5", "available": "10"}]}}

        async def account_settings():
            return {"data": {"uid": "must-not-leak", "accountMode": "unified", "accountLevel": "basic", "assetMode": "multi_assets", "holdMode": "one_way_mode"}}

        async def account_info():
            return {"data": {"userId": "must-not-leak", "ips": "127.0.0.1", "permType": "read_only", "permissions": ["uta_mgt"]}}

        async def current_positions():
            return {"data": {"list": []}}

        async def open_orders():
            return {"data": {"list": [{"orderId": "must-not-leak", "symbol": "BTCUSDT", "category": "USDT-FUTURES", "side": "buy", "orderType": "limit", "price": "50000", "qty": "0.01", "cumExecQty": "0", "orderStatus": "live"}]}}

        monkeypatch.setattr(service.bitget, "account_summary", account_summary)
        monkeypatch.setattr(service.bitget, "account_settings", account_settings)
        monkeypatch.setattr(service.bitget, "account_info", account_info)
        monkeypatch.setattr(service.bitget, "current_positions", current_positions)
        monkeypatch.setattr(service.bitget, "open_orders", open_orders)
        try:
            snapshot = await service.bitget_account_snapshot()
            payload = snapshot.model_dump_json()
            assert snapshot.account_equity_usd == Decimal("12.5")
            assert snapshot.assets[0].coin == "USDT"
            assert snapshot.open_orders[0].symbol == "BTCUSDT"
            assert "must-not-leak" not in payload
            assert "orderId" not in payload
        finally:
            await service.bitget.close()

    asyncio.run(scenario())
