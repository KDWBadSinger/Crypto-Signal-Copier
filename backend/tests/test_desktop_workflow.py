import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from telethon.errors import SessionPasswordNeededError

from app.bitget import BitgetDemoClient, BitgetError, BitgetOrderUncertain
from app.config import load_settings
from app.models import AutoExecutionSettingsRequest, ExecutionMethod, SignalStatus
from app.parser import parse_signal
from app.service import CopierService
from app.telegram_client import TelegramSignalClient

TEXT = "ETHUSDT SHORT\nEntry: 3700\nSL: 3785\nTP1: 3560"
CONTRACT = {"symbol": "ETHUSDT", "maxLever": "50", "minLever": "1", "sizeMultiplier": "0.01",
            "minTradeNum": "0.01", "minTradeUSDT": "5", "pricePlace": "2", "priceEndStep": "1"}


def settings(tmp_path, **kwargs):
    return replace(load_settings(), database_path=tmp_path / "test.sqlite3", seed_demo_data=False,
                   telegram_api_id=123, telegram_api_hash="dummy", telegram_phone="+15550000000",
                   telegram_allowed_chat_ids=frozenset({-100123}), bitget_enable_demo_orders=True, **kwargs)


def event(text=TEXT, chat_id=-100123, message_id=1, age=0):
    async def get_chat():
        return SimpleNamespace(title="Test channel")
    return SimpleNamespace(chat_id=chat_id, raw_text=text, get_chat=get_chat,
        message=SimpleNamespace(id=message_id, date=datetime.now(UTC) - timedelta(seconds=age)))


class FakeTelegram:
    def __init__(self):
        self.authorized = False
        self.password_needed = False
        self.handlers = []

    def is_connected(self): return True
    async def connect(self): pass
    async def disconnect(self): pass
    async def is_user_authorized(self): return self.authorized
    async def get_me(self): return SimpleNamespace(id=1)
    async def send_read_acknowledge(self, *args): pass
    async def send_code_request(self, phone): return SimpleNamespace(phone_code_hash="hash")
    async def sign_in(self, **kwargs):
        if self.password_needed and not kwargs.get("password"):
            raise SessionPasswordNeededError(None)
        self.authorized = True
    def add_event_handler(self, callback, event): self.handlers.append(callback)
    async def iter_dialogs(self):
        yield SimpleNamespace(id=-100123, name="Test channel", is_channel=True, is_group=False)
        yield SimpleNamespace(id=999, name="Private person", is_channel=False, is_group=False)


def test_telegram_login_two_factor_and_channel_listing(tmp_path):
    async def scenario():
        client = TelegramSignalClient(settings(tmp_path), None)
        client.client = FakeTelegram()
        client.client.password_needed = True
        assert await client.send_code() == {"state": "code_sent"}
        assert await client.sign_in("12345", None) == {"state": "password_required"}
        assert await client.sign_in(None, "dummy-password") == {"state": "authorized"}
        assert len(client.client.handlers) == 2
        assert await client.dialogs() == [{"id": -100123, "title": "Test channel", "selected": True}]
    asyncio.run(scenario())


@pytest.mark.parametrize("split", [False, True])
def test_selected_channel_to_signed_demo_order_end_to_end(tmp_path, split):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        service.bitget_connected = True
        service.set_auto_execution_settings(AutoExecutionSettingsRequest(enabled=True, fixed_usdt=20,
                                                                       default_leverage=10, execution_method="market"))
        requests = []
        def transport(request):
            requests.append(request)
            path = request.url.path
            data = {}
            if path.endswith("contracts"): data = [CONTRACT]
            elif path.endswith("ticker"): data = [{"markPrice": "3700"}]
            elif path.endswith("place-order"):
                assert request.headers["paptrading"] == "1"
                assert request.headers["ACCESS-SIGN"]
                import json
                payload = json.loads(request.content)
                assert payload["size"] == "0.13"  # floor(20 USDT * (50 * 50%) / 3700)
                assert payload["presetStopLossPrice"] == "3785"
                assert payload["orderType"] == "market"
                data = {"orderId": "demo-123", "clientOid": payload["clientOid"]}
            return httpx.Response(200, json={"code": "00000", "data": data})
        await service.bitget._http.aclose()
        service.bitget._http = httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="https://api.bitget.com")
        try:
            await service.telegram._handle_message(event(chat_id=-999))
            assert not service.store.list()
            if split:
                await service.telegram._handle_message(event(text="#ETH 市價空 進場3700", age=180))
                assert not requests
                protected = event(text="止盈：3560\n止损：3785", message_id=8)
                protected.message.reply_to_msg_id = 1
                await service.telegram._handle_message(protected)
                repeated = event(text="止盈：3560\n止损：3785", message_id=9)
                repeated.message.reply_to_msg_id = 1
                await service.telegram._handle_message(repeated)
            else:
                await service.telegram._handle_message(event())
            signal = service.store.latest()
            assert signal.status == SignalStatus.SUBMITTED
            assert signal.bitget_order_id == "demo-123"
            assert signal.execution_size == Decimal("0.13")
            await service.telegram._handle_message(event())
            await service.telegram._handle_message(event(text=TEXT + "\nupdated"))
            assert sum(r.url.path.endswith("place-order") for r in requests) == 1
            assert service.store.latest().status == SignalStatus.SUBMITTED
            assert len(service.store.messages()) == (3 if split else 1)
            await service.telegram._handle_message(event(text="hello", message_id=2))
            assert service.store.messages()[0]["status"] == "unparsed"
            await service.telegram._handle_message(event(message_id=3, age=300))
            assert service.store.get_message(-100123, 3)["status"] == "stale"
        finally:
            await service.bitget.close()
    asyncio.run(scenario())


def test_live_mode_never_changes_leverage_or_places_orders(tmp_path, monkeypatch):
    async def scenario():
        service = CopierService(settings(tmp_path, bitget_api_environment="live"))
        service.bitget_connected = True
        service.set_manual_review(False)
        async def forbidden(*args, **kwargs):
            pytest.fail("live write attempted")
        monkeypatch.setattr(service.bitget, "_request", forbidden)
        try:
            await service.ingest_signal(parse_signal(TEXT, source_name="test"))
            assert service.store.audit_for(service.store.latest().id)[-1]["event"] == "auto_execution_blocked"
            with pytest.raises(BitgetError):
                await service.bitget.set_cross_leverage("ETHUSDT", 10)
        finally:
            await service.bitget.close()
    asyncio.run(scenario())


def test_timeout_reconciles_by_client_oid_without_second_order(tmp_path):
    async def scenario():
        client = BitgetDemoClient(settings(tmp_path))
        posted = []
        def transport(request):
            if request.url.path.endswith("contracts"):
                return httpx.Response(200, json={"code": "00000", "data": [CONTRACT]})
            if request.method == "POST":
                posted.append(request)
                raise httpx.ReadTimeout("uncertain", request=request)
            assert "clientOid" in request.url.params
            return httpx.Response(200, json={"code": "00000", "data": {"orderId": "reconciled"}})
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="https://api.bitget.com")
        try:
            signal = parse_signal(TEXT, source_name="test")
            result = await client.place_signal_order(signal, size=Decimal("0.05"), entry_price=Decimal(3700),
                                                    stop_loss=Decimal(3785), take_profit=Decimal(3560))
            assert result.order_id == "reconciled"
            assert len(posted) == 1
        finally:
            await client.close()
    asyncio.run(scenario())


def test_windows_secrets_roundtrip(tmp_path):
    import os
    if os.name != "nt": pytest.skip("Windows DPAPI")
    from app.secrets import write_secrets, read_secrets
    path = tmp_path / "credentials.bin"
    write_secrets(path, {"key": "secret-do-not-leak"})
    write_secrets(path, {"phone": "+123"})
    assert b"secret-do-not-leak" not in path.read_bytes()
    assert read_secrets(path) == {"key": "secret-do-not-leak", "phone": "+123"}


def test_uncertain_order_is_preserved_and_not_replayed(tmp_path, monkeypatch):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.bitget_connected = True
        service.set_auto_execution_settings(AutoExecutionSettingsRequest(enabled=True, execution_method="limit"))
        calls = []
        async def leverage_limits(symbol): return (1, 50)
        async def set_leverage(*args): pass
        async def place_order(*args, **kwargs):
            calls.append(1)
            raise BitgetOrderUncertain("timeout")
        monkeypatch.setattr(service.bitget, "symbol_leverage_limits", leverage_limits)
        monkeypatch.setattr(service.bitget, "set_cross_leverage", set_leverage)
        monkeypatch.setattr(service.bitget, "place_signal_order", place_order)
        try:
            signal = parse_signal(TEXT, source_name="test", chat_id=-100123, message_id=42)
            await service.ingest_signal(signal)
            await service.ingest_signal(signal)
            assert service.store.latest().status == SignalStatus.UNKNOWN
            assert service.store.latest().client_oid
            assert len(calls) == 1
        finally:
            await service.bitget.close()
    asyncio.run(scenario())


def test_channel_selection_checks_membership_and_persists(tmp_path, monkeypatch):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        service.telegram.connected = True
        saved = {}
        monkeypatch.setattr("app.service.update_env_file", lambda updates: saved.update(updates))
        try:
            with pytest.raises(ValueError):
                await service.select_telegram_channels([-999])
            await service.select_telegram_channels([-100123])
            assert saved["TELEGRAM_ALLOWED_CHAT_IDS"] == "-100123"
            await service.select_telegram_channels([])
            assert not service.telegram.settings.telegram_allowed_chat_ids
        finally:
            await service.bitget.close()
    asyncio.run(scenario())


def test_classic_demo_account_assets_positions_and_orders(tmp_path, monkeypatch):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.bitget_connected = True
        async def accounts(): return {"data": [{"marginCoin": "USDT", "accountEquity": "123", "available": "100"}]}
        async def request(method, path, **kwargs):
            if path.endswith("all-position"):
                return {"data": [{"symbol": "ETHUSDT", "holdSide": "long", "total": "0.1", "openPriceAvg": "2500"}]}
            return {"data": {"entrustedList": [{"symbol": "BTCUSDT", "size": "0.01", "price": "60000", "status": "live"}]}}
        monkeypatch.setattr(service.bitget, "account_summary", accounts)
        monkeypatch.setattr(service.bitget, "_request", request)
        try:
            snapshot = await service.bitget_account_snapshot()
            assert snapshot.account_mode == "classic"
            assert snapshot.account_equity_usdt == Decimal(123)
            assert snapshot.positions[0].total == Decimal("0.1")
            assert snapshot.open_orders[0].symbol == "BTCUSDT"
        finally:
            await service.bitget.close()
    asyncio.run(scenario())
